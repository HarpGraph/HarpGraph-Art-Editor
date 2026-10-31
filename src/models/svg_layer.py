from PyQt5.QtGui import QColor
from PyQt5.QtCore import Qt, QObject, QRunnable, QThreadPool, pyqtSignal
from PyQt5.QtWidgets import QGraphicsScene, QGraphicsItem
import logging
from .base_layer import BaseLayer
from .layer_types import LayerType
from .svg_utils import parse_svg_subpaths, parse_svg_pens
from .gcode_pathops import subpaths_to_gcode_lines
from src.graphics.svg_item import SvgGraphicsItem

logger = logging.getLogger(__name__)


class _SvgParseSignals(QObject):
    finished = pyqtSignal(list, object, object)  # subpaths, bbox, fast_info|None
    error    = pyqtSignal(str)


class _SvgParseWorker(QRunnable):
    """Parse in a thread-pool worker -- SVG files with many curves can be slow."""

    def __init__(self, filename: str):
        super().__init__()
        self.filename = filename
        self.signals  = _SvgParseSignals()
        self.setAutoDelete(True)

    def run(self):
        try:
            # fast numpy path for simple line-only SVGs (Orchestrator export):
            # millimetres + per-pen colour/width, ~30x faster than svgelements
            fast = parse_svg_pens(self.filename)
            if fast is not None:
                self.signals.finished.emit(fast['subpaths'], fast['bbox'], fast)
                return
            subpaths, bbox = parse_svg_subpaths(self.filename)
            self.signals.finished.emit(subpaths, bbox, None)
        except Exception as exc:
            self.signals.error.emit(str(exc))


