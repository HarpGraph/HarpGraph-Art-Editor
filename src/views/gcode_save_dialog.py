# -*- coding: utf-8 -*-
from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QFormLayout, QDoubleSpinBox, QCheckBox,
                             QPushButton, QHBoxLayout, QLabel)
from PyQt5.QtCore import QSettings


class GcodeSaveDialog(QDialog):
    """Параметры «специального сохранения» отредактированного G-code слоя.
    Значения запоминаются между запусками."""

    def __init__(self, parent=None, layer=None):
        super().__init__(parent)
        self.setWindowTitle("Сохранить G-code слоя")
        self._settings = QSettings('SVG_GCode_Editor', 'LayerEditor')
        s = self._settings

        layout = QVBoxLayout(self)
        if layer is not None:
            layout.addWidget(QLabel(f"Слой: {layer.name}"))

        self.placement_cb = QCheckBox("Учесть положение и масштаб слоя на поле")
        self.placement_cb.setToolTip(
            "Файл будет таким, каким рисунок лежит на рабочем поле (центр поля = 0,0, Y вверх).\n"
            "После сохранения сдвиг и масштаб слоя обнуляются, вид на листе не меняется.")
        self.placement_cb.setChecked(s.value('save/placement', True, type=bool))
        layout.addWidget(self.placement_cb)

        self.with_z_cb = QCheckBox("Писать подъём и опускание пера (команды Z)")
        self.with_z_cb.setToolTip("Выключено — только G0/G1 по X и Y, без Z")
        self.with_z_cb.setChecked(s.value('save/with_z', True, type=bool))
        layout.addWidget(self.with_z_cb)

        form = QFormLayout()
        self.up_spin = QDoubleSpinBox()
        self.up_spin.setRange(-500.0, 500.0)
        self.up_spin.setDecimals(2)
        self.up_spin.setValue(s.value('save/z_up', getattr(layer, 'travel_z', 5.0), type=float))
        form.addRow("Z пера поднято:", self.up_spin)
        self.down_spin = QDoubleSpinBox()
        self.down_spin.setRange(-500.0, 500.0)
        self.down_spin.setDecimals(2)
        self.down_spin.setValue(s.value('save/z_down', 0.0, type=float))
        form.addRow("Z пера опущено:", self.down_spin)
        self.feed_spin = QDoubleSpinBox()
        self.feed_spin.setRange(1.0, 100000.0)
        self.feed_spin.setDecimals(1)
        self.feed_spin.setValue(s.value('save/feed', 1000.0, type=float))
        form.addRow("Подача (мм/мин):", self.feed_spin)
        layout.addLayout(form)

        self.with_z_cb.toggled.connect(self._sync_enabled)
        self._sync_enabled(self.with_z_cb.isChecked())

        row = QHBoxLayout()
        ok_btn = QPushButton("Сохранить как...")
        cancel_btn = QPushButton("Отмена")
        ok_btn.clicked.connect(self._accept)
        cancel_btn.clicked.connect(self.reject)
        row.addWidget(ok_btn)
        row.addWidget(cancel_btn)
        layout.addLayout(row)

    def _sync_enabled(self, with_z):
        for w in (self.up_spin, self.down_spin, self.feed_spin):
            w.setEnabled(bool(with_z))

    def _accept(self):
        s = self._settings
        s.setValue('save/placement', self.placement_cb.isChecked())
        s.setValue('save/with_z', self.with_z_cb.isChecked())
        s.setValue('save/z_up', self.up_spin.value())
        s.setValue('save/z_down', self.down_spin.value())
        s.setValue('save/feed', self.feed_spin.value())
        self.accept()

    def get_options(self):
        return {
            'apply_placement': self.placement_cb.isChecked(),
            'with_z': self.with_z_cb.isChecked(),
            'travel_z': self.up_spin.value(),
            'draw_z': self.down_spin.value(),
            'feed': self.feed_spin.value(),
        }
