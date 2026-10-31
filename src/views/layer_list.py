# -*- coding: utf-8 -*-
"""Список слоёв как в Photoshop: в каждой строке слева глазик (видимость) и замок
(блокировка), справа стрелки вверх/вниз и крестик «удалить слой».
Клик по иконкам не меняет выбор слоя."""
from PyQt5.QtWidgets import QListWidget, QStyledItemDelegate, QStyle, QStyleOptionViewItem, QApplication
from PyQt5.QtCore import Qt, QRect, QSize, pyqtSignal
from PyQt5.QtGui import QFont, QPalette

ICON_W = 28
RIGHT_ICONS = 3   # ▲ ▼ ✕ справа


class _LayerRowDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        # ширина строки = ширина списка (а не длина текста): иначе при длинном пути
        # к файлу кнопки справа уезжают за край и их не видно
        size = super().sizeHint(option, index)
        widget = option.widget
        width = widget.viewport().width() if widget is not None else size.width()
        return QSize(width, max(size.height(), 26))

    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        text = opt.text
        opt.text = ''
        widget = opt.widget
        style = widget.style() if widget is not None else QApplication.style()
        style.drawControl(QStyle.CE_ItemViewItem, opt, painter, widget)

        layer = index.data(Qt.UserRole)
        rect = option.rect
        painter.save()
        icon_font = QFont(option.font)
        icon_font.setPointSize(max(option.font.pointSize(), 9) + 2)
        painter.setFont(icon_font)
        eye_rect = QRect(rect.left(), rect.top(), ICON_W, rect.height())
        lock_rect = QRect(rect.left() + ICON_W, rect.top(), ICON_W, rect.height())
        visible = bool(getattr(layer, 'visible', True))
        locked = bool(getattr(layer, 'locked', False))
        painter.setOpacity(1.0 if visible else 0.25)
        painter.drawText(eye_rect, Qt.AlignCenter, "\U0001F441")
        painter.setOpacity(1.0 if locked else 0.25)
        painter.drawText(lock_rect, Qt.AlignCenter, "\U0001F512" if locked else "\U0001F513")

        # справа: ▲ ▼ ✕ (стрелка бледнеет, если слой уже крайний); привязка к ширине списка
        vp_w = widget.viewport().width() if widget is not None else rect.width()
        right = rect.left() + vp_w - 1
        count = index.model().rowCount()
        row = index.row()
        for slot, (glyph, enabled) in enumerate((("▲", row > 0),
                                                 ("▼", row < count - 1),
                                                 ("✕", True))):
            r = QRect(right + 1 - (RIGHT_ICONS - slot) * ICON_W, rect.top(), ICON_W, rect.height())
            painter.setOpacity(1.0 if enabled else 0.2)
            painter.drawText(r, Qt.AlignCenter, glyph)

        painter.setOpacity(1.0 if visible else 0.5)
        painter.setFont(option.font)
        selected = bool(opt.state & QStyle.State_Selected)
        painter.setPen(opt.palette.color(QPalette.HighlightedText if selected else QPalette.Text))
        text_rect = QRect(rect.left() + 2 * ICON_W + 4, rect.top(),
                          vp_w - (2 + RIGHT_ICONS) * ICON_W - 8, rect.height())
        painter.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft,
                         painter.fontMetrics().elidedText(text, Qt.ElideRight, text_rect.width()))
        painter.restore()


class LayerListWidget(QListWidget):
    moveRequested = pyqtSignal(int, int)   # строка, направление (-1 выше, +1 ниже)
    deleteRequested = pyqtSignal(int)      # строка

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setItemDelegate(_LayerRowDelegate(self))
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)   # длинные имена обрезаются с «…»
        self.setMouseTracking(True)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.scheduleDelayedItemsLayout()   # ширина строк = ширина списка

    def _toggle_target(self, pos):
        item = self.itemAt(pos)
        if item is None:
            return None, None
        rect = self.visualItemRect(item)
        x = pos.x() - rect.left()
        if x < ICON_W:
            return item, 'visible'
        if x < 2 * ICON_W:
            return item, 'locked'
        from_right = self.viewport().width() - pos.x()
        if from_right < ICON_W:
            return item, 'delete'
        if from_right < 2 * ICON_W:
            return item, 'down'
        if from_right < 3 * ICON_W:
            return item, 'up'
        return item, None

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            item, what = self._toggle_target(event.pos())
            layer = item.data(Qt.UserRole) if item is not None else None
            if layer is not None and what:
                if what in ('visible', 'locked'):
                    setattr(layer, what, not getattr(layer, what))
                    self.viewport().update()
                else:
                    row = self.row(item)
                    if what == 'delete':
                        self.deleteRequested.emit(row)
                    else:
                        self.moveRequested.emit(row, -1 if what == 'up' else 1)
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        item, what = self._toggle_target(event.pos())
        if what in ('visible', 'locked', 'up', 'down'):
            self.mousePressEvent(event)   # второй клик по иконке/стрелке -- ещё одно действие
            return
        if what:
            event.accept()   # двойной клик по крестику не должен срабатывать дважды
            return
        super().mouseDoubleClickEvent(event)
