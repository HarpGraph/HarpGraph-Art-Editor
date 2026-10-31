# -*- coding: utf-8 -*-
"""Рамка вокруг слоя: 8 маркеров по краям и углам. Тянешь маркер -- рисунок растягивается
или сжимается (за боковой маркер -- по одной оси, за угловой -- по обеим, Shift --
с сохранением пропорций). Противоположная сторона/угол остаётся на месте."""
from PyQt5.QtWidgets import QGraphicsRectItem, QGraphicsItem
from PyQt5.QtGui import QPen, QBrush, QColor, QTransform
from PyQt5.QtCore import Qt, QRectF, QPointF

HANDLE_PX = 9          # размер маркера на экране
MIN_SIZE_MM = 0.5      # меньше этого рисунок сжать нельзя

# (hx, hy): -1 слева/сверху, 0 середина, +1 справа/снизу
_HANDLES = [(-1, -1), (0, -1), (1, -1), (1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0)]
_CURSORS = {
    (-1, -1): Qt.SizeFDiagCursor, (1, 1): Qt.SizeFDiagCursor,
    (1, -1): Qt.SizeBDiagCursor, (-1, 1): Qt.SizeBDiagCursor,
    (0, -1): Qt.SizeVerCursor, (0, 1): Qt.SizeVerCursor,
    (-1, 0): Qt.SizeHorCursor, (1, 0): Qt.SizeHorCursor,
}


class _Handle(QGraphicsRectItem):
    def __init__(self, frame, spec):
        h = HANDLE_PX / 2.0
        super().__init__(-h, -h, HANDLE_PX, HANDLE_PX, frame)
        self.frame, self.spec = frame, spec
        self.setFlag(QGraphicsItem.ItemIgnoresTransformations, True)   # размер в пикселях экрана
        self.setBrush(QBrush(QColor(255, 255, 255)))
        pen = QPen(QColor(0, 90, 220), 1.5)
        pen.setCosmetic(True)
        self.setPen(pen)
        self.setCursor(_CURSORS[spec])
        self.setAcceptedMouseButtons(Qt.LeftButton)
        self.setZValue(1)

    def mousePressEvent(self, event):
        event.accept()
        self.frame.begin_drag(self.spec)

    def mouseMoveEvent(self, event):
        self.frame.drag_to(event.scenePos(), bool(event.modifiers() & Qt.ShiftModifier))

    def mouseReleaseEvent(self, event):
        self.frame.end_drag()


class TransformFrame(QGraphicsRectItem):
    def __init__(self, main_window, layer):
        super().__init__()
        self.mw = main_window
        self.layer = layer
        pen = QPen(QColor(0, 90, 220), 1)
        pen.setCosmetic(True)
        pen.setStyle(Qt.DashLine)
        self.setPen(pen)
        self.setZValue(9000)
        self.setAcceptedMouseButtons(Qt.NoButton)   # клики ловят только маркеры
        self._handles = {spec: _Handle(self, spec) for spec in _HANDLES}
        self._drag = None
        self._local_box = None
        self.refresh()

    # --- геометрия ---

    def _compute_local_box(self):
        pts = self.layer.current_item.pts
        lo, hi = pts.min(axis=0), pts.max(axis=0)
        self._local_box = QRectF(float(lo[0]), float(lo[1]), float(hi[0] - lo[0]), float(hi[1] - lo[1]))

    def refresh(self):
        """Перечитать рамку по слою (после правок/отмены/перемещения)."""
        item = getattr(self.layer, 'current_item', None)
        if item is None or item.point_count() == 0:
            self.setVisible(False)
            return
        self._compute_local_box()
        self._place()
        self.setVisible(not self.layer.locked)

    def _scene_box(self):
        return self.layer.current_item.sceneTransform().mapRect(self._local_box)

    def _place(self):
        r = self._scene_box()
        self.setRect(r)
        for (hx, hy), h in self._handles.items():
            x = r.left() if hx < 0 else (r.right() if hx > 0 else r.center().x())
            y = r.top() if hy < 0 else (r.bottom() if hy > 0 else r.center().y())
            h.setPos(x, y)

    # --- перетаскивание ---

    def begin_drag(self, spec):
        item = self.layer.current_item
        tr = item.transform()
        self.mw._frame_dragging = True   # главное окно не должно записать это как перемещение мышью
        self._drag = {
            'spec': spec, 'rect': self._scene_box(), 'pos': QPointF(item.pos()),
            'sx': tr.m11(), 'sy': tr.m22(),
            'before': self.mw.geometry_state(self.layer),
        }

    def drag_to(self, scene_pos, keep_ratio):
        d = self._drag
        if d is None:
            return
        hx, hy = d['spec']
        r = d['rect']
        w0, h0 = max(r.width(), 1e-9), max(r.height(), 1e-9)
        rx = ry = 1.0
        if hx:   # якорь -- противоположная сторона
            ax = r.right() if hx < 0 else r.left()
            rx = max(hx * (scene_pos.x() - ax), MIN_SIZE_MM) / w0
        if hy:
            ay = r.bottom() if hy < 0 else r.top()
            ry = max(hy * (scene_pos.y() - ay), MIN_SIZE_MM) / h0
        if keep_ratio and hx and hy:
            rx = ry = max(rx, ry)
        elif keep_ratio and (hx or hy):
            rx = ry = rx if hx else ry
        sx, sy = d['sx'] * rx, d['sy'] * ry
        pos = QPointF(d['pos'])
        # якорь остаётся на месте: scene = pos + S * local
        if hx or keep_ratio:
            ax = (r.right() if hx < 0 else r.left()) if hx else r.center().x()
            pos.setX(ax - rx * (ax - d['pos'].x()))
        if hy or keep_ratio:
            ay = (r.bottom() if hy < 0 else r.top()) if hy else r.center().y()
            pos.setY(ay - ry * (ay - d['pos'].y()))
        item = self.layer.current_item
        item.setTransform(QTransform().scale(sx, sy))
        item.setPos(pos)
        self.layer.scale_x, self.layer.scale_y = sx, sy
        self._place()

    def end_drag(self):
        d, self._drag = self._drag, None
        if d is not None:
            self.mw.finish_geometry_drag(self.layer, d['before'])
