from enum import Enum, auto

class LayerType(Enum):
    """Типы поддерживаемых слоев"""
    SVG = auto()
    GCODE = auto()
    IMAGE = auto()

    def __str__(self):
        return self.name