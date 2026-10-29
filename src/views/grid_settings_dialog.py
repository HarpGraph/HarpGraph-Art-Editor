from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, 
                           QLabel, QSlider, QPushButton, QColorDialog)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor
import logging

logger = logging.getLogger(__name__)

class GridSettingsDialog(QDialog):
    def __init__(self, parent=None, opacity=0.3, color=Qt.gray):
        super().__init__(parent)
        self.setWindowTitle("Настройки сетки")
        self.opacity = opacity
        self.color = color
        self.setup_ui()
        
    def setup_ui(self):
        layout = QVBoxLayout(self)
        
        # Настройка прозрачности
        opacity_layout = QHBoxLayout()
        opacity_label = QLabel("Прозрачность:")
        self.opacity_slider = QSlider(Qt.Horizontal)
        self.opacity_slider.setRange(0, 100)
        self.opacity_slider.setValue(int(self.opacity * 100))
        self.opacity_value = QLabel(f"{self.opacity:.2f}")
        self.opacity_slider.valueChanged.connect(self.on_opacity_changed)
        
        opacity_layout.addWidget(opacity_label)
        opacity_layout.addWidget(self.opacity_slider)
        opacity_layout.addWidget(self.opacity_value)
        
        # Кнопка выбора цвета
        color_layout = QHBoxLayout()
        color_label = QLabel("Цвет сетки:")
        self.color_button = QPushButton()
        self.color_button.setFixedSize(50, 25)
        self.update_color_button()
        self.color_button.clicked.connect(self.on_color_click)
        
        color_layout.addWidget(color_label)
        color_layout.addWidget(self.color_button)
        
        # Кнопки ОК и Отмена
        buttons_layout = QHBoxLayout()
        ok_button = QPushButton("OK")
        cancel_button = QPushButton("Отмена")
        ok_button.clicked.connect(self.accept)
        cancel_button.clicked.connect(self.reject)
        
        buttons_layout.addWidget(ok_button)
        buttons_layout.addWidget(cancel_button)
        
        # Добавляем все в основной layout
        layout.addLayout(opacity_layout)
        layout.addLayout(color_layout)
        layout.addLayout(buttons_layout)
        
    def on_opacity_changed(self, value):
        self.opacity = value / 100.0
        self.opacity_value.setText(f"{self.opacity:.2f}")
        
    def on_color_click(self):
        color = QColorDialog.getColor(self.color, self)
        if color.isValid():
            self.color = color
            self.update_color_button()
            
    def update_color_button(self):
        self.color_button.setStyleSheet(
            f"background-color: {self.color.name()};"
            f"border: 1px solid black;"
        )
        
    def get_settings(self):
        return {
            'opacity': self.opacity,
            'color': self.color
        }