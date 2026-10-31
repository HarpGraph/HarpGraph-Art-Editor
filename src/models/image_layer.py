from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import QGraphicsItem, QGraphicsScene
import logging
from .base_layer import BaseLayer
from .layer_types import LayerType
from src.graphics.image_item import ImageGraphicsItem

logger = logging.getLogger(__name__)


class ImageLayer(BaseLayer):
    def __init__(self, name: str, scene: QGraphicsScene):
        super().__init__(name, LayerType.IMAGE)
        self.scene = scene
        self.file_path = None
        self.image_size_mm = (None, None)
        self._grid_locked = False

    def load_file(self, filename: str, width_mm: float, height_mm: float) -> bool:
        try:
            pixmap = QPixmap(filename)
            if pixmap.isNull():
                logger.error(f"Failed to load image: {filename}")
                return False

            # ������� ����������� ��������
            if self.current_item:
                self.scene.removeItem(self.current_item)
                self.current_item = None
                self._items.clear()

            item = ImageGraphicsItem(pixmap)
            item.set_size_mm(width_mm, height_mm)
            item.setVisible(self._visible)
            item.setOpacity(self._opacity)
            item.set_locked(self._locked)

            # ���������� ����������� ������������ ������� �����
            item.setPos(-width_mm / 2.0, -height_mm / 2.0)

            self.scene.addItem(item)
            self.current_item = item
            self._items.append(item)
            self.file_path = filename
            self.image_size_mm = (width_mm, height_mm)

            return True
        except Exception as e:
            logger.error(f"Error loading image file: {e}", exc_info=True)
            return False

    def set_size(self, width_mm: float, height_mm: float):
        self.image_size_mm = (width_mm, height_mm)
        if self.current_item:
            self.current_item.set_size_mm(width_mm, height_mm)

    def reset_position(self):
        """Вернуть слой на исходное место: по центру рабочего поля (как при загрузке)."""
        if self.current_item and self.image_size_mm[0]:
            w, h = self.image_size_mm
            self.current_item.setPos(-w / 2.0, -h / 2.0)

    def set_locked(self, locked: bool):
        self.locked = locked
        if self.current_item:
            self.current_item.set_locked(locked)
