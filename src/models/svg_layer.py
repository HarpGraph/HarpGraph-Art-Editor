from PyQt5.QtGui import QColor
from PyQt5.QtCore import Qt, QObject, QRunnable, QThreadPool, pyqtSignal
from PyQt5.QtWidgets import QGraphicsScene, QGraphicsItem
import logging
from .base_layer import BaseLayer
from .layer_types import LayerType
from .svg_utils import parse_svg_subpaths
from .gcode_pathops import subpaths_to_gcode_lines
from src.graphics.svg_item import SvgGraphicsItem

logger = logging.getLogger(__name__)


class _SvgParseSignals(QObject):
    finished = pyqtSignal(list, object)  # subpaths, bbox
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
            subpaths, bbox = parse_svg_subpaths(self.filename)
            self.signals.finished.emit(subpaths, bbox)
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
        self._raw_subpaths = []   # unscaled, origin-relative (native SVG units)
        self._load_generation = 0
        self._workers       = []    # keeps in-flight workers alive (avoids GC crash)

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def load_file(self, filename: str, width_mm: float = None, height_mm: float = None) -> bool:
        try:
            self.file_path = filename
            self._pending_size = (width_mm, height_mm)
            self._load_generation += 1
            generation = self._load_generation

            worker = _SvgParseWorker(filename)
            self._workers.append(worker)
            worker.signals.finished.connect(
                lambda sps, bbox, g=generation, w=worker: self._on_parse_finished(sps, bbox, g, w))
            worker.signals.error.connect(
                lambda msg, g=generation, w=worker: self._on_parse_error(msg, g, w))
            QThreadPool.globalInstance().start(worker)
            return True
        except Exception as e:
            logger.error(f'Error starting SVG load: {e}', exc_info=True)
            return False

    def _on_parse_finished(self, subpaths, bbox, generation, worker=None):
        if worker in self._workers:
            self._workers.remove(worker)
        if generation != self._load_generation:
            return
        if not subpaths or bbox is None:
            logger.warning('No drawable shapes found in SVG file')
            self.load_finished.emit(False)
            return

        min_x, min_y, max_x, max_y = bbox
        self._raw_subpaths = [[(x - min_x, y - min_y) for x, y in sp] for sp in subpaths]
        self._raw_size = (max(max_x - min_x, 1e-6), max(max_y - min_y, 1e-6))

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

    def export_gcode(self, filename: str, feed: float = 1000.0, travel_z: float = 5.0,
                      draw_z: float = 0.0, optimize: bool = True) -> bool:
        if not self.current_item:
            return False
        try:
            subpaths = self.current_item.subpaths_in_scene()
            lines = subpaths_to_gcode_lines(subpaths, feed=feed, travel_z=travel_z,
                                             draw_z=draw_z, optimize=optimize)
            with open(filename, 'w', encoding='utf-8') as f:
                f.write('\n'.join(lines) + '\n')
            return True
        except Exception as e:
            logger.error(f'Error exporting G-code from SVG layer: {e}', exc_info=True)
            return False
