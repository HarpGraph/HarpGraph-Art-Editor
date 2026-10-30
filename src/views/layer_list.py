# -*- coding: utf-8 -*-
"""Список слоёв как в Photoshop: в каждой строке глазик (видимость) и замок
(блокировка), клик по ним переключает слой без смены выбора."""
from PyQt5.QtWidgets import QListWidget, QStyledItemDelegate, QStyle, QStyleOptionViewItem, QApplication
from PyQt5.QtCore import Qt, QRect, QSize
from PyQt5.QtGui import QFont, QPalette

ICON_W = 28


class _LayerRowDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        return QSize(size.width() + 2 * ICON_W, max(size.height(), 26))

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
        painter.setOpacity(1.0 if visible else 0.5)
        painter.setFont(option.font)
        selected = bool(opt.state & QStyle.State_Selected)
        painter.setPen(opt.palette.color(QPalette.HighlightedText if selected else QPalette.Text))
        text_rect = rect.adjusted(2 * ICON_W + 4, 0, -4, 0)
        painter.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft,
                         painter.fontMetrics().elidedText(text, Qt.ElideRight, text_rect.width()))
        painter.restore()


class LayerListWidget(QListWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setItemDelegate(_LayerRowDelegate(self))
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)   # длинные имена обрезаются с «…»
        self.setMouseTracking(True)

    def _toggle_target(self, pos):
        item = self.itemAt(pos)
        if item is None:
            return None, None
        x = pos.x() - self.visualItemRect(item).left()
        if x < ICON_W:
            return item, 'visible'
        if x < 2 * ICON_W:
            return item, 'locked'
        return item, None

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            item, what = self._toggle_target(event.pos())
            layer = item.data(Qt.UserRole) if item is not None else None
            if layer is not None and what:
                setattr(layer, what, not getattr(layer, what))
                self.viewport().update()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        item, what = self._toggle_target(event.pos())
        if what:
            self.mousePressEvent(event)   # второй клик по иконке -- тоже переключение
            return
        super().mouseDoubleClickEvent(event)
