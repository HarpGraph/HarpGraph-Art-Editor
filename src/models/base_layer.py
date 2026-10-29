from PyQt5.QtWidgets import QGraphicsItem
from PyQt5.QtCore import QObject, pyqtSignal
from .layer_types import LayerType
import logging

logger = logging.getLogger(__name__)

class BaseLayer(QObject):
    """Базовый класс слоя с основными сигналами и свойствами"""
    
    # Сигналы для оповещения об изменениях
    name_changed = pyqtSignal(str)
    visibility_changed = pyqtSignal(bool)
    opacity_changed = pyqtSignal(float)
    locked_changed = pyqtSignal(bool)
    
    def __init__(self, name: str, layer_type: LayerType):
        super().__init__()
        self._name = name
        self._type = layer_type
        self._visible = True
        self._opacity = 1.0
        self._locked = False  # Блокировка слоя
        self._grid_locked = False  # Привязка к сетке (по умолчанию отключена)
        self._items = []  # Изменяем items на _items
        self.current_item = None  # Добавляем ссылку на текущий элемент
        logger.debug(f"Created layer: {name} of type {layer_type}")

    @property
    def name(self) -> str:
        return self._name

    @name.setter
    def name(self, value: str):
        if value != self._name:
            self._name = value
            self.name_changed.emit(value)

    @property
    def type(self) -> LayerType:
        return self._type

    @property
    def visible(self) -> bool:
        return self._visible

    @visible.setter
    def visible(self, value: bool):
        if value != self._visible:
            self._visible = value
            # Обновляем видимость для всех элементов слоя
            for item in self._items:
                item.setVisible(value)
            if self.current_item:
                self.current_item.setVisible(value)
            self.visibility_changed.emit(value)
            logger.debug(f"Layer {self.name} visibility changed to: {value}")

    @property
    def opacity(self) -> float:
        return self._opacity

    @opacity.setter
    def opacity(self, value: float):
        value = max(0.0, min(1.0, value))
        if value != self._opacity:
            self._opacity = value
            # Обновляем прозрачность для всех элементов слоя
            for item in self._items:
                item.setOpacity(value)
            if self.current_item:
                self.current_item.setOpacity(value)
            self.opacity_changed.emit(value)
            logger.debug(f"Layer {self.name} opacity changed to: {value}")

    @property
    def locked(self) -> bool:
        return self._locked

    @locked.setter
    def locked(self, value: bool):
        if value != self._locked:
            self._locked = value
            # Обновляем состояние блокировки для всех элементов
            for item in self._items:
                if hasattr(item, 'setFlag'):
                    item.setFlag(QGraphicsItem.ItemIsMovable, not value)
            if self.current_item:
                self.current_item.setFlag(QGraphicsItem.ItemIsMovable, not value)
            self.locked_changed.emit(value)
            logger.debug(f"Layer {self.name} lock state changed to: {value}")