class SvgLayer(BaseLayer):

    load_finished = pyqtSignal(bool)

    def __init__(self, name: str, scene: QGraphicsScene):
        super().__init__(name, LayerType.SVG)
        self.scene        = scene
        self.file_path     = None
        self.line_color    = QColor(Qt.black)
        self.svg_size_mm   = (None, None)
        self.pens          = []    # [{'color': '#RRGGBB', 'width_mm': float}] (fast parse only)
        self._subpath_pens = []    # pen number per raw subpath (same order)
        self._raw_subpaths = []   # unscaled, origin-relative (native SVG units)
        self._sheet_frame  = True  # what the next load should use
        self.sheet_frame   = False # what the loaded layer actually uses (see _on_parse_finished)
        self._load_generation = 0
        self._workers       = []    # keeps in-flight workers alive (avoids GC crash)

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def load_file(self, filename: str, width_mm: float = None, height_mm: float = None,
                  sheet_frame: bool = True) -> bool:
        """`sheet_frame=False` -- старый режим (рамка = границы линий); нужен только для
        проектов, сохранённых до появления привязки к листу."""
        try:
            self.file_path = filename
            self._pending_size = (width_mm, height_mm)
            self._sheet_frame = sheet_frame
            self._load_generation += 1
            generation = self._load_generation

            worker = _SvgParseWorker(filename)
            self._workers.append(worker)
            worker.signals.finished.connect(
                lambda sps, bbox, fast, g=generation, w=worker: self._on_parse_finished(sps, bbox, fast, g, w))
            worker.signals.error.connect(
                lambda msg, g=generation, w=worker: self._on_parse_error(msg, g, w))
            QThreadPool.globalInstance().start(worker)
            return True
        except Exception as e:
            logger.error(f'Error starting SVG load: {e}', exc_info=True)
            return False

    def _on_parse_finished(self, subpaths, bbox, fast_info, generation, worker=None):
        if worker in self._workers:
            self._workers.remove(worker)
        if generation != self._load_generation:
            return
        if not subpaths or bbox is None:
            logger.warning('No drawable shapes found in SVG file')
            self.load_finished.emit(False)
            return

        # per-pen info is only available from the fast Orchestrator-format parse
        self.pens          = list(fast_info['pens']) if fast_info else []
        self._subpath_pens = list(fast_info['pen_index']) if fast_info else []

        if fast_info and self._sheet_frame:
            # Orchestrator SVG: the layer's frame is the SHEET (declared size), not the
            # strokes' bbox -- so per-pen files and the combined file of one sheet
            # land exactly on top of each other. Coordinates are already sheet-relative mm.
            self._raw_subpaths = subpaths
            self._raw_size = (max(fast_info['size_mm'][0], 1e-6), max(fast_info['size_mm'][1], 1e-6))
            self.sheet_frame = True
        else:
            min_x, min_y, max_x, max_y = bbox
            self._raw_subpaths = [[(x - min_x, y - min_y) for x, y in sp] for sp in subpaths]
            self._raw_size = (max(max_x - min_x, 1e-6), max(max_y - min_y, 1e-6))
            self.sheet_frame = False

        width_mm, height_mm = getattr(self, '_pending_size', (None, None))
        if not width_mm or not height_mm:
            width_mm, height_mm = self._raw_size

        if self.current_item:
            self.scene.removeItem(self.current_item)
            self.current_item = None
            self._items.clear()

        item = SvgGraphicsItem()
        item.layer_ref = self
        item.set_line_color(self.line_color)
        self.scene.addItem(item)
        self.current_item = item
        self._items.append(item)
        self.current_item.setVisible(self._visible)
        self.current_item.setOpacity(self._opacity)
        self.current_item.setFlag(QGraphicsItem.ItemIsMovable, not self._locked)

        self.set_size(width_mm, height_mm)
        # centre on the scene origin (work-field centre), same as ImageLayer/GCodeLayer
        self.current_item.setPos(-width_mm / 2.0, -height_mm / 2.0)
        logger.debug(f'SVG load done: {self.file_path}')
        self.load_finished.emit(True)

    def _on_parse_error(self, error_msg: str, generation: int, worker=None):
        if worker in self._workers:
            self._workers.remove(worker)
        if generation != self._load_generation:
            return
        logger.error(f'SVG parse error: {error_msg}')
        self.load_finished.emit(False)

    # ------------------------------------------------------------------
    # Sizing / export
    # ------------------------------------------------------------------

    def set_size(self, width_mm: float, height_mm: float):
        """Rescale from the original parsed geometry -- avoids compounding
        rounding error across repeated resizes. Preserves whatever position
        the item is currently at (including manual drags on the canvas)."""
        if not self._raw_subpaths:
            return
        self.svg_size_mm = (width_mm, height_mm)
        raw_w, raw_h = self._raw_size
        sx, sy = width_mm / raw_w, height_mm / raw_h
        scaled = [[(x * sx, y * sy) for x, y in sp] for sp in self._raw_subpaths]
        if self.current_item:
            pos = self.current_item.pos()
            self.current_item.set_svg_data(scaled)
            self.current_item.setPos(pos)

    def reset_position(self):
        """Вернуть слой на исходное место: по центру рабочего поля (как при загрузке)."""
        if self.current_item and self.svg_size_mm[0]:
            w, h = self.svg_size_mm
            self.current_item.setPos(-w / 2.0, -h / 2.0)

    def export_gcode(self, filename: str, feed: float = 1000.0, travel_z: float = 5.0,
                      draw_z: float = 0.0, optimize: bool = True, per_pen: bool = False) -> bool:
        if not self.current_item:
            return False
        try:
            subpaths = self.current_item.subpaths_in_scene()
            if per_pen and len(self.pens) >= 2 and len(self._subpath_pens) == len(subpaths):
                # one self-contained file per pen (= pencil), suffix _penN
                import os
                base, ext = os.path.splitext(filename)
                ext = ext or '.gcode'
                written = []
                for i in range(len(self.pens)):
                    pen_subpaths = [sp for sp, p in zip(subpaths, self._subpath_pens) if p == i]
                    if not pen_subpaths:
                        continue
                    lines = subpaths_to_gcode_lines(pen_subpaths, feed=feed, travel_z=travel_z,
                                                     draw_z=draw_z, optimize=optimize)
                    pen_file = f'{base}_pen{i + 1}{ext}'
                    with open(pen_file, 'w', encoding='utf-8') as f:
                        f.write('\n'.join(lines) + '\n')
                    written.append(pen_file)
                logger.info(f'Exported {len(written)} per-pen G-code files: {written}')
                return bool(written)

            lines = subpaths_to_gcode_lines(subpaths, feed=feed, travel_z=travel_z,
                                             draw_z=draw_z, optimize=optimize)
            with open(filename, 'w', encoding='utf-8') as f:
                f.write('\n'.join(lines) + '\n')
            return True
        except Exception as e:
            logger.error(f'Error exporting G-code from SVG layer: {e}', exc_info=True)
            return False
