from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, 
                           QLineEdit, QComboBox, QPushButton)
from PyQt5.QtCore import Qt
from src.models.layer_types import LayerType

class NewLayerDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Создание нового слоя")
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)

        # Имя слоя
        name_layout = QHBoxLayout()
        name_label = QLabel("Имя слоя:")
        self.name_edit = QLineEdit()
        name_layout.addWidget(name_label)
        name_layout.addWidget(self.name_edit)
        layout.addLayout(name_layout)

        # Тип слоя
        type_layout = QHBoxLayout()
        type_label = QLabel("Тип слоя:")
        self.type_combo = QComboBox()
        for layer_type in LayerType:
            self.type_combo.addItem(str(layer_type))
        type_layout.addWidget(type_label)
        type_layout.addWidget(self.type_combo)
        layout.addLayout(type_layout)

        # Кнопки
        buttons_layout = QHBoxLayout()
        ok_button = QPushButton("OK")
        cancel_button = QPushButton("Отмена")
        ok_button.clicked.connect(self.accept)
        cancel_button.clicked.connect(self.reject)
        buttons_layout.addWidget(ok_button)
        buttons_layout.addWidget(cancel_button)
        layout.addLayout(buttons_layout)

    def get_layer_info(self):
        return {
            'name': self.name_edit.text(),
            'type': LayerType[self.type_combo.currentText()]
        }