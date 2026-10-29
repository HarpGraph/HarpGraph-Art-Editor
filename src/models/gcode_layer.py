from src.models.base_layer import BaseLayer
from src.models.layer_types import LayerType
from src.graphics.gcode_item import GCodeGraphicsItem
from PyQt5.QtWidgets import QGraphicsScene, QGraphicsItem
from PyQt5.QtCore import Qt, QObject, QRunnable, pyqtSignal, QThreadPool
from PyQt5.QtGui import QColor
import numpy as np
import re
import math
import logging

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# OPT-3: worker signals (must be a QObject to carry pyqtSignal)
# ---------------------------------------------------------------------------

class _ParseSignals(QObject):
    finished = pyqtSignal(object, object, object)   # pts (N,2) np, is_travel np, linenos np
    error    = pyqtSignal(str)


class _GCodeParseWorker(QRunnable):
    """OPT-3: parse file in a thread-pool worker -- no Qt objects touched."""

    def __init__(self, filename: str, travel_z: float = 5.0):
        super().__init__()
        self.filename = filename
        self.travel_z = travel_z
        self.signals  = _ParseSignals()
        self.setAutoDelete(True)

    def run(self):
        try:
            pts, is_travel, linenos = _parse_gcode_pure(self.filename, self.travel_z)
            self.signals.finished.emit(pts, is_travel, linenos)
        except Exception as exc:
            self.signals.error.emit(str(exc))


_GWORD_RE = re.compile(r'G0*([0-9]+)')


def _strip_comment(line: str) -> str:
    """Remove ';' end-of-line and '(...)' inline G-code comments."""
    if ';' in line:
        line = line[:line.index(';')]
    while '(' in line:
        start = line.index('(')
        end = line.find(')', start)
        if end == -1:
            line = line[:start]
            break
        line = line[:start] + line[end + 1:]
    return line


def _arc_points(x0, y0, x1, y1, i_off, j_off, clockwise, max_seg=2.0):
    """Interpolate a G2/G3 arc (I/J relative center) into short line segments."""
    cx, cy = x0 + i_off, y0 + j_off
    r = math.hypot(i_off, j_off)
    if r < 1e-6:
        return [(x1, y1)]
    a0 = math.atan2(y0 - cy, x0 - cx)
    a1 = math.atan2(y1 - cy, x1 - cx)
    if clockwise:
        while a1 >= a0:
            a1 -= 2 * math.pi
    else:
        while a1 <= a0:
            a1 += 2 * math.pi
    sweep = abs(a1 - a0)
    steps = max(1, int(math.ceil((sweep * max(r, 1e-6)) / max_seg)))
    pts = []
    for s in range(1, steps + 1):
        a = a0 + (a1 - a0) * (s / steps)
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    pts[-1] = (x1, y1)   # snap endpoint exactly, avoid float drift
    return pts


