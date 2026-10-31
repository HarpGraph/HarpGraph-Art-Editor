# -*- coding: utf-8 -*-
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QDoubleSpinBox, QCheckBox, QPushButton
)


class SvgExportDialog(QDialog):
    """Options for converting an SVG layer's flattened paths into G-code."""

    def __init__(self, parent=None, pens=None):
        super().__init__(parent)
        self.setWindowTitle("Экспорт SVG в G-code")
        self.pens = pens or []
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)

        feed_layout = QHBoxLayout()
        feed_layout.addWidget(QLabel("Подача (мм/мин):"))
        self.feed_spin = QDoubleSpinBox()
        self.feed_spin.setRange(1.0, 20000.0)
        self.feed_spin.setDecimals(1)
        self.feed_spin.setValue(1000.0)
        feed_layout.addWidget(self.feed_spin)
        layout.addLayout(feed_layout)

        travel_layout = QHBoxLayout()
        travel_layout.addWidget(QLabel("Z холостого хода (подъём):"))
        self.travel_z_spin = QDoubleSpinBox()
        self.travel_z_spin.setRange(0.0, 500.0)
        self.travel_z_spin.setDecimals(2)
        self.travel_z_spin.setValue(5.0)
        travel_layout.addWidget(self.travel_z_spin)
        layout.addLayout(travel_layout)

        draw_layout = QHBoxLayout()
        draw_layout.addWidget(QLabel("Z рисования (опущено):"))
        self.draw_z_spin = QDoubleSpinBox()
        self.draw_z_spin.setRange(-500.0, 500.0)
        self.draw_z_spin.setDecimals(2)
        self.draw_z_spin.setValue(0.0)
        draw_layout.addWidget(self.draw_z_spin)
        layout.addLayout(draw_layout)

        self.optimize_cb = QCheckBox("Оптимизировать порядок обхода (сокращать холостые хода)")
        self.optimize_cb.setChecked(True)
        layout.addWidget(self.optimize_cb)

        self.per_pen_cb = None
        if len(self.pens) >= 2:
            self.per_pen_cb = QCheckBox(
                f"Отдельный файл на каждый карандаш ({len(self.pens)} шт., суффикс _pen1, _pen2...)")
            self.per_pen_cb.setChecked(True)
            layout.addWidget(self.per_pen_cb)

        buttons_layout = QHBoxLayout()
        ok_btn = QPushButton("Сохранить как...")
        cancel_btn = QPushButton("Отмена")
        ok_btn.clicked.connect(self.accept)
        cancel_btn.clicked.connect(self.reject)
        buttons_layout.addWidget(ok_btn)
        buttons_layout.addWidget(cancel_btn)
        layout.addLayout(buttons_layout)

    def get_options(self):
        return {
            'feed': self.feed_spin.value(),
            'travel_z': self.travel_z_spin.value(),
            'draw_z': self.draw_z_spin.value(),
            'optimize': self.optimize_cb.isChecked(),
            'per_pen': bool(self.per_pen_cb and self.per_pen_cb.isChecked()),
        }
