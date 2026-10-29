from PyQt5.QtWidgets import QGraphicsItem, QGraphicsItemGroup, QGraphicsPathItem
from PyQt5.QtGui import QPainterPath, QPen, QColor
from PyQt5.QtCore import Qt, QRectF, QPointF
import logging

logger = logging.getLogger(__name__)


class SvgGraphicsItem(QGraphicsItemGroup):
    """Renders flattened SVG subpaths as one path; mirrors GCodeGraphicsItem's
    move/lock/colour behaviour so it fits the existing layer machinery."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.path_item = None
        self.subpaths = []  # list[list[(x, y)]] in local item coordinates
        self.line_color = QColor(Qt.black)
        self.layer_ref = None
        self.setFlag(QGraphicsItem.ItemIsMovable)
        self.setFlag(QGraphicsItem.ItemIsSelectable)
        self._bounding_rect = QRectF()

    def set_svg_data(self, subpaths):
        try:
            self.subpaths = subpaths
            path = QPainterPath()
            min_x = min_y = None
            max_x = max_y = None
            for sp in subpaths:
                if not sp:
                    continue
                path.moveTo(sp[0][0], sp[0][1])
                for x, y in sp:
                    path.lineTo(x, y)
                    min_x = x if min_x is None else min(min_x, x)
                    max_x = x if max_x is None else max(max_x, x)
                    min_y = y if min_y is None else min(min_y, y)
                    max_y = y if max_y is None else max(max_y, y)

            if self.path_item:
                self.removeFromGroup(self.path_item)
                if self.path_item.scene():
                    self.path_item.scene().removeItem(self.path_item)
            self.path_item = QGraphicsPathItem(path, self)
            pen = QPen(self.line_color, 0)
            pen.setCosmetic(True)
            self.path_item.setPen(pen)
            self.addToGroup(self.path_item)

            if min_x is not None:
                margin = 1.0
                self._bounding_rect = QRectF(
                    min_x - margin, min_y - margin,
                    (max_x - min_x) + 2 * margin, (max_y - min_y) + 2 * margin,
                )
            self.prepareGeometryChange()
        except Exception as e:
            logger.error(f'Error loading SVG data: {e}', exc_info=True)

    def boundingRect(self):
        if not self._bounding_rect.isNull():
            return self._bounding_rect
        return super().boundingRect()

    def set_line_color(self, color: QColor):
        self.line_color = QColor(color)
        if self.path_item:
            pen = self.path_item.pen()
            pen.setColor(self.line_color)
            self.path_item.setPen(pen)
        self.update()

    def set_locked(self, locked: bool):
        self.setFlag(QGraphicsItem.ItemIsMovable, not locked)

    def subpaths_in_scene(self):
        """Subpaths mapped through this item's current pos/transform, so
        export reflects any manual repositioning done in the editor."""
        result = []
        for sp in self.subpaths:
            result.append([(p.x(), p.y()) for p in
                            (self.mapToScene(QPointF(x, y)) for x, y in sp)])
        return result
