# -*- coding: utf-8 -*-
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QDoubleSpinBox, QCheckBox, QPushButton
)
from PyQt5.QtGui import QImage


class ImageSizeDialog(QDialog):
    def __init__(self, parent=None, filename: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Image size (mm)")
        self._updating = False
        self.image_size_px = self._load_image_size(filename)
        self.aspect = self.image_size_px[0] / self.image_size_px[1] if self.image_size_px[1] else 1.0
        self.setup_ui()

    def _load_image_size(self, filename: str):
        img = QImage(filename)
        if not img.isNull():
            return img.width(), img.height()
        return 0, 0

    def setup_ui(self):
        layout = QVBoxLayout(self)

        if any(self.image_size_px):
            info = QLabel(f"Original size: {self.image_size_px[0]} x {self.image_size_px[1]} px")
            layout.addWidget(info)

        size_layout = QHBoxLayout()
        self.width_spin = QDoubleSpinBox()
        self.width_spin.setSuffix(" mm")
        self.width_spin.setRange(1.0, 10000.0)
        self.width_spin.setDecimals(2)
        self.width_spin.setValue(100.0)

        self.height_spin = QDoubleSpinBox()
        self.height_spin.setSuffix(" mm")
        self.height_spin.setRange(1.0, 10000.0)
        self.height_spin.setDecimals(2)
        self.height_spin.setValue(100.0 / self.aspect if self.aspect else 100.0)

        self.lock_ratio_cb = QCheckBox("Lock aspect ratio")
        self.lock_ratio_cb.setChecked(True)

        self.width_spin.valueChanged.connect(self._on_width_changed)
        self.height_spin.valueChanged.connect(self._on_height_changed)

        size_layout.addWidget(QLabel("Width:"))
        size_layout.addWidget(self.width_spin)
        size_layout.addWidget(QLabel("Height:"))
        size_layout.addWidget(self.height_spin)
        layout.addLayout(size_layout)
        layout.addWidget(self.lock_ratio_cb)

        buttons_layout = QHBoxLayout()
        ok_btn = QPushButton("OK")
        cancel_btn = QPushButton("Cancel")
        ok_btn.clicked.connect(self.accept)
        cancel_btn.clicked.connect(self.reject)
        buttons_layout.addWidget(ok_btn)
        buttons_layout.addWidget(cancel_btn)
        layout.addLayout(buttons_layout)

    def _on_width_changed(self, value):
        if self._updating or not self.lock_ratio_cb.isChecked() or self.aspect == 0:
            return
        self._updating = True
        self.height_spin.setValue(value / self.aspect)
        self._updating = False

    def _on_height_changed(self, value):
        if self._updating or not self.lock_ratio_cb.isChecked() or self.aspect == 0:
            return
        self._updating = True
        self.width_spin.setValue(value * self.aspect)
        self._updating = False

    def get_size(self):
        return {
            'width_mm': self.width_spin.value(),
            'height_mm': self.height_spin.value()
        }