def _parse_gcode_pure(filename: str, travel_z: float = 5.0):
    """OPT-3 + OPT-5: pure function, no Qt -- safe to call from any thread.

    Tracks modal X/Y/Z/G90-G91/G20-G21 state instead of requiring every
    axis word to be repeated on every line (real G-code usually omits
    unchanged coordinates). G2/G3 arcs (I/J form) are interpolated into
    line segments instead of being drawn as straight chords.

    A vertex is flagged as "travel" when the last-seen Z is at or above
    `travel_z` -- the same convention already used and verified in
    Gcode_Terminal (tool-lift axis raised = rapid, no mark left).

    Returns (pts, is_travel, linenos) -- three parallel numpy arrays
    (OPT-10: no per-vertex Python objects, a 5M-line file stays compact).
    linenos is the 1-based source file line number each vertex came from
    (lets the trim tool map a point index back to an exact file line).
    """
    points, is_travel, linenos = [], [], []
    cur_x = cur_y = cur_z = 0.0
    modal_g = None
    relative = False
    units_scale = 1.0

    with open(filename, 'r', encoding='utf-8', errors='ignore') as fh:
        for lineno, raw_line in enumerate(fh, start=1):
            line = _strip_comment(raw_line).strip().upper()
            if not line:
                continue

            g_code = None
            x = y = i_off = j_off = None
            has_x = has_y = False

            for tok in line.split():
                letter, rest = tok[0], tok[1:]
                if letter == 'G':
                    m = _GWORD_RE.match(tok)
                    if m:
                        g_code = int(m.group(1))
                elif letter == 'X':
                    try:
                        x = float(rest) * units_scale
                        has_x = True
                    except ValueError:
                        pass
                elif letter == 'Y':
                    try:
                        y = float(rest) * units_scale
                        has_y = True
                    except ValueError:
                        pass
                elif letter == 'Z':
                    try:
                        z_val = float(rest) * units_scale
                        cur_z = (cur_z + z_val) if relative else z_val
                    except ValueError:
                        pass
                elif letter == 'I':
                    try:
                        i_off = float(rest) * units_scale
                    except ValueError:
                        pass
                elif letter == 'J':
                    try:
                        j_off = float(rest) * units_scale
                    except ValueError:
                        pass

            if g_code == 20:
                units_scale = 25.4
                continue
            if g_code == 21:
                units_scale = 1.0
                continue
            if g_code == 90:
                relative = False
                continue
            if g_code == 91:
                relative = True
                continue
            if g_code == 92:
                # set current position without moving -- resync modal state
                if has_x:
                    cur_x = x
                if has_y:
                    cur_y = y
                continue
            if g_code in (0, 1, 2, 3):
                modal_g = g_code
            elif g_code is not None:
                continue  # other non-motion G-code (G4 dwell, G28, ...)

            if modal_g is None or not (has_x or has_y):
                continue

            new_x = (cur_x + x) if (relative and has_x) else (x if has_x else cur_x)
            new_y = (cur_y + y) if (relative and has_y) else (y if has_y else cur_y)
            travel = cur_z >= travel_z

            if modal_g in (2, 3) and i_off is not None and j_off is not None:
                for px, py in _arc_points(cur_x, cur_y, new_x, new_y, i_off, j_off,
                                           clockwise=(modal_g == 2)):
                    points.append((px, py))
                    is_travel.append(travel)
                    linenos.append(lineno)
            else:
                points.append((new_x, new_y))
                is_travel.append(travel)
                linenos.append(lineno)

            cur_x, cur_y = new_x, new_y

    # OPT-5/OPT-10: vectorised numpy output
    pts = np.array(points, dtype=np.float32).reshape(-1, 2)
    trav = np.array(is_travel, dtype=bool)
    lns = np.array(linenos, dtype=np.int32)
    return pts, trav, lns


# ---------------------------------------------------------------------------
# GCodeLayer
# ---------------------------------------------------------------------------

