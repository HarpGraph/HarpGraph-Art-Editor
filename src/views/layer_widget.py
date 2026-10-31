from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QSlider, QCheckBox, QGroupBox, QRadioButton,
                             QPushButton, QColorDialog, QDoubleSpinBox, QFileDialog, QComboBox)
from PyQt5.QtCore import Qt, QSettings
import logging
from ..models.base_layer import BaseLayer
from ..models.gcode_layer import GCodeLayer
from ..models.image_layer import ImageLayer
from ..models.svg_layer import SvgLayer
from .svg_export_dialog import SvgExportDialog
from .gcode_trim_dialog import GcodeTrimDialog
from PyQt5.QtGui import QColor, QTransform

logger = logging.getLogger(__name__)

class LayerWidget(QWidget):
    """Виджет для управления отдельным слоем"""
    
    def __init__(self, layer: BaseLayer, parent=None):
        try:
            super().__init__(parent)
            self.layer = layer
            self.setup_ui()
            logger.debug(f"Created layer widget for: {layer.name}")
        except Exception as e:
            logger.error(f"Error creating layer widget: {e}", exc_info=True)
        
    def setup_ui(self):
        try:
            layout = QVBoxLayout(self)
            layout.setContentsMargins(2, 2, 2, 2)

            # Видимость и блокировка слоя -- в списке слоёв (глазик и замок);
            # здесь только имя
            name_label = QLabel(self.layer.name)
            layout.addWidget(name_label)

            # Режим отображения и параметры для G-code слоев
            if isinstance(self.layer, GCodeLayer):
                mode_group = QGroupBox("Режим отображения")
                mode_layout = QVBoxLayout()
                
                self.lines_radio = QRadioButton("Линии")
                self.points_radio = QRadioButton("Точки")
                self.both_radio = QRadioButton("Линии и точки")
                
                mode_layout.addWidget(self.lines_radio)
                mode_layout.addWidget(self.points_radio)
                mode_layout.addWidget(self.both_radio)

                # Кнопки цветов
                color_layout = QHBoxLayout()
                self.line_color_btn = QPushButton("Цвет линий")
                self.point_color_btn = QPushButton("Цвет точек")
                self.travel_color_btn = QPushButton("Цвет холостых")
                self.line_color_btn.clicked.connect(self.change_line_color)
                self.point_color_btn.clicked.connect(self.change_point_color)
                self.travel_color_btn.clicked.connect(self.change_travel_color)
                color_layout.addWidget(self.line_color_btn)
                color_layout.addWidget(self.point_color_btn)
                color_layout.addWidget(self.travel_color_btn)
                self.update_color_buttons()
                mode_layout.addLayout(color_layout)

                # Холостые ходы (Z >= порог -- инструмент поднят, линию не рисует)
                travel_layout = QHBoxLayout()
                self.travel_cb = QCheckBox("Холостые ходы")
                self.travel_cb.setToolTip("Показывать перемещения с поднятым инструментом (Z ≥ порога)")
                self.travel_cb.setChecked(getattr(self.layer, 'show_travel', True))
                self.travel_cb.toggled.connect(self.on_travel_toggled)
                travel_layout.addWidget(self.travel_cb)
                self.travel_z_spin = QDoubleSpinBox()
                self.travel_z_spin.setRange(0.0, 500.0)
                self.travel_z_spin.setDecimals(1)
                self.travel_z_spin.setValue(getattr(self.layer, 'travel_z', 5.0))
                self.travel_z_spin.setToolTip("Z ≥ порога считается холостым ходом (применяется при следующей загрузке файла)")
                self.travel_z_spin.valueChanged.connect(self.on_travel_z_changed)
                travel_layout.addWidget(self.travel_z_spin)
                mode_layout.addLayout(travel_layout)

                # Слайдер прозрачности
                opacity_layout = QHBoxLayout()
                opacity_label = QLabel("Прозрачность:")
                self.opacity_slider = QSlider(Qt.Horizontal)
                self.opacity_slider.setRange(0, 100)
                self.opacity_slider.setValue(int(getattr(self.layer, 'opacity', 1.0) * 100))
                self.opacity_slider.valueChanged.connect(self.on_opacity_changed)
                opacity_layout.addWidget(opacity_label)
                opacity_layout.addWidget(self.opacity_slider)
                mode_layout.addLayout(opacity_layout)

                # Привязка к сетке
                grid_layout = QHBoxLayout()
                self.grid_lock_cb = QCheckBox("Привязка к сетке")
                self.grid_lock_cb.setToolTip("Привязать слой к линиям сетки")
                self.grid_lock_cb.setChecked(self.layer._grid_locked)
                self.grid_lock_cb.toggled.connect(self.on_grid_lock_changed)
                grid_layout.addWidget(self.grid_lock_cb)
                mode_layout.addLayout(grid_layout)

                # Положение и размер относительно рабочего поля (центр поля = 0,0)
                self._setup_transform_ui(mode_layout)

                # Редактирование G-code: все действия -- в одном списке (будет расти)
                self._setup_edit_ui(mode_layout)

                mode_group.setLayout(mode_layout)
                layout.addWidget(mode_group)
                
                # Подключаем сигналы
                self.lines_radio.toggled.connect(lambda checked: self.on_mode_changed('lines') if checked else None)
                self.points_radio.toggled.connect(lambda checked: self.on_mode_changed('points') if checked else None)
                self.both_radio.toggled.connect(lambda checked: self.on_mode_changed('both') if checked else None)
                
                # Восстанавливаем текущий режим отображения
                current_mode = self.layer.current_item.render_mode if self.layer.current_item else 'both'
                if current_mode == 'lines':
                    self.lines_radio.setChecked(True)
                elif current_mode == 'points':
                    self.points_radio.setChecked(True)
                else:
                    self.both_radio.setChecked(True)
            elif isinstance(self.layer, ImageLayer):
                image_group = QGroupBox("Параметры изображения")
                image_layout = QVBoxLayout()

                size_layout = QHBoxLayout()
                size_layout.addWidget(QLabel("Ширина (мм):"))
                self.width_spin = QDoubleSpinBox()
                self.width_spin.setRange(1.0, 10000.0)
                self.width_spin.setDecimals(2)
                width_mm = getattr(self.layer, 'image_size_mm', (None, None))[0] or 100.0
                self.width_spin.setValue(width_mm)
                self.width_spin.valueChanged.connect(self.on_image_size_changed)
                size_layout.addWidget(self.width_spin)

                size_layout.addWidget(QLabel("Высота (мм):"))
                self.height_spin = QDoubleSpinBox()
                self.height_spin.setRange(1.0, 10000.0)
                self.height_spin.setDecimals(2)
                height_mm = getattr(self.layer, 'image_size_mm', (None, None))[1] or 100.0
                self.height_spin.setValue(height_mm)
                self.height_spin.valueChanged.connect(self.on_image_size_changed)
                size_layout.addWidget(self.height_spin)

                image_layout.addLayout(size_layout)

                opacity_layout = QHBoxLayout()
                opacity_label = QLabel("Прозрачность:")
                self.opacity_slider = QSlider(Qt.Horizontal)
                self.opacity_slider.setRange(0, 100)
                self.opacity_slider.setValue(int(getattr(self.layer, 'opacity', 1.0) * 100))
                self.opacity_slider.valueChanged.connect(self.on_opacity_changed)
                opacity_layout.addWidget(opacity_label)
                opacity_layout.addWidget(self.opacity_slider)
                image_layout.addLayout(opacity_layout)

                image_group.setLayout(image_layout)
                layout.addWidget(image_group)
            elif isinstance(self.layer, SvgLayer):
                svg_group = QGroupBox("Параметры SVG")
                svg_layout = QVBoxLayout()

                size_layout = QHBoxLayout()
                size_layout.addWidget(QLabel("Ширина (мм):"))
                self.width_spin = QDoubleSpinBox()
                self.width_spin.setRange(1.0, 10000.0)
                self.width_spin.setDecimals(2)
                width_mm = getattr(self.layer, 'svg_size_mm', (None, None))[0] or 100.0
                self.width_spin.setValue(width_mm)
                self.width_spin.valueChanged.connect(self.on_image_size_changed)
                size_layout.addWidget(self.width_spin)

                size_layout.addWidget(QLabel("Высота (мм):"))
                self.height_spin = QDoubleSpinBox()
                self.height_spin.setRange(1.0, 10000.0)
                self.height_spin.setDecimals(2)
                height_mm = getattr(self.layer, 'svg_size_mm', (None, None))[1] or 100.0
                self.height_spin.setValue(height_mm)
                self.height_spin.valueChanged.connect(self.on_image_size_changed)
                size_layout.addWidget(self.height_spin)
                svg_layout.addLayout(size_layout)

                color_layout = QHBoxLayout()
                self.line_color_btn = QPushButton("Цвет линий")
                self.line_color_btn.clicked.connect(self.change_line_color)
                color_layout.addWidget(self.line_color_btn)
                self.update_color_buttons()
                svg_layout.addLayout(color_layout)

                opacity_layout = QHBoxLayout()
                opacity_label = QLabel("Прозрачность:")
                self.opacity_slider = QSlider(Qt.Horizontal)
                self.opacity_slider.setRange(0, 100)
                self.opacity_slider.setValue(int(getattr(self.layer, 'opacity', 1.0) * 100))
                self.opacity_slider.valueChanged.connect(self.on_opacity_changed)
                opacity_layout.addWidget(opacity_label)
                opacity_layout.addWidget(self.opacity_slider)
                svg_layout.addLayout(opacity_layout)

                self.export_gcode_btn = QPushButton("Экспортировать как G-code...")
                self.export_gcode_btn.clicked.connect(self.on_export_gcode)
                svg_layout.addWidget(self.export_gcode_btn)

                svg_group.setLayout(svg_layout)
                layout.addWidget(svg_group)
            else:
                # Для иных слоёв только прозрачность
                opacity_layout = QHBoxLayout()
                opacity_label = QLabel("Прозрачность:")
                self.opacity_slider = QSlider(Qt.Horizontal)
                self.opacity_slider.setRange(0, 100)
                self.opacity_slider.setValue(int(getattr(self.layer, 'opacity', 1.0) * 100))
                self.opacity_slider.valueChanged.connect(self.on_opacity_changed)
                opacity_layout.addWidget(opacity_label)
                opacity_layout.addWidget(self.opacity_slider)
                layout.addLayout(opacity_layout)

        except Exception as e:
            logger.error(f"Error setting up layer widget UI: {e}", exc_info=True)

    # ------------------------------------------------------------------
    # Редактирование G-code: выпадающий список действий + кнопка «Выполнить»
    # ------------------------------------------------------------------

    def _gcode_edit_actions(self):
        """(название, обработчик) -- сюда добавляются новые операции редактирования."""
        return [
            ("Обрезать / сохранить часть (ползунки)...", self.on_export_range),
            ("Выделить полигоном...", self.on_select_polygon),
            ("Выделить рамкой...", self.on_select_rect),
            ("Копировать выделенные линии в новый слой...", lambda: self.window().extract_selected_lines(self.layer, cut=False)),
            ("Вырезать выделенные линии в новый слой...", lambda: self.window().extract_selected_lines(self.layer, cut=True)),
        ]

    def _setup_edit_ui(self, parent_layout):
        group = QGroupBox("Редактирование G-code")
        v = QVBoxLayout()
        self.edit_combo = QComboBox()
        self._edit_handlers = []
        for title, handler in self._gcode_edit_actions():
            self.edit_combo.addItem(title)
            self._edit_handlers.append(handler)
        v.addWidget(self.edit_combo)
        run_btn = QPushButton("Выполнить")
        run_btn.setToolTip("Запустить выбранное действие над этим слоем")
        run_btn.clicked.connect(self.on_run_edit_action)
        v.addWidget(run_btn)

        # Как область (полигон/рамка) обращается с блоками штрихов; значения хранятся в QSettings
        # (окно слоя создаётся до добавления в главное окно, поэтому читаем настройки напрямую)
        settings = QSettings('SVG_GCode_Editor', 'LayerEditor')
        self.block_cb = QCheckBox("Область не перерезает блоки штрихов")
        self.block_cb.setToolTip(
            "Блок штрихов (между подъёмами инструмента), которого касается граница области, "
            "не режется, а целиком уходит внутрь или остаётся снаружи.\n"
            "Работает в режимах «Линии» и «Линии и точки»; в режиме «Точки» режутся точки.")
        self.block_cb.setChecked(settings.value('area/block_mode', False, type=bool))
        v.addWidget(self.block_cb)
        tol_row = QHBoxLayout()
        tol_row.addWidget(QLabel("Допуск у границы, мм:"))
        self.tol_spin = QDoubleSpinBox()
        self.tol_spin.setRange(0.0, 1000.0)
        self.tol_spin.setDecimals(2)
        self.tol_spin.setValue(settings.value('area/tol_mm', 1.0, type=float))
        self.tol_spin.setToolTip(
            "Блок, выступающий за границу не больше допуска, остаётся внутри; заходящий внутрь "
            "не глубже допуска — остаётся снаружи; иначе блок уходит туда, где его большая часть.")
        self.tol_spin.setEnabled(self.block_cb.isChecked())
        tol_row.addWidget(self.tol_spin)
        v.addLayout(tol_row)
        self.block_cb.toggled.connect(self._on_block_options)
        self.tol_spin.valueChanged.connect(self._on_block_options)
        group.setLayout(v)
        parent_layout.addWidget(group)

    def _on_block_options(self, *_):
        self.tol_spin.setEnabled(self.block_cb.isChecked())
        mw = self.window()
        if hasattr(mw, 'set_area_options'):
            mw.set_area_options(block_mode=self.block_cb.isChecked(), tol_mm=self.tol_spin.value())

    def on_run_edit_action(self):
        idx = self.edit_combo.currentIndex()
        if 0 <= idx < len(self._edit_handlers):
            self._edit_handlers[idx]()

    def on_select_polygon(self):
        try:
            item = getattr(self.layer, 'current_item', None)
            if item is None or item.point_count() == 0:
                logger.warning("No G-code loaded for this layer -- nothing to select")
                return
            self.window().start_polygon_selection(self.layer)
        except Exception as e:
            logger.error(f"Error starting polygon selection: {e}", exc_info=True)

    def on_select_rect(self):
        try:
            item = getattr(self.layer, 'current_item', None)
            if item is None or item.point_count() == 0:
                logger.warning("No G-code loaded for this layer -- nothing to select")
                return
            self.window().start_rect_selection(self.layer)
        except Exception as e:
            logger.error(f"Error starting rect selection: {e}", exc_info=True)

    def on_visibility_changed(self, visible):
        self.layer.visible = visible
        logger.debug(f"Layer {self.layer.name} visibility changed to: {visible}")

    # ------------------------------------------------------------------
    # Положение и размер G-code относительно рабочего поля
    # (нулевая точка станка = центр поля; Y в полях -- вверх, как на станке)
    # ------------------------------------------------------------------

    def _setup_transform_ui(self, parent_layout):
        group = QGroupBox("Положение и размер (мм, центр поля = 0,0)")
        v = QVBoxLayout()

        pos_row = QHBoxLayout()
        pos_row.addWidget(QLabel("X:"))
        self.tx_x = QDoubleSpinBox()
        self.tx_x.setRange(-100000, 100000)
        self.tx_x.setDecimals(2)
        self.tx_x.setToolTip("Сдвиг рисунка вправо от центра поля (мм)")
        pos_row.addWidget(self.tx_x)
        pos_row.addWidget(QLabel("Y:"))
        self.tx_y = QDoubleSpinBox()
        self.tx_y.setRange(-100000, 100000)
        self.tx_y.setDecimals(2)
        self.tx_y.setToolTip("Сдвиг рисунка вверх от центра поля (мм)")
        pos_row.addWidget(self.tx_y)
        v.addLayout(pos_row)

        size_row = QHBoxLayout()
        size_row.addWidget(QLabel("Ш:"))
        self.tx_w = QDoubleSpinBox()
        self.tx_w.setRange(0.01, 100000)
        self.tx_w.setDecimals(2)
        self.tx_w.setToolTip("Ширина рисунка (мм). Меняется отдельно от высоты = искажение")
        size_row.addWidget(self.tx_w)
        size_row.addWidget(QLabel("В:"))
        self.tx_h = QDoubleSpinBox()
        self.tx_h.setRange(0.01, 100000)
        self.tx_h.setDecimals(2)
        self.tx_h.setToolTip("Высота рисунка (мм). Меняется отдельно от ширины = искажение")
        size_row.addWidget(self.tx_h)
        self.tx_keep = QCheckBox("Пропорц.")
        self.tx_keep.setChecked(True)
        self.tx_keep.setToolTip("Менять ширину и высоту вместе, сохраняя пропорции")
        size_row.addWidget(self.tx_keep)
        v.addLayout(size_row)

        btn_row = QHBoxLayout()
        center_btn = QPushButton("В центр поля")
        center_btn.setToolTip("Поставить центр рисунка в нулевую точку станка (центр поля)")
        center_btn.clicked.connect(self.on_center_in_field)
        btn_row.addWidget(center_btn)
        reset_btn = QPushButton("Сброс")
        reset_btn.setToolTip("Вернуть исходное положение и размер из файла")
        reset_btn.clicked.connect(self.on_reset_transform)
        btn_row.addWidget(reset_btn)
        refresh_btn = QPushButton("⟳")
        refresh_btn.setFixedWidth(28)
        refresh_btn.setToolTip("Обновить поля после перетаскивания мышью")
        refresh_btn.clicked.connect(self.refresh_transform_fields)
        btn_row.addWidget(refresh_btn)
        v.addLayout(btn_row)

        group.setLayout(v)
        parent_layout.addWidget(group)

        self.refresh_transform_fields()
        self.tx_x.valueChanged.connect(self.on_transform_pos_changed)
        self.tx_y.valueChanged.connect(self.on_transform_pos_changed)
        self.tx_w.valueChanged.connect(lambda _v: self.on_transform_size_changed('w'))
        self.tx_h.valueChanged.connect(lambda _v: self.on_transform_size_changed('h'))

    def _content_size(self):
        """Исходный (немасштабированный) размер рисунка в мм из массива точек."""
        item = getattr(self.layer, 'current_item', None)
        pts = getattr(item, 'pts', None)
        if item is None or pts is None or len(pts) == 0:
            return 0.0, 0.0
        w = float(pts[:, 0].max() - pts[:, 0].min())
        h = float(pts[:, 1].max() - pts[:, 1].min())
        return max(w, 1e-6), max(h, 1e-6)

    def _set_spin(self, spin, value):
        spin.blockSignals(True)
        spin.setValue(value)
        spin.blockSignals(False)

    def refresh_transform_fields(self):
        """Считать текущие положение/размер из элемента сцены в поля."""
        try:
            item = getattr(self.layer, 'current_item', None)
            if item is None or not hasattr(self, 'tx_x'):
                return
            pos = item.pos()
            self._set_spin(self.tx_x, pos.x())
            self._set_spin(self.tx_y, -pos.y())
            w0, h0 = self._content_size()
            tr = item.transform()
            self._set_spin(self.tx_w, w0 * tr.m11())
            self._set_spin(self.tx_h, h0 * tr.m22())
        except Exception as e:
            logger.error(f"Error refreshing transform fields: {e}", exc_info=True)

    def _apply_scale(self, sx, sy):
        item = getattr(self.layer, 'current_item', None)
        if item is None:
            return
        self.layer.scale_x = sx
        self.layer.scale_y = sy
        item.setTransform(QTransform().scale(sx, sy))
        self._notify_bounds_changed()

    def _notify_bounds_changed(self):
        mw = self.window()
        if hasattr(mw, '_update_scene_bounds'):
            mw._update_scene_bounds()

    def on_transform_pos_changed(self, *_):
        try:
            item = getattr(self.layer, 'current_item', None)
            if item is None:
                return
            item.setPos(self.tx_x.value(), -self.tx_y.value())
            self._notify_bounds_changed()
        except Exception as e:
            logger.error(f"Error moving layer: {e}", exc_info=True)

    def on_transform_size_changed(self, which: str):
        try:
            item = getattr(self.layer, 'current_item', None)
            if item is None:
                return
            w0, h0 = self._content_size()
            if w0 <= 0 or h0 <= 0:
                return
            if which == 'w':
                sx = self.tx_w.value() / w0
                if self.tx_keep.isChecked():
                    sy = sx
                    self._set_spin(self.tx_h, h0 * sy)
                else:
                    sy = item.transform().m22()
            else:
                sy = self.tx_h.value() / h0
                if self.tx_keep.isChecked():
                    sx = sy
                    self._set_spin(self.tx_w, w0 * sx)
                else:
                    sx = item.transform().m11()
            self._apply_scale(sx, sy)
        except Exception as e:
            logger.error(f"Error scaling layer: {e}", exc_info=True)

    def on_center_in_field(self):
        """Поставить центр рисунка в нулевую точку станка (центр рабочего поля)."""
        try:
            item = getattr(self.layer, 'current_item', None)
            pts = getattr(item, 'pts', None)
            if item is None or pts is None or len(pts) == 0:
                return
            cx = float(pts[:, 0].max() + pts[:, 0].min()) / 2.0
            cy = float(pts[:, 1].max() + pts[:, 1].min()) / 2.0
            tr = item.transform()
            item.setPos(-cx * tr.m11(), -cy * tr.m22())
            self.refresh_transform_fields()
            self._notify_bounds_changed()
        except Exception as e:
            logger.error(f"Error centering layer: {e}", exc_info=True)

    def on_reset_transform(self):
        try:
            item = getattr(self.layer, 'current_item', None)
            if item is None:
                return
            item.setPos(0, 0)
            self._apply_scale(1.0, 1.0)
            self.refresh_transform_fields()
        except Exception as e:
            logger.error(f"Error resetting transform: {e}", exc_info=True)

    def on_lock_changed(self, locked):
        try:
            self.layer.locked = locked
            logger.debug(f"Layer {self.layer.name} lock changed to: {locked}")
        except Exception as e:
            logger.error(f"Error changing layer lock state: {e}", exc_info=True)

    def on_opacity_changed(self, value):
        opacity = value / 100.0
        self.layer.opacity = opacity
        logger.debug(f"Layer {self.layer.name} opacity changed to: {opacity}")

    def on_export_range(self):
        """Open the visual dual-slider trim tool for this G-code layer."""
        try:
            if not self.layer.current_item or self.layer.current_item.point_count() == 0:
                logger.warning("No G-code loaded for this layer -- nothing to trim")
                return
            dialog = GcodeTrimDialog(self, self.layer)
            dialog.show()
            self._trim_dialog = dialog   # keep a reference so it isn't GC'd while open
        except Exception as e:
            logger.error(f"Error opening trim dialog: {e}", exc_info=True)

    def on_export_gcode(self):
        """Convert the SVG layer's flattened paths into a G-code file."""
        try:
            dialog = SvgExportDialog(self, pens=getattr(self.layer, 'pens', []))
            if dialog.exec_() != SvgExportDialog.Accepted:
                return
            opts = dialog.get_options()
            filename, _ = QFileDialog.getSaveFileName(
                self, "Экспортировать как G-code", "", "G-code files (*.gcode);;All files (*.*)")
            if not filename:
                return
            ok = self.layer.export_gcode(filename, feed=opts['feed'], travel_z=opts['travel_z'],
                                          draw_z=opts['draw_z'], optimize=opts['optimize'],
                                          per_pen=opts['per_pen'])
            if not ok:
                logger.error("Failed to export G-code from SVG layer")
        except Exception as e:
            logger.error(f"Error exporting SVG to G-code: {e}", exc_info=True)

    def on_mode_changed(self, mode: str):
        """Обработчик изменения режима отображения G-code"""
        try:
            if isinstance(self.layer, GCodeLayer) and self.layer.current_item:
                self.layer.current_item.set_render_mode(mode)
                logger.debug(f"Layer {self.layer.name} render mode changed to: {mode}")
        except Exception as e:
            logger.error(f"Error changing render mode: {e}", exc_info=True)

    def on_grid_lock_changed(self, locked):
        """Обработчик изменения привязки к сетке"""
        try:
            self.layer._grid_locked = locked
            if self.layer.current_item:
                self.layer.current_item.set_grid_lock(locked)
            logger.debug(f"Layer {self.layer.name} grid lock changed to: {locked}")
        except Exception as e:
            logger.error(f"Error changing grid lock state: {e}", exc_info=True)

    def on_image_size_changed(self):
        """Applies the width/height spinboxes -- shared by ImageLayer and SvgLayer."""
        try:
            width = self.width_spin.value() if hasattr(self, 'width_spin') else None
            height = self.height_spin.value() if hasattr(self, 'height_spin') else None
            if width and height and hasattr(self.layer, 'set_size'):
                self.layer.set_size(width, height)
        except Exception as e:
            logger.error(f"Error changing layer size: {e}", exc_info=True)

    def update_color_buttons(self):
        try:
            if hasattr(self, 'line_color_btn'):
                line_color = getattr(self.layer, 'line_color', QColor(Qt.black))
                self.line_color_btn.setStyleSheet(f"background-color: {line_color.name()}")
            if hasattr(self, 'point_color_btn'):
                point_color = getattr(self.layer, 'point_color', QColor(Qt.red))
                self.point_color_btn.setStyleSheet(f"background-color: {point_color.name()}")
            if hasattr(self, 'travel_color_btn'):
                travel_color = getattr(self.layer, 'travel_color', QColor(170, 102, 204))
                self.travel_color_btn.setStyleSheet(f"background-color: {travel_color.name()}")
        except Exception as e:
            logger.error(f"Error updating color buttons: {e}", exc_info=True)

    def change_line_color(self):
        try:
            current = getattr(self.layer, 'line_color', QColor(Qt.black))
            color = QColorDialog.getColor(current, self, "Выберите цвет линий")
            if color.isValid():
                self.layer.line_color = color
                if getattr(self.layer, 'current_item', None):
                    self.layer.current_item.set_line_color(color)
                self.update_color_buttons()
                logger.debug(f"Layer {self.layer.name} line color changed to: {color.name()}")
        except Exception as e:
            logger.error(f"Error changing line color: {e}", exc_info=True)

    def change_point_color(self):
        try:
            current = getattr(self.layer, 'point_color', QColor(Qt.red))
            color = QColorDialog.getColor(current, self, "Выберите цвет точек")
            if color.isValid():
                self.layer.point_color = color
                if getattr(self.layer, 'current_item', None):
                    self.layer.current_item.set_point_color(color)
                self.update_color_buttons()
                logger.debug(f"Layer {self.layer.name} point color changed to: {color.name()}")
        except Exception as e:
            logger.error(f"Error changing point color: {e}", exc_info=True)

    def change_travel_color(self):
        try:
            current = getattr(self.layer, 'travel_color', QColor(170, 102, 204))
            color = QColorDialog.getColor(current, self, "Выберите цвет холостых ходов")
            if color.isValid():
                self.layer.travel_color = color
                if getattr(self.layer, 'current_item', None):
                    self.layer.current_item.set_travel_color(color)
                self.update_color_buttons()
                logger.debug(f"Layer {self.layer.name} travel color changed to: {color.name()}")
        except Exception as e:
            logger.error(f"Error changing travel color: {e}", exc_info=True)

    def on_travel_toggled(self, visible):
        try:
            self.layer.show_travel = visible
            if getattr(self.layer, 'current_item', None):
                self.layer.current_item.set_travel_visible(visible)
            logger.debug(f"Layer {self.layer.name} travel visibility changed to: {visible}")
        except Exception as e:
            logger.error(f"Error toggling travel visibility: {e}", exc_info=True)

    def on_travel_z_changed(self, value):
        self.layer.travel_z = float(value)