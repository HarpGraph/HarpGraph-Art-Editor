from PyQt5.QtWidgets import QGraphicsPixmapItem
from PyQt5.QtGui import QPixmap, QTransform
from PyQt5.QtCore import Qt


class ImageGraphicsItem(QGraphicsPixmapItem):
    def __init__(self, pixmap: QPixmap, parent=None):
        super().__init__(pixmap, parent)
        self.original_pixmap = pixmap
        self.width_mm = float(pixmap.width()) if pixmap.width() else 0.0
        self.height_mm = float(pixmap.height()) if pixmap.height() else 0.0
        self.setTransformationMode(Qt.SmoothTransformation)
        self.setFlag(self.ItemIsMovable)

    def set_size_mm(self, width_mm: float, height_mm: float):
        # Clamp to positive values
        self.width_mm = max(1.0, float(width_mm))
        self.height_mm = max(1.0, float(height_mm))

        w_px = self.original_pixmap.width()
        h_px = self.original_pixmap.height()

        sx = self.width_mm / w_px if w_px else 1.0
        sy = self.height_mm / h_px if h_px else 1.0

        transform = QTransform()
        transform.scale(sx, sy)
        self.setTransform(transform, False)

    def set_locked(self, locked: bool):
        self.setFlag(self.ItemIsMovable, not locked)

    def get_size_mm(self):
        return self.width_mm, self.height_mm
