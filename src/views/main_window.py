from PyQt5.QtWidgets import (QMainWindow, QDockWidget, QListWidget, 
                           QGraphicsView, QGraphicsScene, QToolBar, 
                           QAction, QFileDialog, QListWidgetItem, QWidget, 
                           QVBoxLayout, QInputDialog, QLabel, 
                           QLineEdit, QPushButton, QHBoxLayout,
                           QApplication, QSpinBox, QMessageBox,
                           QColorDialog)
from PyQt5.QtCore import Qt, QSettings, QPointF, QRectF, QEvent  # Добавляем QSettings
from PyQt5.QtGui import QPainter, QColor, QPolygonF, QPen, QBrush, QPainterPath
from src.views.layer_dialog import NewLayerDialog
from src.views.image_size_dialog import ImageSizeDialog
from src.views.svg_size_dialog import SvgSizeDialog
from src.models.layer_types import LayerType
from src.models.base_layer import BaseLayer
from .layer_widget import LayerWidget  # Добавляем импорт LayerWidget
from src.graphics.grid_item import GridItem  # Добавляем импорт GridItem
from .grid_settings_dialog import GridSettingsDialog  # Добавить в импорты
import numpy as np
import logging

logger = logging.getLogger(__name__)


def _points_in_polygon(xs, ys, poly):
    """Векторная проверка 'точка внутри полигона' (even-odd ray casting).
    xs/ys -- numpy-массивы координат, poly -- (M,2). Работает для миллионов
    точек без Python-цикла по точкам (OPT-11)."""
    inside = np.zeros(len(xs), dtype=bool)
    x1, y1 = poly[-1]
    for x2, y2 in poly:
        crosses = (y1 > ys) != (y2 > ys)
        if crosses.any():
            with np.errstate(divide='ignore', invalid='ignore'):
                x_int = (x2 - x1) * (ys - y1) / (y2 - y1) + x1
            inside ^= crosses & (xs < x_int)
        x1, y1 = x2, y2
    return inside

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__() 
        self.setWindowTitle("Layer Editor")
        self.layers = []  # Список всех слоев
        self.settings = QSettings('SVG_GCode_Editor', 'LayerEditor')
        # фиксированное "рабочее поле" (мм) -- стабильный ориентир для навигации,
        # как в Gcode_Terminal -- задаётся до setup_ui(), т.к. тулбар читает эти значения
        self.field_w_mm = 400.0
        self.field_h_mm = 400.0
        self.field_bg_color = QColor(Qt.white)
        self.field_rect_item = None
        self.setup_ui()
        self.load_grid_settings()  # Загружаем настройки сетки
        self.load_field_settings()  # Загружаем размер рабочего поля
        self.create_coordinates_widget()  # Создаем виджет для отображения координат
        self.selection_mode = False
        self.selection_points = []
        self.selection_path_item = None
        self._prev_drag_mode = None
        self.scene.installEventFilter(self)
        self._update_scene_bounds()  # ограничить прокрутку полем+содержимым
        self.fit_view_to_field()  # стартовый вид -- всё поле, а не случайный кусок сетки
        logger.debug("Main window initialized")

    def setup_ui(self):
        # Основное окно просмотра
        self.scene = QGraphicsScene(self)
        # QOpenGLWidget clears to black by default -- pin the background colour
        # explicitly (configurable -- see set_field_bg_color) so it doesn't
        # depend on the viewport widget type
        self.scene.setBackgroundBrush(QBrush(self.field_bg_color))
        self.view = QGraphicsView(self.scene)
        
        # Добавляем сетку
        self.grid = GridItem()
        self.grid.setZValue(-1000)  # Устанавливаем сетку на самый нижний слой
        self.scene.addItem(self.grid)
        logger.debug(f"Grid created: visible={self.grid.isVisible()}, zValue={self.grid.zValue()}")

        # Границы рабочего поля -- видимый стабильный ориентир (см. _update_field_visual)
        self._update_field_visual()

        # Настраиваем отображение
        self.view.setRenderHint(QPainter.Antialiasing)
        self.view.setViewportUpdateMode(QGraphicsView.BoundingRectViewportUpdate)
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.view.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.view.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.view.setResizeAnchor(QGraphicsView.AnchorUnderMouse)
        # left-drag pans the canvas (much easier than scrollbars); items with
        # ItemIsMovable still consume the press themselves and move instead
        self.view.setDragMode(QGraphicsView.ScrollHandDrag)
        
        # Добавляем обработку масштабирования
        self.view.wheelEvent = self.handle_wheel_event
        
        self.setCentralWidget(self.view)

        # Панель инструментов
        self.create_toolbar()

        # Панель слоев
        self.create_layer_dock()

    def find_layer_for_item(self, item) -> BaseLayer:
        """Поиск слоя, которому принадлежит элемент"""
        try:
            # Проходим по всем слоям
            for i in range(self.layer_list.count()):
                layer_item = self.layer_list.item(i)
                layer = layer_item.data(Qt.UserRole)
                
                # Проверяем, принадлежит ли элемент этому слою
                if hasattr(layer, 'current_item') and layer.current_item == item:
                    return layer
            
            return None
        except Exception as e:
            logger.error(f"Error finding layer for item: {e}")
            return None

    def get_current_layer_item(self):
        selected_items = self.layer_list.selectedItems()
        if selected_items:
            return selected_items[0]
        return None

    def update_layer_item_text(self, item: QListWidgetItem, layer: BaseLayer):
        text = f"{layer.name} ({layer.type})"
        file_path = getattr(layer, 'file_path', None)
        if file_path:
            text += f" - {file_path}"
        item.setText(text)

    def delete_selected_layers(self):
        selected_items = self.layer_list.selectedItems()
        if not selected_items:
            return

        # Удаляем выбранные слои
        for item in selected_items:
            layer = item.data(Qt.UserRole)
            if layer:
                for graphics_item in getattr(layer, '_items', []):
                    self.scene.removeItem(graphics_item)
                if getattr(layer, 'current_item', None):
                    self.scene.removeItem(layer.current_item)
                if layer in self.layers:
                    self.layers.remove(layer)
            row = self.layer_list.row(item)
            self.layer_list.takeItem(row)

        # Выбираем новый текущий слой (если остались)
        if self.layer_list.count() > 0:
            self.layer_list.setCurrentRow(0)
            current_item = self.layer_list.currentItem()
            self.on_layer_selected(current_item, None)
        else:
            self.on_layer_selected(None, None)

        self.update_layers_z_values()
        self._update_scene_bounds()

    def clear_all_layers(self):
        """Remove every layer's graphics items and reset layer bookkeeping
        (used before loading a project, to start from a clean scene)."""
        for layer in list(self.layers):
            removed = set()
            for graphics_item in getattr(layer, '_items', []):
                if graphics_item not in removed and graphics_item.scene():
                    self.scene.removeItem(graphics_item)
                    removed.add(graphics_item)
            current_item = getattr(layer, 'current_item', None)
            if current_item is not None and current_item not in removed and current_item.scene():
                self.scene.removeItem(current_item)
        self.layers.clear()
        self.layer_list.clear()
        self.on_layer_selected(None, None)
        self._update_scene_bounds()

    def save_project_dialog(self):
        """Save every layer (files, colours, position, opacity...) as one project file."""
        filename, _ = QFileDialog.getSaveFileName(
            self, "Сохранить проект", "", "HarpGraph project (*.hgproj);;All files (*.*)")
        if not filename:
            return
        from src.models.project_io import save_project
        if save_project(self, filename):
            logger.debug(f"Project saved: {filename}")
            QMessageBox.information(self, "Проект", f"Проект сохранён:\n{filename}")
        else:
            QMessageBox.warning(self, "Проект", "Ошибка сохранения проекта -- см. app_log.txt")

    def open_project_dialog(self):
        """Restore every layer from a previously saved project file."""
        if self.layers:
            reply = QMessageBox.question(
                self, "Открыть проект",
                "Текущие слои будут закрыты без сохранения. Продолжить?",
                QMessageBox.Yes | QMessageBox.No)
            if reply != QMessageBox.Yes:
                return
        filename, _ = QFileDialog.getOpenFileName(
            self, "Открыть проект", "", "HarpGraph project (*.hgproj);;All files (*.*)")
        if not filename:
            return
        from src.models.project_io import load_project
        try:
            added, failed = load_project(self, filename)
        except Exception as e:
            logger.error(f"Error loading project: {e}", exc_info=True)
            QMessageBox.warning(self, "Проект", f"Не удалось открыть проект:\n{e}")
            return
        # G-code/SVG слои проекта парсятся асинхронно -- границы прокрутки
        # надо пересчитать, когда каждый из них реально появится на сцене
        for layer in self.layers:
            sig = getattr(layer, 'load_finished', None)
            if sig is not None:
                sig.connect(lambda ok: self._update_scene_bounds() if ok else None)
        self._update_scene_bounds()
        self.fit_view_to_field()
        msg = f"Слоёв добавлено: {added}"
        if failed:
            missing = "\n".join(f"- {name}: {path}" for name, path in failed)
            msg += f"\n\nНе найдены файлы для {len(failed)} слоёв:\n{missing}"
        QMessageBox.information(self, "Проект", msg)

    def move_layer(self, direction: int):
        current_item = self.get_current_layer_item()
        if not current_item:
            return
        current_row = self.layer_list.row(current_item)
        target_row = current_row + direction
        if target_row < 0 or target_row >= self.layer_list.count():
            return

        item = self.layer_list.takeItem(current_row)
        self.layer_list.insertItem(target_row, item)
        self.layer_list.setCurrentItem(item)

        layer = item.data(Qt.UserRole)
        if layer in self.layers:
            layer_index = self.layers.index(layer)
            target_index = layer_index + direction
            if 0 <= target_index < len(self.layers):
                self.layers[layer_index], self.layers[target_index] = \
                    self.layers[target_index], self.layers[layer_index]

        self.update_layers_z_values()

    def update_layers_z_values(self):
        count = self.layer_list.count()
        for i in range(count):
            item = self.layer_list.item(i)
            layer = item.data(Qt.UserRole)
            z = count - i
            if layer:
                if getattr(layer, 'current_item', None):
                    layer.current_item.setZValue(z)
                for graphics_item in getattr(layer, '_items', []):
                    if graphics_item is not layer.current_item:
                        graphics_item.setZValue(z)

    def handle_wheel_event(self, event):
        """Масштабирование колесиком мыши (без необходимости держать Ctrl)"""
        try:
            factor = 1.2 if event.angleDelta().y() > 0 else 1.0 / 1.2
            self.view.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
            self.scale_view(factor)
            event.accept()
        except Exception as e:
            logger.error(f"Error in wheel event: {e}", exc_info=True)
            QGraphicsView.wheelEvent(self.view, event)

    def center_view_on_origin(self):
        """Возвращает фокус в центр рабочего поля (0,0), не меняя масштаб"""
        self.view.centerOn(0, 0)
        logger.debug("View centred on work-field origin")

    # ------------------------------------------------------------------
    # Границы навигации: сцена ограничена полем + содержимым, зум -- в пределах
    # ------------------------------------------------------------------

    def _update_scene_bounds(self):
        """Ограничить область прокрутки рабочим полем + содержимым слоёв
        с запасом -- чтобы вид не мог 'улететь' в пустоту без шанса вернуться."""
        try:
            hw, hh = self.field_w_mm / 2.0, self.field_h_mm / 2.0
            rect = QRectF(-hw, -hh, self.field_w_mm, self.field_h_mm)
            content = self._content_bounding_rect()
            if content is not None and not content.isNull():
                rect = rect.united(content)
            margin = max(rect.width(), rect.height()) * 0.5
            rect = rect.adjusted(-margin, -margin, margin, margin)
            self.scene.setSceneRect(rect)
            logger.debug(f"Scene bounds set to {rect}")
        except Exception as e:
            logger.error(f"Error updating scene bounds: {e}", exc_info=True)

    def _zoom_limits(self):
        """(min, max) допустимого масштаба вида. Минимум -- видно всю сцену
        (поле + содержимое + запас) и ещё вдвое дальше; максимум -- 2000 px/мм."""
        vp = self.view.viewport()
        rect = self.scene.sceneRect()
        if rect.width() > 1e-6 and rect.height() > 1e-6 and vp.width() > 0:
            fit = min(vp.width() / rect.width(), vp.height() / rect.height())
            min_scale = fit * 0.5
        else:
            min_scale = 0.001
        return min_scale, 2000.0

    def fit_view_to_content(self):
        """Показать все загруженные линии целиком -- 'найти потерянное'."""
        self._update_scene_bounds()
        rect = self._content_bounding_rect()
        if rect is None or rect.width() <= 1e-6 or rect.height() <= 1e-6:
            self.fit_view_to_field()
            return
        margin = max(rect.width(), rect.height()) * 0.05
        rect = rect.adjusted(-margin, -margin, margin, margin)
        self.view.fitInView(rect, Qt.KeepAspectRatio)
        self.grid.set_scale(self.view.transform().m11())
        logger.debug("View fit to layer content")

    def create_toolbar(self):
        toolbar = QToolBar()
        self.addToolBar(toolbar)

        # Действие для создания нового слоя
        new_layer_action = QAction("Новый слой", self)
        new_layer_action.triggered.connect(self.create_new_layer)
        toolbar.addAction(new_layer_action)

        # Действие для загрузки файла
        load_file_action = QAction("Загрузить файл", self)
        load_file_action.triggered.connect(self.load_file_to_layer)
        toolbar.addAction(load_file_action)

        # Действие для выделения полигона и экспорта точек
        self.polygon_action = QAction("Выделить полигон", self)
        self.polygon_action.setCheckable(True)
        self.polygon_action.triggered.connect(self.toggle_polygon_selection)
        toolbar.addAction(self.polygon_action)

        delete_layer_action = QAction("Удалить слой", self)
        delete_layer_action.triggered.connect(self.delete_selected_layers)
        toolbar.addAction(delete_layer_action)

        move_up_action = QAction("Вверх", self)
        move_up_action.triggered.connect(lambda: self.move_layer(-1))
        toolbar.addAction(move_up_action)

        move_down_action = QAction("Вниз", self)
        move_down_action.triggered.connect(lambda: self.move_layer(1))
        toolbar.addAction(move_down_action)

        # Разделитель
        toolbar.addSeparator()

        save_project_action = QAction("💾 Сохранить проект...", self)
        save_project_action.setToolTip("Сохранить все слои с файлами/цветами/позицией в один файл-пресет")
        save_project_action.triggered.connect(self.save_project_dialog)
        toolbar.addAction(save_project_action)

        open_project_action = QAction("📂 Открыть проект...", self)
        open_project_action.setToolTip("Восстановить все слои из сохранённого проекта")
        open_project_action.triggered.connect(self.open_project_dialog)
        toolbar.addAction(open_project_action)

        # Добавляем разделитель
        toolbar.addSeparator()

        # Вернуть фокус в центр рабочего поля, не меняя масштаб
        center_action = QAction("⌖ В центр", self)
        center_action.setToolTip("Вернуть фокус в центр рабочего поля (0,0), не меняя масштаб (клавиша C)")
        center_action.setShortcut("C")
        center_action.triggered.connect(self.center_view_on_origin)
        toolbar.addAction(center_action)

        # Показать все загруженные линии -- спасение, если вид 'улетел'
        fit_content_action = QAction("⤢ По линиям", self)
        fit_content_action.setToolTip("Показать все загруженные линии целиком (клавиша F)")
        fit_content_action.setShortcut("F")
        fit_content_action.triggered.connect(self.fit_view_to_content)
        toolbar.addAction(fit_content_action)

        # Размер рабочего поля + сброс вида к нему (устойчивый ориентир, как в Gcode_Terminal)
        toolbar.addWidget(QLabel(" Поле мм:"))
        self.field_w_spin = QSpinBox()
        self.field_w_spin.setRange(10, 20000)
        self.field_w_spin.setValue(int(self.field_w_mm))
        self.field_w_spin.setSuffix(" Ш")
        self.field_w_spin.setToolTip("Ширина рабочего поля машины (мм)")
        self.field_w_spin.valueChanged.connect(self._on_field_size_changed)
        toolbar.addWidget(self.field_w_spin)

        self.field_h_spin = QSpinBox()
        self.field_h_spin.setRange(10, 20000)
        self.field_h_spin.setValue(int(self.field_h_mm))
        self.field_h_spin.setSuffix(" В")
        self.field_h_spin.setToolTip("Высота рабочего поля машины (мм)")
        self.field_h_spin.valueChanged.connect(self._on_field_size_changed)
        toolbar.addWidget(self.field_h_spin)

        fit_field_action = QAction("⤢ По размеру поля", self)
        fit_field_action.setToolTip("Показать всё рабочее поле целиком (клавиша Home)")
        fit_field_action.setShortcut("Home")
        fit_field_action.triggered.connect(self.fit_view_to_field)
        toolbar.addAction(fit_field_action)

        field_color_action = QAction("🎨 Цвет поля", self)
        field_color_action.setToolTip("Цвет фона рабочего поля (замена слою-заливке)")
        field_color_action.triggered.connect(self.pick_field_bg_color)
        toolbar.addAction(field_color_action)

        # Добавляем разделитель
        toolbar.addSeparator()
        
        # Добавляем действия для сетки
        self.grid_action = QAction("Сетка", self)
        self.grid_action.setCheckable(True)
        self.grid_action.setChecked(True)
        self.grid_action.triggered.connect(self.toggle_grid)
        toolbar.addAction(self.grid_action)
        
        self.grid_step_action = QAction("Шаг сетки", self)
        self.grid_step_action.triggered.connect(self.set_grid_step)
        toolbar.addAction(self.grid_step_action)
        
        self.grid_opacity_action = QAction("Настройки сетки", self)  # Было "Прозрачность сетки"
        self.grid_opacity_action.triggered.connect(self.set_grid_opacity)
        toolbar.addAction(self.grid_opacity_action)

    def load_grid_settings(self):
        """Загрузка настроек сетки"""
        try:
            visible = self.settings.value('grid/visible', True, type=bool)
            step = self.settings.value('grid/step', 10.0, type=float)
            opacity = self.settings.value('grid/opacity', 0.3, type=float)
            color_name = self.settings.value('grid/color', '#808080')
            color = QColor(color_name)
            
            self.grid.setVisible(visible)
            self.grid.set_grid_params(step=step, opacity=opacity, color=color)
            self.grid_action.setChecked(visible)
            
            logger.debug(f"Loaded grid settings: visible={visible}, step={step}, opacity={opacity}, color={color_name}")
        except Exception as e:
            logger.error(f"Error loading grid settings: {e}")

    def save_grid_settings(self):
        """Сохранение настроек сетки"""
        try:
            self.settings.setValue('grid/visible', self.grid.isVisible())
            self.settings.setValue('grid/step', self.grid.grid_step)
            self.settings.setValue('grid/opacity', self.grid.grid_opacity)
            self.settings.setValue('grid/color', self.grid.grid_color.name())
            logger.debug("Grid settings saved")
        except Exception as e:
            logger.error(f"Error saving grid settings: {e}")

    def _update_field_visual(self):
        """(Пере)рисовать границу рабочего поля -- видимый стабильный ориентир,
        не зависящий от масштаба/загруженного файла (см. GCodeViewer в Gcode_Terminal)."""
        hw, hh = self.field_w_mm / 2.0, self.field_h_mm / 2.0
        rect = QRectF(-hw, -hh, self.field_w_mm, self.field_h_mm)
        if self.field_rect_item is None:
            pen = QPen(QColor(90, 90, 140))
            pen.setCosmetic(True)
            self.field_rect_item = self.scene.addRect(rect, pen)
            self.field_rect_item.setZValue(-500)  # над сеткой, под содержимым слоёв
        else:
            self.field_rect_item.setRect(rect)

    def _on_field_size_changed(self):
        self.field_w_mm = float(self.field_w_spin.value())
        self.field_h_mm = float(self.field_h_spin.value())
        self._update_field_visual()
        self._update_scene_bounds()
        self.save_field_settings()
        logger.debug(f"Field size set to {self.field_w_mm}x{self.field_h_mm} mm")

    def load_field_settings(self):
        """Загрузка размера и цвета рабочего поля"""
        try:
            self.field_w_mm = self.settings.value('field/width', 400.0, type=float)
            self.field_h_mm = self.settings.value('field/height', 400.0, type=float)
            self.field_bg_color = QColor(self.settings.value('field/bg_color', '#ffffff'))
            self.field_w_spin.blockSignals(True)
            self.field_h_spin.blockSignals(True)
            self.field_w_spin.setValue(int(self.field_w_mm))
            self.field_h_spin.setValue(int(self.field_h_mm))
            self.field_w_spin.blockSignals(False)
            self.field_h_spin.blockSignals(False)
            self.scene.setBackgroundBrush(QBrush(self.field_bg_color))
            self._update_field_visual()
            logger.debug(f"Loaded field settings: {self.field_w_mm}x{self.field_h_mm}, bg={self.field_bg_color.name()}")
        except Exception as e:
            logger.error(f"Error loading field settings: {e}")

    def save_field_settings(self):
        """Сохранение размера и цвета рабочего поля"""
        try:
            self.settings.setValue('field/width', self.field_w_mm)
            self.settings.setValue('field/height', self.field_h_mm)
            self.settings.setValue('field/bg_color', self.field_bg_color.name())
            logger.debug("Field settings saved")
        except Exception as e:
            logger.error(f"Error saving field settings: {e}")

    def pick_field_bg_color(self):
        """Цвет фона рабочего поля -- замена отдельного слоя-заливки."""
        color = QColorDialog.getColor(self.field_bg_color, self, "Цвет рабочего поля")
        if color.isValid():
            self.field_bg_color = color
            self.scene.setBackgroundBrush(QBrush(color))
            self.save_field_settings()
            logger.debug(f"Field background color set to {color.name()}")

    def fit_view_to_field(self):
        """Показать рабочее поле целиком -- устойчивый 'домашний' вид,
        не зависящий от того, что и насколько загружено (как в Gcode_Terminal)."""
        hw, hh = self.field_w_mm / 2.0, self.field_h_mm / 2.0
        margin = max(self.field_w_mm, self.field_h_mm) * 0.02
        rect = QRectF(-hw - margin, -hh - margin, self.field_w_mm + 2 * margin, self.field_h_mm + 2 * margin)
        self.view.fitInView(rect, Qt.KeepAspectRatio)
        self.grid.set_scale(self.view.transform().m11())
        logger.debug("View fit to work field")

    def toggle_grid(self, checked):
        """Включение/выключение сетки"""
        self.grid.setVisible(checked)
        self.save_grid_settings()
        logger.debug(f"Grid visibility set to {checked}")

    def toggle_polygon_selection(self, checked):
        """Включение/выключение режима выделения полигона"""
        if checked:
            self.start_polygon_selection()
        else:
            self.cancel_polygon_selection()

    def set_grid_step(self):
        """Настройка шага сетки"""
        step, ok = QInputDialog.getDouble(
            self, 'Шаг сетки',
            'Введите размер шага (мм):',
            self.grid.grid_step, 1, 100, 1
        )
        if ok:
            self.grid.set_grid_params(step=step)
            self.save_grid_settings()
            logger.debug(f"Grid step set to {step}")

    def set_grid_opacity(self):
        """Настройка параметров сетки"""
        try:
            dialog = GridSettingsDialog(
                self,
                opacity=self.grid.grid_opacity,
                color=self.grid.grid_color
            )
            
            if dialog.exec_() == GridSettingsDialog.Accepted:
                settings = dialog.get_settings()
                self.grid.set_grid_params(
                    opacity=settings['opacity'],
                    color=settings['color']
                )
                self.save_grid_settings()
                logger.debug(f"Grid settings updated: opacity={settings['opacity']}, color={settings['color'].name()}")
        except Exception as e:
            logger.error(f"Error in grid settings: {e}")

    def create_new_layer(self):
        dialog = NewLayerDialog(self)
        if dialog.exec_() == NewLayerDialog.Accepted:
            layer_info = dialog.get_layer_info()
            self.add_layer(layer_info['name'], layer_info['type'])

    def add_layer(self, name: str, layer_type: LayerType):
        """Создание и добавление нового слоя"""
        try:
            # Создаем слой в зависимости от типа
            if layer_type == LayerType.GCODE:
                from ..models.gcode_layer import GCodeLayer
                layer = GCodeLayer(name, self.scene)
            elif layer_type == LayerType.IMAGE:
                from ..models.image_layer import ImageLayer
                layer = ImageLayer(name, self.scene)
            elif layer_type == LayerType.SVG:
                from ..models.svg_layer import SvgLayer
                layer = SvgLayer(name, self.scene)
            else:
                logger.error(f"Layer type not implemented: {layer_type}")
                return None

            # Добавляем слой в список
            self.layers.append(layer)
            
            # Создаем элемент списка
            item = QListWidgetItem()
            item.setData(Qt.UserRole, layer)  # Сохраняем ссылку на слой
            self.update_layer_item_text(item, layer)
            self.layer_list.addItem(item)
            self.update_layers_z_values()
            
            logger.debug(f"Added new layer: {name} of type {layer_type}")
            return layer

        except Exception as e:
            logger.error(f"Error creating layer: {e}")
            return None

    def start_polygon_selection(self):
        try:
            self.selection_mode = True
            self.selection_points = []
            if self.selection_path_item:
                self.scene.removeItem(self.selection_path_item)
                self.selection_path_item = None
            pen = QPen(Qt.red, 0)
            pen.setStyle(Qt.DashLine)
            self.selection_path_item = self.scene.addPath(QPainterPath(), pen)
            self.selection_path_item.setZValue(10000)
            self._prev_drag_mode = self.view.dragMode()
            self.view.setDragMode(QGraphicsView.NoDrag)
            self.view.setCursor(Qt.CrossCursor)
        except Exception as e:
            logger.error(f"Error starting polygon selection: {e}", exc_info=True)
            self.cancel_polygon_selection()

    def cancel_polygon_selection(self):
        try:
            self.selection_mode = False
            self.selection_points = []
            if self.selection_path_item:
                self.scene.removeItem(self.selection_path_item)
                self.selection_path_item = None
            if hasattr(self, 'polygon_action'):
                self.polygon_action.setChecked(False)
            if self._prev_drag_mode is not None:
                self.view.setDragMode(self._prev_drag_mode)
                self._prev_drag_mode = None
            self.view.setCursor(Qt.ArrowCursor)
        except Exception as e:
            logger.error(f"Error canceling polygon selection: {e}", exc_info=True)

    def create_layer_dock(self):
        dock = QDockWidget("Слои", self)
        dock.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)
        
        # Создаем основной виджет
        main_widget = QWidget()
        main_layout = QVBoxLayout(main_widget)
        
        # Создаем список слоев
        self.layer_list = QListWidget()
        self.layer_list.setSelectionMode(QListWidget.ExtendedSelection)
        main_layout.addWidget(self.layer_list)
        
        # Создаем контейнер для виджета слоя
        self.layer_widget_container = QWidget()
        self.layer_widget_layout = QVBoxLayout(self.layer_widget_container)
        main_layout.addWidget(self.layer_widget_container)
        
        dock.setWidget(main_widget)
        self.addDockWidget(Qt.RightDockWidgetArea, dock)
        
        # Подключаем обработчик выбора слоя
        self.layer_list.currentItemChanged.connect(self.on_layer_selected)

    def on_layer_selected(self, current, previous):
        """Обработчик выбора слоя в списке"""
        try:
            # Очищаем контейнер
            while self.layer_widget_layout.count():
                item = self.layer_widget_layout.takeAt(0)
                widget = item.widget()
                if widget:
                    widget.deleteLater()
            
            if current:
                layer = current.data(Qt.UserRole)
                if layer:
                    # Создаем виджет для выбранного слоя
                    layer_widget = LayerWidget(layer)
                    self.layer_widget_layout.addWidget(layer_widget)
                    logger.debug(f"Created widget for layer: {layer.name}")
        except Exception as e:
            logger.error(f"Error in layer selection: {e}", exc_info=True)

    def eventFilter(self, obj, event):
        try:
            if self.selection_mode and event.type() == QEvent.GraphicsSceneMousePress:
                if event.button() == Qt.LeftButton:
                    pos = event.scenePos()
                    # Проверяем замыкание полигона
                    if self.selection_points and len(self.selection_points) >= 3:
                        first = self.selection_points[0]
                        if (pos - first).manhattanLength() < 10:
                            self.finalize_polygon_selection()
                            return True
                    self.selection_points.append(pos)
                    self.update_selection_path()
                    return True
                elif event.button() == Qt.RightButton:
                    self.cancel_polygon_selection()
                    return True
        except Exception as e:
            logger.error(f"Error in event filter: {e}", exc_info=True)
        return super().eventFilter(obj, event)

    def update_selection_path(self):
        if not self.selection_path_item or not self.selection_points:
            return
        path = QPainterPath()
        path.moveTo(self.selection_points[0])
        for p in self.selection_points[1:]:
            path.lineTo(p)
        self.selection_path_item.setPath(path)

    def finalize_polygon_selection(self):
        if len(self.selection_points) < 3:
            self.cancel_polygon_selection()
            return
        polygon = QPolygonF(self.selection_points)
        self.save_points_in_polygon(polygon)
        self.cancel_polygon_selection()

    def load_file_to_layer(self):
        try:
            layer = self.get_current_layer()
            if not layer:
                return

            if layer.type == LayerType.GCODE:
                filename, _ = QFileDialog.getOpenFileName(self, "Загрузить G-code", "", "G-code files (*.gcode);;All files (*.*)")
                if not filename:
                    return
                # OPT-3: load_file() returns immediately; _on_async_load_finished
                # is called in the GUI thread via load_finished signal when done.
                # UniqueConnection prevents double-connect on repeated loads.
                layer.load_finished.connect(
                    lambda ok, _l=layer: self._on_async_load_finished(ok, _l),
                    Qt.UniqueConnection
                )
                layer.load_file(filename)

            elif layer.type == LayerType.SVG:
                filename, _ = QFileDialog.getOpenFileName(self, "Загрузить SVG", "", "SVG files (*.svg);;All files (*.*)")
                if not filename:
                    return
                try:
                    from ..models.svg_utils import get_svg_bbox
                    bbox = get_svg_bbox(filename)
                except Exception as e:
                    logger.error(f"Error reading SVG bbox: {e}", exc_info=True)
                    bbox = None
                native_size = (bbox[2] - bbox[0], bbox[3] - bbox[1]) if bbox else (100.0, 100.0)
                size_dialog = SvgSizeDialog(self, native_size=native_size)
                if size_dialog.exec_() != SvgSizeDialog.Accepted:
                    return
                size = size_dialog.get_size()
                layer.load_finished.connect(
                    lambda ok, _l=layer: self._on_async_load_finished(ok, _l),
                    Qt.UniqueConnection
                )
                layer.load_file(filename, size['width_mm'], size['height_mm'])

            elif layer.type == LayerType.IMAGE:
                filename, _ = QFileDialog.getOpenFileName(self, "Загрузить изображение", "", "Images (*.png *.jpg *.jpeg *.bmp);;All files (*.*)")
                if not filename:
                    return
                size_dialog = ImageSizeDialog(self, filename)
                if size_dialog.exec_() != ImageSizeDialog.Accepted:
                    return
                size = size_dialog.get_size()
                success = layer.load_file(filename, size['width_mm'], size['height_mm'])
                if success:
                    self._fit_view_to_scene(layer)
                else:
                    logger.error(f"Ошибка загрузки файла {filename}")

        except Exception as e:
            logger.error(f"Error loading file: {e}", exc_info=True)

    def _on_async_load_finished(self, success: bool, layer):
        """Called in the GUI thread after an async G-code/SVG parse completes."""
        try:
            if success:
                self._fit_view_to_scene(layer)
            else:
                logger.error("Ошибка загрузки файла")
        except Exception as e:
            logger.error(f"Error in _on_async_load_finished: {e}", exc_info=True)

    def _content_bounding_rect(self):
        """Bounding rect of actual layer content only. scene.itemsBoundingRect()
        is unusable here -- GridItem reports a huge fixed rect (so it isn't
        culled while panning), which used to swamp this calculation and zoom
        the view out to almost nothing after every load."""
        rect = None
        for layer in self.layers:
            item = getattr(layer, 'current_item', None)
            if item is None:
                continue
            item_rect = item.mapRectToScene(item.boundingRect())
            rect = item_rect if rect is None else rect.united(item_rect)
        return rect

    def _fit_view_to_scene(self, layer):
        """Centre and scale view to fit loaded content; restore grid."""
        self._update_scene_bounds()
        grid_visible = self.grid.isVisible()
        self.view.resetTransform()
        rect = self._content_bounding_rect()
        if rect is None or rect.width() <= 1e-6 or rect.height() <= 1e-6:
            # degenerate/empty content -- fall back to the work field
            self.fit_view_to_field()
            self.grid.setVisible(grid_visible)
            current_item = self.get_current_layer_item()
            if current_item:
                self.update_layer_item_text(current_item, layer)
                self.on_layer_selected(current_item, None)
            return

        # small margin so content isn't flush against the viewport edge
        margin = max(rect.width(), rect.height()) * 0.05
        rect = rect.adjusted(-margin, -margin, margin, margin)
        self.view.centerOn(rect.center())
        scale = min(
            self.view.viewport().width()  / rect.width(),
            self.view.viewport().height() / rect.height()
        )
        scale = max(scale, 0.001)
        self.view.scale(scale, scale)
        self.grid.setVisible(grid_visible)
        self.grid.set_scale(scale)
        current_item = self.get_current_layer_item()
        if current_item:
            self.update_layer_item_text(current_item, layer)
            self.on_layer_selected(current_item, None)
        logger.debug(f"View scaled to: {scale}, grid visible: {grid_visible}")

    def save_points_in_polygon(self, polygon: QPolygonF):
        try:
            filename, _ = QFileDialog.getSaveFileName(
                self,
                "Сохранить отфильтрованный G-code",
                "",
                "G-code files (*.gcode);;All files (*.*)"
            )
            if not filename:
                return

            poly = np.array([[p.x(), p.y()] for p in polygon], dtype=np.float64)
            total = 0
            with open(filename, 'w', encoding='utf-8') as f:
                f.write("; Filtered G-code generated from selection\n")
                for layer in self.layers:
                    if not (getattr(layer, 'visible', True) and layer.type == LayerType.GCODE
                            and getattr(layer, 'current_item', None)):
                        continue
                    item = layer.current_item
                    pts = getattr(item, 'pts', None)
                    if pts is None or len(pts) == 0:
                        continue
                    # OPT-11: аффинное преобразование и проверка полигона -- numpy,
                    # без mapToScene на каждую точку
                    tr = item.sceneTransform()
                    xs = pts[:, 0] * tr.m11() + pts[:, 1] * tr.m21() + tr.dx()
                    ys = pts[:, 0] * tr.m12() + pts[:, 1] * tr.m22() + tr.dy()
                    idx = np.nonzero(_points_in_polygon(xs, ys, poly))[0]
                    trav = item.is_travel
                    for i in idx:
                        g = 'G0' if bool(trav[i]) else 'G1'
                        f.write(f"{g} X{pts[i, 0]:.3f} Y{-pts[i, 1]:.3f}\n")
                    total += len(idx)

            if total == 0:
                logger.debug("No points found inside polygon")
                QMessageBox.information(self, "Выделение",
                                        "Внутри полигона не найдено ни одной точки")
                return
            logger.debug(f"Saved {total} commands to {filename}")
        except Exception as e:
            logger.error(f"Error saving points in polygon: {e}", exc_info=True)

    def create_coordinates_widget(self):
        """Создание виджета для отображения координат"""
        coords_widget = QWidget()
        layout = QHBoxLayout(coords_widget)
        
        # Метка для координат
        self.coords_label = QLabel("Координаты:")
        layout.addWidget(self.coords_label)
        
        # Поле для отображения координат
        self.coords_display = QLineEdit()
        self.coords_display.setReadOnly(True)
        self.coords_display.setMinimumWidth(200)
        layout.addWidget(self.coords_display)
        
        # Кнопка копирования
        copy_button = QPushButton("Копировать")
        copy_button.clicked.connect(self.copy_coordinates)
        layout.addWidget(copy_button)
        
        # Добавляем виджет в тулбар
        coords_toolbar = QToolBar()
        coords_toolbar.addWidget(coords_widget)
        self.addToolBar(coords_toolbar)
        
    def update_coordinates(self, coords: str):
        """Обновление отображаемых координат"""
        self.coords_display.setText(coords)
        
    def copy_coordinates(self):
        """Копирование координат в буфер обмена"""
        if self.coords_display.text():
            QApplication.clipboard().setText(self.coords_display.text())

    def scale_view(self, factor):
        """Масштабирование вида и сетки -- с пределами, чтобы нельзя было
        'потерять' рисунок, уйдя в бесконечно мелкий или крупный масштаб"""
        try:
            current = self.view.transform().m11()
            min_scale, max_scale = self._zoom_limits()
            target = max(min_scale, min(current * factor, max_scale))
            if current <= 1e-12:
                return
            factor = target / current
            if abs(factor - 1.0) < 1e-9:
                return
            self.view.scale(factor, factor)
            current_scale = self.view.transform().m11()
            self.grid.set_scale(current_scale)
            logger.debug(f"View scaled to: {current_scale}")
        except Exception as e:
            logger.error(f"Error scaling view: {e}", exc_info=True)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        try:
            # Обновляем масштаб сетки при изменении размера окна
            current_scale = self.view.transform().m11()
            self.grid.set_scale(current_scale)
            logger.debug(f"Window resized, grid scale updated: {current_scale}")
        except Exception as e:
            logger.error(f"Error in resize event: {e}", exc_info=True)

    def get_current_layer(self):
        """Получить текущий выбранный слой"""
        selected_items = self.layer_list.selectedItems()
        if selected_items:
            return selected_items[0].data(Qt.UserRole)
        return None