class GCodeLayer(BaseLayer):

    # OPT-3: signal emitted in the GUI thread when async load completes
    load_finished = pyqtSignal(bool)

    def __init__(self, name: str, scene: QGraphicsScene):
        super().__init__(name, LayerType.GCODE)
        self.scene       = scene
        self._grid_locked   = False
        self.file_path      = None
        self.line_color     = QColor(Qt.black)
        self.point_color    = QColor(Qt.red)
        self.travel_color   = QColor(170, 102, 204)  # same as Gcode_Terminal col_travel
        self.travel_z       = 5.0    # Z >= this is treated as a pen-up/rapid move
        self.show_travel    = True
        self.scale_x        = 1.0    # масштаб/искажение относительно рабочего поля
        self.scale_y        = 1.0
        self._load_generation = 0   # fixes double-load race (stale results ignored)
        self._workers       = []    # keeps in-flight workers alive (avoids GC crash)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_file(self, filename: str) -> bool:
        """OPT-3: kick off async parse; returns True immediately.
        Connect load_finished(bool) to react when done.
        """
        try:
            # clear previous item right away so UI stays consistent
            if self.current_item:
                self.scene.removeItem(self.current_item)
                self.current_item = None
                self._items.clear()

            self.file_path = filename
            self._load_generation += 1
            generation = self._load_generation   # capture for stale-result check

            worker = _GCodeParseWorker(filename, self.travel_z)
            self._workers.append(worker)
            worker.signals.finished.connect(
                lambda pts, trav, lns, g=generation, w=worker: self._on_parse_finished(pts, trav, lns, g, w))
            worker.signals.error.connect(
                lambda msg, g=generation, w=worker: self._on_parse_error(msg, g, w))
            QThreadPool.globalInstance().start(worker)
            return True

        except Exception as e:
            logger.error(f'Error starting file load: {e}', exc_info=True)
            return False

    # ------------------------------------------------------------------
    # Line-range export: cut a slice of the source file and save it apart
    # ------------------------------------------------------------------

    def line_count(self) -> int:
        if not self.file_path:
            return 0
        try:
            with open(self.file_path, 'r', encoding='utf-8', errors='ignore') as f:
                return sum(1 for _ in f)
        except Exception as e:
            logger.error(f'Error counting lines: {e}', exc_info=True)
            return 0

    def save_line_range(self, out_filename: str, start_line: int, end_line: int) -> bool:
        """Save file lines [start_line, end_line] (1-based, inclusive) as a
        standalone G-code file -- lets the user carve out a portion to run
        separately without re-parsing/re-optimising anything."""
        if not self.file_path:
            return False
        try:
            with open(self.file_path, 'r', encoding='utf-8', errors='ignore') as f:
                lines = f.readlines()
            start_idx = max(0, start_line - 1)
            end_idx = min(len(lines), end_line)
            if start_idx >= end_idx:
                logger.warning('Invalid line range for G-code export')
                return False
            with open(out_filename, 'w', encoding='utf-8') as f:
                f.writelines(lines[start_idx:end_idx])
            return True
        except Exception as e:
            logger.error(f'Error saving G-code line range: {e}', exc_info=True)
            return False

    def save_point_range(self, out_filename: str, start_idx: int, end_idx: int) -> bool:
        """Same as save_line_range, but addressed by vertex index (as used by
        the visual trim sliders) instead of raw file line number."""
        if not self.current_item or self.current_item.point_count() == 0:
            return False
        linenos = self.current_item.linenos
        start_idx = max(0, min(start_idx, len(linenos) - 1))
        end_idx = max(start_idx, min(end_idx, len(linenos) - 1))
        return self.save_line_range(out_filename, int(linenos[start_idx]), int(linenos[end_idx]))

    # ------------------------------------------------------------------
    # Backward-compat: synchronous parse (used by tests / subclasses)
    # ------------------------------------------------------------------

    def parse_gcode(self, filename: str):
        """Synchronous parse -- returns (pts, is_travel, linenos) numpy arrays."""
        try:
            return _parse_gcode_pure(filename, self.travel_z)
        except Exception as e:
            logger.error(f'Error parsing G-code: {e}')
            return (np.empty((0, 2), dtype=np.float32),
                    np.empty(0, dtype=bool), np.empty(0, dtype=np.int32))

    # ------------------------------------------------------------------
    # OPT-3: async callbacks (run in GUI thread via Qt signal/slot)
    # ------------------------------------------------------------------

    def _on_parse_finished(self, points, is_travel, linenos, generation, worker=None):
        """Called in the GUI thread after background parse completes."""
        if worker in self._workers:
            self._workers.remove(worker)
        if generation != self._load_generation:
            logger.debug('Discarding stale G-code parse result (superseded by newer load)')
            return

        if points is None or len(points) == 0:
            logger.warning('No points found in G-code file')
            self.load_finished.emit(False)
            return

        self.current_item = GCodeGraphicsItem()
        self.current_item.set_gcode_data(points, is_travel, linenos)
        self.current_item.set_line_color(self.line_color)
        self.current_item.set_point_color(self.point_color)
        self.current_item.set_travel_color(self.travel_color)
        self.current_item.set_travel_visible(self.show_travel)
        self.current_item.layer_ref = self
        self.scene.addItem(self.current_item)
        self._items.append(self.current_item)
        self.current_item.setVisible(self._visible)
        self.current_item.setOpacity(self._opacity)
        self.current_item.setFlag(QGraphicsItem.ItemIsMovable, not self._locked)
        # восстановить масштаб слоя (вид/искажение) после перезагрузки файла
        if self.scale_x != 1.0 or self.scale_y != 1.0:
            from PyQt5.QtGui import QTransform
            self.current_item.setTransform(QTransform().scale(self.scale_x, self.scale_y))

        logger.debug(f'Async load done: {self.file_path}')
        self.load_finished.emit(True)

    def _on_parse_error(self, error_msg: str, generation: int, worker=None):
        """Called in the GUI thread when background parse fails."""
        if worker in self._workers:
            self._workers.remove(worker)
        if generation != self._load_generation:
            return
        logger.error(f'Parse error: {error_msg}')
        self.load_finished.emit(False)
