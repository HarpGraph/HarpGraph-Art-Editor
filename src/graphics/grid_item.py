from PyQt5.QtWidgets import QGraphicsItem
from PyQt5.QtWidgets import QGraphicsItem
from PyQt5.QtGui import QPen, QPainter
from PyQt5.QtCore import Qt, QRectF, QPointF, QLineF
import logging
import math

logger = logging.getLogger(__name__)

class GridItem(QGraphicsItem):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.grid_step = 10.0  
        self.grid_color = Qt.gray  
        self.grid_opacity = 0.3   
        self.axes_color = Qt.red  
        self.show_coords = True   
        self.setZValue(-1000)     
        self.scale_factor = 1.0   # Добавляем коэффициент масштаба

    def set_scale(self, scale):
        self.scale_factor = scale
        self.update()
        logger.debug(f"Grid scale set to: {scale}")

    def set_grid_params(self, step=None, color=None, opacity=None):
        """Установка параметров сетки"""
        if step is not None:
            self.grid_step = float(step)
        if color is not None:
            self.grid_color = color
        if opacity is not None:
            self.grid_opacity = max(0.0, min(1.0, float(opacity)))
        self.update()

    def boundingRect(self):
        """Возвращаем большой прямоугольник для области отрисовки"""
        return QRectF(-100000, -100000, 200000, 200000)

    def paint(self, painter: QPainter, option, widget):
        try:
            view = self.scene().views()[0]
            rect = view.viewport().rect()
            scene_rect = view.mapToScene(rect).boundingRect()

            painter.save()

            # OPT-4: собираем все линии сетки в список и рисуем одним вызовом
            grid_pen = QPen(self.grid_color)
            grid_pen.setCosmetic(True)
            painter.setPen(grid_pen)
            painter.setOpacity(self.grid_opacity)

            left = scene_rect.left()
            right = scene_rect.right()
            top = scene_rect.top()
            bottom = scene_rect.bottom()
            step = self.grid_step

            lines = []
            # Вертикальные линии
            x = round(left / step) * step
            while x <= right:
                lines.append(QLineF(x, top, x, bottom))
                x += step
            # Горизонтальные линии
            y = round(top / step) * step
            while y <= bottom:
                lines.append(QLineF(left, y, right, y))
                y += step

            if lines:
                painter.drawLines(lines)  # OPT-4: один вызов вместо N

            # Оси координат
            axes_pen = QPen(Qt.red)
            axes_pen.setWidth(2)
            painter.setPen(axes_pen)
            painter.setOpacity(1.0)
            painter.drawLine(QPointF(left, 0), QPointF(right, 0))   # ось X
            painter.drawLine(QPointF(0, top), QPointF(0, bottom))   # ось Y

            painter.restore()

        except Exception as e:
            logger.error(f"Error painting grid: {e}", exc_info=True)
