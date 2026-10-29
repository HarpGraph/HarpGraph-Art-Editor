# -*- coding: utf-8 -*-
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QDoubleSpinBox, QCheckBox, QPushButton
)


class SvgSizeDialog(QDialog):
    """Same lock-aspect width/height picker as ImageSizeDialog, but driven
    by an externally-supplied aspect ratio (SVG bbox) instead of QImage."""

    def __init__(self, parent=None, native_size=(100.0, 100.0)):
        super().__init__(parent)
        self.setWindowTitle("Размер SVG (мм)")
        self._updating = False
        self.native_size = native_size
        self.aspect = native_size[0] / native_size[1] if native_size[1] else 1.0
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)

        info = QLabel(f"Исходный размер: {self.native_size[0]:.1f} x {self.native_size[1]:.1f} ед.")
        layout.addWidget(info)

        size_layout = QHBoxLayout()
        self.width_spin = QDoubleSpinBox()
        self.width_spin.setSuffix(" мм")
        self.width_spin.setRange(1.0, 10000.0)
        self.width_spin.setDecimals(2)
        self.width_spin.setValue(100.0)

        self.height_spin = QDoubleSpinBox()
        self.height_spin.setSuffix(" мм")
        self.height_spin.setRange(1.0, 10000.0)
        self.height_spin.setDecimals(2)
        self.height_spin.setValue(100.0 / self.aspect if self.aspect else 100.0)

        self.lock_ratio_cb = QCheckBox("Сохранять пропорции")
        self.lock_ratio_cb.setChecked(True)

        self.width_spin.valueChanged.connect(self._on_width_changed)
        self.height_spin.valueChanged.connect(self._on_height_changed)

        size_layout.addWidget(QLabel("Ширина:"))
        size_layout.addWidget(self.width_spin)
        size_layout.addWidget(QLabel("Высота:"))
        size_layout.addWidget(self.height_spin)
        layout.addLayout(size_layout)
        layout.addWidget(self.lock_ratio_cb)

        buttons_layout = QHBoxLayout()
        ok_btn = QPushButton("OK")
        cancel_btn = QPushButton("Отмена")
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
