# -*- coding: utf-8 -*-
"""Visual point-range trim tool -- drag two sliders to preview + save a
portion of the loaded G-code, watching the cut live on the canvas."""
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QSlider, QPushButton,
    QPlainTextEdit, QFileDialog, QRadioButton, QColorDialog
)
from PyQt5.QtGui import QTextCursor, QColor
from PyQt5.QtCore import Qt, QSettings
import logging

logger = logging.getLogger(__name__)


class GcodeTrimDialog(QDialog):
    def __init__(self, parent, layer):
        super().__init__(parent)
        self.layer = layer
        self.setWindowTitle(f"Обрезка G-code — {layer.name}")
        self.setModal(False)   # lets you pan/zoom the canvas while trimming
        item = layer.current_item
        self.total_points = item.point_count() if item else 0
        self._text_visible = False
        self.settings = QSettings('SVG_GCode_Editor', 'LayerEditor')
        self._removed_color = QColor(self.settings.value('trim/removed_color', '#5a5a5a'))
        self.setup_ui()
        self._update_preview()

    def setup_ui(self):
        layout = QVBoxLayout(self)

        self.info_label = QLabel()
        layout.addWidget(self.info_label)

        start_row = QHBoxLayout()
        start_row.addWidget(QLabel("Обрезать с начала:"))
        self.start_slider = QSlider(Qt.Horizontal)
        self.start_slider.setRange(0, max(self.total_points - 1, 0))
        self.start_slider.setValue(0)
        self.start_slider.valueChanged.connect(self._update_preview)
        start_row.addWidget(self.start_slider)
        layout.addLayout(start_row)

        end_row = QHBoxLayout()
        end_row.addWidget(QLabel("Обрезать с конца:"))
        self.end_slider = QSlider(Qt.Horizontal)
        self.end_slider.setRange(0, max(self.total_points - 1, 0))
        self.end_slider.setValue(0)
        # handle at the right = "last point", dragging left eats into the end
        self.end_slider.setInvertedAppearance(True)
        self.end_slider.setInvertedControls(True)
        self.end_slider.valueChanged.connect(self._update_preview)
        end_row.addWidget(self.end_slider)
        layout.addLayout(end_row)

        # Removed-range display: a fixed grey overlay is uncomfortable for
        # some eyes, so both hide-vs-tint and colour/opacity are adjustable.
        mode_row = QHBoxLayout()
        self.hide_radio = QRadioButton("Скрывать полностью")
        self.color_radio = QRadioButton("Выделять цветом")
        hide_default = self.settings.value('trim/hide_removed', True, type=bool)
        self.hide_radio.setChecked(hide_default)
        self.color_radio.setChecked(not hide_default)
        self.hide_radio.toggled.connect(self._on_mode_changed)
        mode_row.addWidget(self.hide_radio)
        mode_row.addWidget(self.color_radio)
        layout.addLayout(mode_row)

        color_row = QHBoxLayout()
        color_row.addWidget(QLabel("Цвет удаляемого:"))
        self.color_btn = QPushButton()
        self.color_btn.setFixedWidth(40)
        self.color_btn.clicked.connect(self._on_pick_color)
        color_row.addWidget(self.color_btn)
        color_row.addWidget(QLabel("Прозрачность:"))
        self.opacity_slider = QSlider(Qt.Horizontal)
        self.opacity_slider.setRange(5, 100)
        self.opacity_slider.setValue(int(self.settings.value('trim/removed_opacity', 0.6, type=float) * 100))
        self.opacity_slider.valueChanged.connect(self._on_settings_changed)
        color_row.addWidget(self.opacity_slider)
        layout.addLayout(color_row)
        self._update_color_button()
        self._update_color_controls_enabled()

        self.toggle_text_btn = QPushButton("Показать текст G-code")
        self.toggle_text_btn.setCheckable(True)
        self.toggle_text_btn.toggled.connect(self._on_toggle_text)
        layout.addWidget(self.toggle_text_btn)

        self.text_view = QPlainTextEdit()
        self.text_view.setReadOnly(True)
        self.text_view.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.text_view.setVisible(False)
        self.text_view.setMinimumHeight(300)
        layout.addWidget(self.text_view)

        buttons_row = QHBoxLayout()
        self.save_btn = QPushButton("Сохранить выделенное...")
        self.save_btn.clicked.connect(self._on_save)
        close_btn = QPushButton("Закрыть")
        close_btn.clicked.connect(self.close)
        buttons_row.addWidget(self.save_btn)
        buttons_row.addWidget(close_btn)
        layout.addLayout(buttons_row)

        self.resize(560, 200)

    # ------------------------------------------------------------------
    # Live preview
    # ------------------------------------------------------------------

    def _end_index(self):
        return max(0, self.total_points - 1 - self.end_slider.value())

    def _update_preview(self, *_):
        item = self.layer.current_item
        if not item or self.total_points == 0:
            return
        start_idx = self.start_slider.value()
        end_idx = self._end_index()
        if start_idx > end_idx:
            # don't let the two handles cross -- clamp whichever just moved
            if self.sender() is self.end_slider:
                self.end_slider.setValue(self.total_points - 1 - start_idx)
                end_idx = start_idx
            else:
                self.start_slider.setValue(end_idx)
                start_idx = end_idx

        item.set_trim_highlight(start_idx, end_idx, hide_removed=self.hide_radio.isChecked(),
                                 removed_color=self._removed_color,
                                 removed_opacity=self.opacity_slider.value() / 100.0)
        kept = end_idx - start_idx + 1
        pct = 100.0 * kept / self.total_points if self.total_points else 0.0
        self.info_label.setText(
            f"Останутся точки {start_idx + 1}–{end_idx + 1} из {self.total_points} "
            f"({kept} точек, {pct:.1f}%)")
        if self._text_visible:
            self._refresh_text_highlight(start_idx, end_idx)

    # ------------------------------------------------------------------
    # Removed-range display settings
    # ------------------------------------------------------------------

    def _update_color_button(self):
        self.color_btn.setStyleSheet(f"background-color: {self._removed_color.name()}")

    def _update_color_controls_enabled(self):
        enabled = self.color_radio.isChecked()
        self.color_btn.setEnabled(enabled)
        self.opacity_slider.setEnabled(enabled)

    def _on_mode_changed(self, *_):
        self._update_color_controls_enabled()
        self._save_settings()
        self._update_preview()

    def _on_settings_changed(self, *_):
        self._save_settings()
        self._update_preview()

    def _on_pick_color(self):
        color = QColorDialog.getColor(self._removed_color, self, "Цвет удаляемого участка")
        if color.isValid():
            self._removed_color = color
            self._update_color_button()
            self._save_settings()
            self._update_preview()

    def _save_settings(self):
        self.settings.setValue('trim/hide_removed', self.hide_radio.isChecked())
        self.settings.setValue('trim/removed_color', self._removed_color.name())
        self.settings.setValue('trim/removed_opacity', self.opacity_slider.value() / 100.0)

    # ------------------------------------------------------------------
    # Text view tab
    # ------------------------------------------------------------------

    def _on_toggle_text(self, checked):
        self._text_visible = checked
        self.toggle_text_btn.setText("Скрыть текст G-code" if checked else "Показать текст G-code")
        self.text_view.setVisible(checked)
        if checked:
            if not self.text_view.toPlainText():
                self._load_text()
            self._refresh_text_highlight(self.start_slider.value(), self._end_index())

    def _load_text(self):
        try:
            with open(self.layer.file_path, 'r', encoding='utf-8', errors='ignore') as f:
                self.text_view.setPlainText(f.read())
        except Exception as e:
            logger.error(f"Error loading G-code text: {e}", exc_info=True)

    def _refresh_text_highlight(self, start_idx, end_idx):
        item = self.layer.current_item
        if not item or len(item.linenos) == 0:
            return
        start_line = int(item.linenos[start_idx])
        end_line = int(item.linenos[end_idx])
        doc = self.text_view.document()
        cursor = QTextCursor(doc.findBlockByNumber(max(start_line - 1, 0)))
        cursor.movePosition(QTextCursor.StartOfBlock)
        end_block = doc.findBlockByNumber(max(end_line - 1, 0))
        cursor.setPosition(end_block.position() + max(end_block.length() - 1, 0), QTextCursor.KeepAnchor)
        self.text_view.setTextCursor(cursor)
        self.text_view.ensureCursorVisible()

    # ------------------------------------------------------------------
    # Save / close
    # ------------------------------------------------------------------

    def _on_save(self):
        try:
            start_idx = self.start_slider.value()
            end_idx = self._end_index()
            filename, _ = QFileDialog.getSaveFileName(
                self, "Сохранить фрагмент G-code", "", "G-code files (*.gcode);;All files (*.*)")
            if not filename:
                return
            if self.layer.save_point_range(filename, start_idx, end_idx):
                logger.debug(f"Saved point range {start_idx}-{end_idx} to {filename}")
            else:
                logger.error("Failed to save G-code point range")
        except Exception as e:
            logger.error(f"Error saving trimmed G-code: {e}", exc_info=True)

    def closeEvent(self, event):
        item = self.layer.current_item
        if item:
            item.clear_trim_highlight()
        super().closeEvent(event)
