# -*- coding: utf-8 -*-
"""G-code scene item built for MILLIONS of vertices.

Design (OPT-10..OPT-13):
  * All vertex data lives in numpy arrays (pts/is_travel/linenos) -- no
    per-point Python tuples/strings, so a 5M-line file stays in ~100 MB.
  * QPainterPath objects are built in one shot by streaming a binary
    buffer through QDataStream (Qt's documented stream format: element
    count, then (type:i32, x:f64, y:f64) records, then cStart+fillRule).
    This replaces the per-vertex lineTo() Python loop (seconds -> ms).
  * The polyline is split into CHUNK-sized child items.  Qt's scene index
    culls off-screen chunks, so panning/zooming while zoomed-in only
    repaints the visible pieces ("зумить частями").
  * Each chunk pre-builds decimated copies of itself (1/8, 1/64 of the
    vertices).  When zoomed far out -- where segments are subpixel anyway --
    paint() draws the coarse copy, keeping full-view redraws fast.

The same numpy arrays are the planned substrate for future per-layer
operations (offset/scale to the work field, jitter, smoothing, thinning):
they all become vectorised array transforms feeding _rebuild_chunks().
"""
import struct
import time
import logging

import numpy as np
from PyQt5.QtWidgets import (QGraphicsItem, QGraphicsItemGroup, QGraphicsView,
                             QMenu, QToolTip, QApplication)
from PyQt5.QtGui import QPainterPath, QPen, QColor, QTransform
from PyQt5.QtCore import (Qt, QRectF, QPointF, QTimer, QByteArray,
                          QDataStream)

logger = logging.getLogger(__name__)

# vertices per chunk item (culling granularity)
CHUNK_VERTICES = 50000
# decimation levels prepared per chunk (1 = full detail)
LOD_LEVELS = (1, 8, 64)
# a segment is considered subpixel below this many screen pixels
LOD_PIXEL_THRESHOLD = 0.3

# Qt5 QPainterPath stream record: element type (Move=0/Line=1), x, y
_QPATH_DTYPE = np.dtype([('t', '>i4'), ('x', '>f8'), ('y', '>f8')])


# ---------------------------------------------------------------------------
# Fast numpy -> QPainterPath builders
# ---------------------------------------------------------------------------

def _records_to_qpath(rec) -> QPainterPath:
    """Stream pre-packed element records into a QPainterPath in C++ speed."""
    path = QPainterPath()
    n = len(rec)
    if n == 0:
        return path
    # count (i32) + records + cStart (i32) + fillRule (i32, OddEven=0)
    data = struct.pack('>i', n) + rec.tobytes() + struct.pack('>ii', 0, 0)
    stream = QDataStream(QByteArray(data))
    stream >> path
    return path


def _spans_to_records(pts, a, b):
    """Element records for straight spans a[k] -> b[k] (vertex indices).

    Consecutive spans that share an endpoint continue the subpath; a MoveTo
    is inserted only where the chain breaks.  Fully vectorised.
    """
    m = len(a)
    if m == 0:
        return np.empty(0, dtype=_QPATH_DTYPE)
    new_start = np.empty(m, dtype=bool)
    new_start[0] = True
    np.not_equal(a[1:], b[:-1], out=new_start[1:])
    cum_moves = np.cumsum(new_start)
    line_pos = np.arange(m, dtype=np.int64) + cum_moves   # record slot of LineTo k
    rec = np.empty(m + int(cum_moves[-1]), dtype=_QPATH_DTYPE)
    rec['t'][line_pos] = 1
    rec['x'][line_pos] = pts[b, 0]
    rec['y'][line_pos] = pts[b, 1]
    move_pos = line_pos[new_start] - 1
    starts = a[new_start]
    rec['t'][move_pos] = 0
    rec['x'][move_pos] = pts[starts, 0]
    rec['y'][move_pos] = pts[starts, 1]
    return rec


def _sel_to_paths(pts, trav_cum, sel):
    """Build (draw_path, travel_path) from a sorted selected-vertex array.

    A span between neighbouring selected vertices is drawn as travel only
    when ALL skipped segments are travel; sel always contains draw/travel
    boundary vertices, so spans are homogeneous.
    """
    if len(sel) < 2:
        return QPainterPath(), QPainterPath()
    a, b = sel[:-1], sel[1:]
    n_trav = trav_cum[b] - trav_cum[a]
    trav_mask = n_trav == (b - a)
    draw_mask = ~trav_mask
    draw = _records_to_qpath(_spans_to_records(pts, a[draw_mask], b[draw_mask]))
    trav = _records_to_qpath(_spans_to_records(pts, a[trav_mask], b[trav_mask]))
    return draw, trav


def _level_selection(n, level, boundaries):
    """Vertex subset for a decimation level: every `level`-th vertex plus
    the last one plus every draw/travel boundary (keeps strokes honest)."""
    if level <= 1:
        return np.arange(n, dtype=np.int64)
    parts = [np.arange(0, n, level, dtype=np.int64),
             np.array([n - 1], dtype=np.int64)]
    if len(boundaries):
        parts.append(boundaries)
    return np.unique(np.concatenate(parts))


def extract_segment_runs(pts, is_travel, mask):
    """Вершины и флаги холостого хода для выделенных линий (отрезков).

    Подряд идущие выделенные линии образуют серию; перед каждой серией --
    холостой переезд (первая вершина серии помечается холостой). Возвращает
    (индексы вершин, is_travel) -- векторно, без Python-циклов по точкам.
    """
    idx = np.flatnonzero(mask)
    if not len(idx):
        return np.empty(0, dtype=np.int64), np.empty(0, dtype=bool)
    new_run = np.concatenate(([True], idx[1:] != idx[:-1] + 1))
    starts = idx[new_run]
    ends = idx[np.concatenate((new_run[1:], [True]))]
    counts = ends - starts + 2
    first_pos = np.cumsum(counts) - counts
    vidx = np.repeat(starts, counts) + (np.arange(int(counts.sum())) - np.repeat(first_pos, counts))
    trav = np.asarray(is_travel)[vidx].astype(bool)
    trav[first_pos] = True
    return vidx.astype(np.int64), trav


# ---------------------------------------------------------------------------
# Chunk child item (OPT-12: culling + per-chunk LOD)
# ---------------------------------------------------------------------------

class _GCodeChunkItem(QGraphicsItem):
    """One slice of the polyline with pre-built LOD paths."""

    def __init__(self, parent, rect, level_paths, avg_seg):
        super().__init__(parent)
        self._rect = rect
        # {level: (draw_path, travel_path)} -- levels sorted fine -> coarse
        self._level_paths = level_paths
        self._avg_seg = max(avg_seg, 1e-9)
        self.setFlag(QGraphicsItem.ItemUsesExtendedStyleOption)

    def boundingRect(self):
        return self._rect

    def paint(self, painter, option, widget):
        group = self.parentItem()
        if group is None:
            return
        lod = option.levelOfDetailFromTransform(painter.worldTransform())
        seg_px = self._avg_seg * lod
        chosen = None
        for level in LOD_LEVELS:
            if level in self._level_paths and seg_px * level >= LOD_PIXEL_THRESHOLD:
                chosen = level
                break
        if chosen is None:
            chosen = max(self._level_paths.keys())
        draw_path, travel_path = self._level_paths[chosen]
        if not draw_path.isEmpty():
            painter.setPen(group.line_pen)
            painter.drawPath(draw_path)
        if group.show_travel and not travel_path.isEmpty():
            # dashed pens are expensive -- use solid on coarse levels
            painter.setPen(group.travel_pen if chosen == 1 else group.travel_pen_solid)
            painter.drawPath(travel_path)


# ---------------------------------------------------------------------------
# GCodeGraphicsItem
# ---------------------------------------------------------------------------

class GCodeGraphicsItem(QGraphicsItemGroup):
    def __init__(self, parent=None):
        super().__init__(parent)
        # numpy data model (display coords: Y already inverted)
        self.pts = np.empty((0, 2), dtype=np.float32)
        self.is_travel = np.empty(0, dtype=bool)
        self.linenos = np.empty(0, dtype=np.int32)
        self._trav_cum = np.empty(0, dtype=np.int64)     # cumsum(is_travel)
        self._boundaries = np.empty(0, dtype=np.int64)   # draw/travel switch vertices
        self._avg_seg = 1.0

        self.render_mode = 'lines'
        self.line_color = QColor(Qt.black)
        self.point_color = QColor(Qt.red)
        self.travel_color = QColor(170, 102, 204)
        self.show_travel = True
        self.layer_ref = None
        self.setFlag(QGraphicsItem.ItemIsMovable)
        self.setFlag(QGraphicsItem.ItemIsSelectable)
        self.setFlag(QGraphicsItem.ItemUsesExtendedStyleOption)  # exposedRect in paint()
        self.setAcceptHoverEvents(True)
        self.initial_pos = None
        self._main_window = None          # OPT-8: cached main window ref
        self._hovered_idx = None          # индекс вершины под курсором
        self._active = True               # слой, с которым сейчас работают (см. set_active)
        self._seg_sel = None              # bool-маска выделенных линий (отрезков), len = N-1
        self._seg_anchor = None           # последняя выбранная линия (для Shift-диапазона)
        self._seg_item = None             # подсветка выделенных линий
        self._selected_idx = None         # вершина, выбранная левой кнопкой
        self._bounding_rect = QRectF()
        self._chunks = []                 # OPT-12 chunk children
        # OPT-13: lazy sorted-by-X index for fast hover lookups
        self._xorder = None
        self._xsorted = None
        self._trim_overlay_item = None      # colour-tint preview overlay
        self._trim_kept_draw_item = None    # 'hide removed' preview -- kept draw strokes
        self._trim_kept_travel_item = None  # 'hide removed' preview -- kept travel strokes
        self._trim_range = None             # (start, end) вершин в предпросмотре обрезки
        self._trim_hide = True
        self._trim_color = QColor(90, 90, 90)
        self._trim_opacity = 0.6
        self._rebuild_pens()

    # ------------------------------------------------------------------
    # Pens (shared by all chunks)
    # ------------------------------------------------------------------

    def _rebuild_pens(self):
        self.line_pen = QPen(self.line_color, 0)
        self.line_pen.setCosmetic(True)
        self.travel_pen = QPen(self.travel_color, 0)
        self.travel_pen.setCosmetic(True)
        self.travel_pen.setStyle(Qt.DashLine)
        self.travel_pen_solid = QPen(self.travel_color, 0)
        self.travel_pen_solid.setCosmetic(True)

    # ------------------------------------------------------------------
    # OPT-8 helpers
    # ------------------------------------------------------------------

    def _get_main_window(self):
        """OPT-8: return main window, cache on first call."""
        if self._main_window is None and self.scene() and self.scene().views():
            self._main_window = self.scene().views()[0].window()
        return self._main_window

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    def point_count(self) -> int:
        return int(len(self.pts))

    def set_gcode_data(self, points, is_travel=None, linenos=None):
        """Load vertex arrays.  `points` is an (N,2) array (or list) of raw
        G-code coords (Y up); Y is inverted here for screen space."""
        try:
            pts = np.array(points, dtype=np.float32).reshape(-1, 2)
            pts[:, 1] *= -1.0                          # invert Y in-place
            n = len(pts)
            if is_travel is None:
                trav = np.zeros(n, dtype=bool)
            else:
                trav = np.asarray(is_travel, dtype=bool)
            if linenos is None:
                lns = np.arange(1, n + 1, dtype=np.int32)
            else:
                lns = np.asarray(linenos, dtype=np.int32)

            self.pts = pts
            self.is_travel = trav
            self.linenos = lns
            self._xorder = None
            self._xsorted = None

            t = trav.astype(np.int64)
            self._trav_cum = np.cumsum(t)
            if n > 2:
                self._boundaries = np.nonzero(t[1:-1] != t[2:])[0].astype(np.int64) + 1
            else:
                self._boundaries = np.empty(0, dtype=np.int64)
            if n > 1:
                seg = np.diff(pts[:: max(1, n // 100000)], axis=0)  # sampled
                lengths = np.hypot(seg[:, 0], seg[:, 1])
                self._avg_seg = float(lengths.mean()) if len(lengths) else 1.0
                if self._avg_seg <= 1e-9:
                    self._avg_seg = 1e-3
            else:
                self._avg_seg = 1.0

            # bounding rect from numpy -- no QGraphicsItem children needed
            if n:
                margin = 1.0
                x_min, y_min = float(pts[:, 0].min()), float(pts[:, 1].min())
                x_max, y_max = float(pts[:, 0].max()), float(pts[:, 1].max())
                self._bounding_rect = QRectF(
                    x_min - margin, y_min - margin,
                    (x_max - x_min) + 2 * margin,
                    (y_max - y_min) + 2 * margin,
                )
            else:
                self._bounding_rect = QRectF()

            self.clear_segment_selection()
            self.clear_trim_highlight()
            self._rebuild_chunks()

            self._hovered_idx = None
            self._selected_idx = None
            self.prepareGeometryChange()
            logger.debug(f'Loaded points: {n} in {len(self._chunks)} chunks')

        except Exception as e:
            logger.error(f'Error loading G-code: {e}', exc_info=True)

    def _rebuild_chunks(self):
        """OPT-12: split the polyline into chunk items with per-level paths."""
        for ch in self._chunks:
            if ch.scene():
                ch.scene().removeItem(ch)
            ch.setParentItem(None)
        self._chunks = []

        pts, trav_cum = self.pts, self._trav_cum
        n = len(pts)
        if n < 2:
            return

        # per-level global selections, sliced per chunk below
        selections = {lv: _level_selection(n, lv, self._boundaries)
                      for lv in LOD_LEVELS}
        max_gap = max(LOD_LEVELS)

        c0 = 0
        while c0 < n - 1:
            c1 = min(n - 1, c0 + CHUNK_VERTICES)
            level_paths = {}
            for lv, sel in selections.items():
                lo = int(np.searchsorted(sel, c0, side='right'))
                hi = int(np.searchsorted(sel, c1, side='right'))
                part = sel[max(lo - 1, 0):hi]
                if len(part) >= 2:
                    level_paths[lv] = _sel_to_paths(pts, trav_cum, part)
                else:
                    level_paths[lv] = (QPainterPath(), QPainterPath())

            lo_v = max(0, c0 - max_gap)
            sl = pts[lo_v:c1 + 1]
            rect = QRectF(
                float(sl[:, 0].min()) - 1.0, float(sl[:, 1].min()) - 1.0,
                float(sl[:, 0].max() - sl[:, 0].min()) + 2.0,
                float(sl[:, 1].max() - sl[:, 1].min()) + 2.0,
            )
            chunk = _GCodeChunkItem(self, rect, level_paths, self._avg_seg)
            chunk.setVisible(self.render_mode in ('lines', 'both'))
            self._chunks.append(chunk)
            c0 = c1

    def update_points(self):
        """No per-point QGraphicsItems -- just reset hover state and repaint."""
        self._hovered_idx = None
        self.update()

    # ------------------------------------------------------------------
    # Geometry
    # ------------------------------------------------------------------

    def boundingRect(self):
        if not self._bounding_rect.isNull():
            return self._bounding_rect
        return super().boundingRect()

    # ------------------------------------------------------------------
    # Painting -- lines are drawn by chunk children; the group itself only
    # draws the point markers (filtered to the exposed region, OPT-11)
    # ------------------------------------------------------------------

    MAX_POINTS_DRAWN = 30000
    POINT_SIZE_PX = 3.0          # размер точки на экране не зависит от масштаба и наведения
    HOVER_TOL_PX = 5.0

    def _view_scale(self) -> float:
        """Экранных пикселей на единицу координат элемента."""
        mw = self._get_main_window()
        try:
            return max(mw.view.transform().m11() * abs(self.sceneTransform().m11()), 1e-9)
        except Exception:
            return 1.0

    def paint(self, painter, option, widget):
        if self.render_mode not in ('points', 'both', 'coords'):
            return
        n = len(self.pts)
        if n == 0:
            return
        lod = option.levelOfDetailFromTransform(painter.worldTransform())
        if lod < 0.15:
            return

        # only points inside the exposed rect (needs ItemUsesExtendedStyleOption)
        r = option.exposedRect
        xs, ys = self.pts[:, 0], self.pts[:, 1]
        mask = ((xs >= r.left() - 1) & (xs <= r.right() + 1) &
                (ys >= r.top() - 1) & (ys <= r.bottom() + 1))
        idx = np.nonzero(mask)[0]
        if len(idx) > self.MAX_POINTS_DRAWN:
            idx = idx[:: len(idx) // self.MAX_POINTS_DRAWN + 1]

        # при предпросмотре обрезки точки вне оставляемого диапазона
        # скрываются или тонируются так же, как линии
        removed_idx = np.empty(0, dtype=idx.dtype)
        if self._trim_range is not None:
            keep = (idx >= self._trim_range[0]) & (idx <= self._trim_range[1])
            removed_idx = idx[~keep]
            idx = idx[keep]

        painter.save()
        pts = self.pts
        # Точки рисуем в координатах экрана (без трансформации): при огромном
        # увеличении косметическое перо вырождалось в короткую горизонтальную линию
        tr = painter.worldTransform()
        painter.setWorldTransform(QTransform())

        def to_device(x, y):
            return (x * tr.m11() + y * tr.m21() + tr.dx(),
                    x * tr.m12() + y * tr.m22() + tr.dy())

        def draw(indices, color):
            if not len(indices):
                return
            pen = QPen(color, self.POINT_SIZE_PX)
            pen.setCapStyle(Qt.RoundCap)
            painter.setPen(pen)
            dx, dy = to_device(pts[indices, 0].astype(np.float64), pts[indices, 1].astype(np.float64))
            painter.drawPoints(*[QPointF(float(a), float(b)) for a, b in zip(dx, dy)])

        if len(removed_idx) and not self._trim_hide:
            tint = QColor(self._trim_color)
            tint.setAlphaF(max(0.0, min(1.0, self._trim_opacity)))
            draw(removed_idx, tint)
        draw(idx, self.point_color)

        for idx_, color_, extra in ((self._selected_idx, QColor(255, 200, 0), True),
                                    (self._hovered_idx, self.point_color.lighter(170), False)):
            if idx_ is None or idx_ >= n or not self._in_kept_range(idx_):
                continue
            hx, hy = to_device(float(pts[idx_, 0]), float(pts[idx_, 1]))
            if extra:
                # выбранная точка -- кольцо вокруг неизменной точки
                ring = QPen(color_, 2)
                painter.setPen(ring)
                painter.setBrush(Qt.NoBrush)
                painter.drawEllipse(QPointF(hx, hy), 7.0, 7.0)
            else:
                # наведение -- только цветом, размер не меняется
                hover_pen = QPen(color_, self.POINT_SIZE_PX)
                hover_pen.setCapStyle(Qt.RoundCap)
                painter.setPen(hover_pen)
                painter.drawPoint(QPointF(hx, hy))
        painter.restore()

    # ------------------------------------------------------------------
    # Hover / выбор точки (OPT-13: sorted-X index -> O(log n) nearest-point lookup)
    # ------------------------------------------------------------------

    def _in_kept_range(self, i: int) -> bool:
        return self._trim_range is None or self._trim_range[0] <= i <= self._trim_range[1]

    def _ensure_hover_index(self):
        if self._xorder is None and len(self.pts):
            self._xorder = np.argsort(self.pts[:, 0]).astype(np.int64)
            self._xsorted = np.ascontiguousarray(self.pts[self._xorder, 0])

    def _coords_text(self, i: int) -> str:
        """Координаты вершины так, как они записаны в файле (Y вверх)."""
        return f'X{float(self.pts[i, 0]):.3f} Y{-float(self.pts[i, 1]):.3f}'

    def _command_for_index(self, i: int) -> str:
        g = 'G0' if bool(self.is_travel[i]) else 'G1'
        return f'{g} {self._coords_text(i)}'

    def _points_visible(self) -> bool:
        return self.render_mode in ('points', 'both', 'coords') and len(self.pts) > 0

    def _hit_index(self, px: float, py: float):
        """Ближайшая вершина к точке (px, py) в координатах элемента; допуск -- в пикселях экрана."""
        if not self._points_visible():
            return None
        self._ensure_hover_index()
        tol = self.HOVER_TOL_PX / self._view_scale()
        lo = int(np.searchsorted(self._xsorted, px - tol))
        hi = int(np.searchsorted(self._xsorted, px + tol))
        if hi <= lo:
            return None
        cand = self._xorder[lo:hi]
        if self._trim_range is not None:
            cand = cand[(cand >= self._trim_range[0]) & (cand <= self._trim_range[1])]
        if not len(cand):
            return None
        dx = self.pts[cand, 0] - px
        dy = self.pts[cand, 1] - py
        d2 = dx * dx + dy * dy
        k = int(np.argmin(d2))
        return int(cand[k]) if d2[k] < tol * tol else None

    def _show_point_info(self, i, screen_pos=None):
        """Координаты точки: в строке состояния и подсказкой рядом с курсором."""
        mw = self._get_main_window()
        if i is None:
            QToolTip.hideText()
            if mw is not None:
                mw.statusBar().clearMessage()
            return
        text = self._coords_text(i)
        line = int(self.linenos[i]) if i < len(self.linenos) else None
        if mw is not None:
            mw.statusBar().showMessage(text + (f'   (строка {line})' if line else ''))
        if screen_pos is not None:
            QToolTip.showText(screen_pos, text + (f'\nстрока {line}' if line else ''))

    def hoverMoveEvent(self, event):
        if not self._active or not self._points_visible():
            return
        i = self._hit_index(event.pos().x(), event.pos().y())
        if i != self._hovered_idx:
            self._hovered_idx = i
            self.update()
        self._show_point_info(i, event.screenPos())

    def hoverLeaveEvent(self, event):
        self._hovered_idx = None
        QToolTip.hideText()
        self.update()

    def contextMenuEvent(self, event):
        """Правая кнопка на точке -- меню «Копировать координаты»."""
        try:
            if not self._active:
                event.ignore()   # неактивный слой не перехватывает меню нижних слоёв
                return
            mw = self._get_main_window()
            if mw is not None and (getattr(mw, 'selection_mode', False)
                                   or time.monotonic() - getattr(mw, '_polygon_cancel_ts', 0.0) < 0.7):
                event.ignore()   # правая кнопка только что отменила полигон
                return
            px, py = event.pos().x(), event.pos().y()
            i = self._hit_index(px, py)
            if i is None and self._selected_idx is not None and self._points_visible():
                i = self._selected_idx
            if i is None and self.selected_segment_count() == 0:
                seg = self._hit_segment(px, py) if self._lines_visible() else None
                if seg is not None:   # правый клик по линии выделяет её
                    self.select_segment(seg, 'replace')
            if i is None and self.selected_segment_count() == 0:
                event.ignore()
                return

            menu = QMenu()
            copy_xy = copy_cmd = None
            if i is not None:
                self._selected_idx = i
                self.update()
                coords = self._coords_text(i)
                copy_xy = menu.addAction(f'Копировать координаты  ({coords})')
                copy_cmd = menu.addAction(f'Копировать как G-code  ({self._command_for_index(i)})')
            n_sel = self.selected_segment_count()
            act_copy = act_cut = act_inv = act_clear = None
            if n_sel:
                if i is not None:
                    menu.addSeparator()
                act_copy = menu.addAction(f'Копировать выделенное ({n_sel} линий) в новый слой и файл…')
                act_cut = menu.addAction(f'Вырезать выделенное ({n_sel} линий) в новый слой и файл…')
                menu.addSeparator()
                act_inv = menu.addAction('Инвертировать выделение')
                act_clear = menu.addAction('Снять выделение')
            chosen = menu.exec_(event.screenPos())
            if chosen is None:
                pass
            elif chosen is copy_xy:
                QApplication.clipboard().setText(self._coords_text(i))
            elif chosen is copy_cmd:
                QApplication.clipboard().setText(self._command_for_index(i))
            elif chosen is act_copy and mw is not None:
                mw.extract_selected_lines(self.layer_ref, cut=False)
            elif chosen is act_cut and mw is not None:
                mw.extract_selected_lines(self.layer_ref, cut=True)
            elif chosen is act_inv:
                self.invert_segment_selection()
                self._announce_selection()
            elif chosen is act_clear:
                self.clear_segment_selection()
                self._announce_selection()
            event.accept()
        except Exception as e:
            logger.error(f'Error in context menu: {e}', exc_info=True)

    # ------------------------------------------------------------------
    # Выделение линий (штрихов): клик, Shift -- подряд, Ctrl -- выборочно,
    # двойной клик -- весь блок штрихов (между подъёмами инструмента).
    # Линия i -- отрезок между вершинами i и i+1.
    # ------------------------------------------------------------------

    def segment_count(self) -> int:
        return max(0, len(self.pts) - 1)

    def selected_segment_count(self) -> int:
        return int(self._seg_sel.sum()) if self._seg_sel is not None else 0

    def segment_mask(self):
        """Копия маски выделенных линий или None."""
        if self._seg_sel is None or not self._seg_sel.any():
            return None
        return self._seg_sel.copy()

    def _ensure_seg_sel(self):
        if self._seg_sel is None or len(self._seg_sel) != self.segment_count():
            self._seg_sel = np.zeros(self.segment_count(), dtype=bool)

    def _lines_visible(self) -> bool:
        return self.render_mode in ('lines', 'both') and self.segment_count() > 0

    def _hit_segment(self, px: float, py: float):
        """Ближайшая линия к точке (px, py); допуск -- в пикселях экрана."""
        if not self._lines_visible():
            return None
        tol = self.HOVER_TOL_PX / self._view_scale()
        pts = self.pts
        x0, x1 = pts[:-1, 0], pts[1:, 0]
        y0, y1 = pts[:-1, 1], pts[1:, 1]
        m = ((np.minimum(x0, x1) <= px + tol) & (np.maximum(x0, x1) >= px - tol) &
             (np.minimum(y0, y1) <= py + tol) & (np.maximum(y0, y1) >= py - tol))
        if not self.show_travel:
            m &= ~self.is_travel[1:]
        if self._trim_range is not None:
            lo, hi = self._trim_range
            m[:lo] = False
            m[hi:] = False
        cand = np.flatnonzero(m)
        if not len(cand):
            return None
        ax, ay = x0[cand].astype(np.float64), y0[cand].astype(np.float64)
        dx, dy = x1[cand] - ax, y1[cand] - ay
        l2 = dx * dx + dy * dy
        t = np.clip(((px - ax) * dx + (py - ay) * dy) / np.where(l2 > 0, l2, 1.0), 0.0, 1.0)
        d2 = (ax + t * dx - px) ** 2 + (ay + t * dy - py) ** 2
        k = int(np.argmin(d2))
        return int(cand[k]) if d2[k] <= tol * tol else None

    def block_range(self, i: int):
        """(first, last) линии блока: подряд идущие линии одного типа
        (штрихи между подъёмами инструмента либо холостые ходы)."""
        t = self.is_travel[1:]
        starts = np.flatnonzero(np.concatenate(([True], t[1:] != t[:-1])))
        k = int(np.searchsorted(starts, i, side='right')) - 1
        first = int(starts[k])
        last = int(starts[k + 1]) - 1 if k + 1 < len(starts) else len(t) - 1
        return first, last

    def select_segment(self, i: int, mode: str = 'replace'):
        """mode: 'replace' -- одна линия, 'range' (Shift) -- подряд от прошлой, 'toggle' (Ctrl) -- выборочно."""
        self._ensure_seg_sel()
        if mode == 'replace':
            self._seg_sel[:] = False
            self._seg_sel[i] = True
        elif mode == 'range':
            a = self._seg_anchor if self._seg_anchor is not None else i
            lo, hi = min(a, i), max(a, i)
            self._seg_sel[lo:hi + 1] = True
        else:
            self._seg_sel[i] = not self._seg_sel[i]
        self._seg_anchor = i
        self._update_seg_item()

    def select_block(self, i: int, add: bool = False):
        self._ensure_seg_sel()
        first, last = self.block_range(i)
        if not add:
            self._seg_sel[:] = False
        self._seg_sel[first:last + 1] = True
        self._seg_anchor = i
        self._update_seg_item()

    def invert_segment_selection(self):
        self._ensure_seg_sel()
        self._seg_sel = ~self._seg_sel
        self._update_seg_item()

    def set_segment_mask(self, mask):
        self._seg_sel = None if mask is None else np.asarray(mask, dtype=bool).copy()
        self._seg_anchor = None
        self._update_seg_item()

    def clear_segment_selection(self):
        self._seg_sel = None
        self._seg_anchor = None
        self._update_seg_item()

    def _update_seg_item(self):
        if self._seg_item is not None:
            self._remove_scene_item(self._seg_item)
            self._seg_item = None
        if self._seg_sel is None or not self._seg_sel.any():
            self.update()
            return
        from PyQt5.QtWidgets import QGraphicsPathItem
        idx = np.flatnonzero(self._seg_sel).astype(np.int64)
        path = _records_to_qpath(_spans_to_records(self.pts, idx, idx + 1))
        self._seg_item = QGraphicsPathItem(path, self)
        pen = QPen(QColor(255, 150, 0), 3)
        pen.setCosmetic(True)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        self._seg_item.setPen(pen)
        self._seg_item.setZValue(60)
        self._seg_item.setAcceptedMouseButtons(Qt.NoButton)
        self.update()

    def _announce_selection(self):
        mw = self._get_main_window()
        n = self.selected_segment_count()
        if mw is not None:
            mw.statusBar().showMessage(
                f'Выделено линий: {n}. Правая кнопка — копировать или вырезать в новый слой.' if n
                else 'Выделение снято')

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton and self._active and self._lines_visible():
            i = self._hit_segment(event.pos().x(), event.pos().y())
            if i is not None:
                self.select_block(i, add=bool(event.modifiers() & Qt.ControlModifier))
                self._announce_selection()
                event.accept()
                return
        super().mouseDoubleClickEvent(event)

    # ------------------------------------------------------------------
    # Visual trim preview (used by GcodeTrimDialog)
    # ------------------------------------------------------------------

    MAX_PREVIEW_SPANS = 400000   # decimate previews beyond this many segments

    def _preview_selection(self, start_idx, end_idx):
        """Selected-vertex array covering [start_idx, end_idx], decimated if
        the range is huge (preview stays visually identical, repaint stays fast)."""
        count = end_idx - start_idx + 1
        step = max(1, count // self.MAX_PREVIEW_SPANS)
        parts = [np.arange(start_idx, end_idx + 1, step, dtype=np.int64),
                 np.array([end_idx], dtype=np.int64)]
        if len(self._boundaries):
            b = self._boundaries
            parts.append(b[(b >= start_idx) & (b <= end_idx)])
        return np.unique(np.concatenate(parts))

    def set_trim_highlight(self, start_idx: int, end_idx: int, hide_removed: bool = True,
                            removed_color: QColor = None, removed_opacity: float = 0.6):
        """Preview a save-range before committing to it.

        hide_removed=True  -- segments outside [start_idx, end_idx] are not
                               drawn at all (fully invisible).
        hide_removed=False -- original strokes stay visible; a translucent
                               `removed_color`/`removed_opacity` overlay is
                               painted over everything outside the kept range.
        """
        n = len(self.pts)
        if n == 0:
            return
        start_idx = max(0, min(start_idx, n - 1))
        end_idx = max(start_idx, min(end_idx, n - 1))
        if removed_color is None:
            removed_color = QColor(90, 90, 90)

        self._clear_trim_items()
        self._trim_range = (start_idx, end_idx)
        self._trim_hide = hide_removed
        self._trim_color = QColor(removed_color)
        self._trim_opacity = removed_opacity
        self.update()
        show_lines = self.render_mode in ('lines', 'both')

        if hide_removed:
            for ch in self._chunks:
                ch.setVisible(False)
            sel = self._preview_selection(start_idx, end_idx)
            kept_draw, kept_travel = _sel_to_paths(self.pts, self._trav_cum, sel)

            from PyQt5.QtWidgets import QGraphicsPathItem
            self._trim_kept_draw_item = QGraphicsPathItem(kept_draw, self)
            self._trim_kept_draw_item.setPen(self.line_pen)
            self._trim_kept_draw_item.setZValue(50)
            self._trim_kept_draw_item.setVisible(show_lines)

            if not kept_travel.isEmpty():
                self._trim_kept_travel_item = QGraphicsPathItem(kept_travel, self)
                self._trim_kept_travel_item.setPen(self.travel_pen_solid)
                self._trim_kept_travel_item.setZValue(50)
            if self._trim_kept_travel_item and not (self.show_travel and show_lines):
                self._trim_kept_travel_item.setVisible(False)
        else:
            # switching back from a previous 'hide' preview must restore chunks
            self._restore_chunk_visibility()

            overlay = QPainterPath()
            if start_idx > 0:
                sel = self._preview_selection(0, start_idx)
                d, t = _sel_to_paths(self.pts, self._trav_cum, sel)
                overlay.addPath(d)
                overlay.addPath(t)
            if end_idx < n - 1:
                sel = self._preview_selection(end_idx, n - 1)
                d, t = _sel_to_paths(self.pts, self._trav_cum, sel)
                overlay.addPath(d)
                overlay.addPath(t)

            from PyQt5.QtWidgets import QGraphicsPathItem
            self._trim_overlay_item = QGraphicsPathItem(overlay, self)
            color = QColor(removed_color)
            color.setAlphaF(max(0.0, min(1.0, removed_opacity)))
            pen = QPen(color, 0)
            pen.setCosmetic(True)
            self._trim_overlay_item.setPen(pen)
            self._trim_overlay_item.setZValue(50)
            self._trim_overlay_item.setVisible(show_lines)

    def clear_trim_highlight(self):
        self._clear_trim_items()
        self._trim_range = None
        self._restore_chunk_visibility()
        self.update()

    def _restore_chunk_visibility(self):
        visible = self.render_mode in ('lines', 'both')
        for ch in self._chunks:
            ch.setVisible(visible)

    def _clear_trim_items(self):
        for attr in ('_trim_overlay_item', '_trim_kept_draw_item', '_trim_kept_travel_item'):
            item = getattr(self, attr)
            if item:
                self._remove_scene_item(item)
                setattr(self, attr, None)

    def _remove_scene_item(self, item):
        """Detach a child item and remove it from the scene so it doesn't
        leak as an orphaned top-level item on every rebuild."""
        item.setParentItem(None)
        if item.scene():
            item.scene().removeItem(item)

    # ------------------------------------------------------------------
    # Colour / visibility
    # ------------------------------------------------------------------

    def set_line_color(self, color: QColor):
        self.line_color = QColor(color)
        self._rebuild_pens()
        if self._trim_kept_draw_item:
            self._trim_kept_draw_item.setPen(self.line_pen)
        for ch in self._chunks:
            ch.update()
        self.update()

    def set_point_color(self, color: QColor):
        self.point_color = QColor(color)
        self.update()

    def set_render_mode(self, mode: str):
        if mode == 'coords':
            mode = 'both'   # режим «Точки с координатами» убран: координаты доступны всегда
        logger.debug(f'Render mode: {self.render_mode} -> {mode}')
        self.render_mode = mode
        trim_active = (self._trim_kept_draw_item is not None)
        if not trim_active:
            self._restore_chunk_visibility()
        # линии предпросмотра обрезки показываем только в режимах с линиями
        show_lines = mode in ('lines', 'both')
        if self._trim_kept_draw_item:
            self._trim_kept_draw_item.setVisible(show_lines)
        if self._trim_kept_travel_item:
            self._trim_kept_travel_item.setVisible(show_lines and self.show_travel)
        if self._trim_overlay_item:
            self._trim_overlay_item.setVisible(show_lines)
        self.update()

    def set_travel_visible(self, visible: bool):
        self.show_travel = visible
        for ch in self._chunks:
            ch.update()
        if self._trim_kept_travel_item:
            self._trim_kept_travel_item.setVisible(visible and self.render_mode in ('lines', 'both'))

    def set_travel_color(self, color: QColor):
        self.travel_color = QColor(color)
        self._rebuild_pens()
        if self._trim_kept_travel_item:
            self._trim_kept_travel_item.setPen(self.travel_pen_solid)
        for ch in self._chunks:
            ch.update()
        self.update()

    # ------------------------------------------------------------------
    # Lock / grid
    # ------------------------------------------------------------------

    def set_locked(self, locked: bool):
        self.setFlag(QGraphicsItem.ItemIsMovable, not locked)

    def set_active(self, active: bool):
        """Активный слой -- тот, с которым идёт работа: только у него есть
        наведение, выбор точки и меню; у остальных выбор сбрасывается."""
        self._active = bool(active)
        self.setAcceptHoverEvents(self._active)
        if not self._active:
            self._hovered_idx = None
            self._selected_idx = None
            self.clear_segment_selection()
        self.update()

    def set_grid_lock(self, locked: bool):
        try:
            if locked:
                main_window = self._get_main_window()
                grid_step = main_window.grid.grid_step
                pos = self.pos()
                new_x = round(pos.x() / grid_step) * grid_step
                new_y = round(pos.y() / grid_step) * grid_step
                self.setPos(new_x, new_y)
                self.setScale(1.0)
                logger.debug(f'Grid snap: ({new_x}, {new_y})')
            self.setFlag(QGraphicsItem.ItemIsMovable, not locked)
            logger.debug(f'Grid lock set to {locked}')
        except Exception as e:
            logger.error(f'Error setting grid lock: {e}', exc_info=True)

    # ------------------------------------------------------------------
    # Mouse events
    # ------------------------------------------------------------------

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            # левый клик: точка (кольцо) -- если попали в неё, иначе линия (штрих);
            # Shift -- линии подряд, Ctrl -- выборочно; клик мимо снимает выбор
            if self._active:
                px, py = event.pos().x(), event.pos().y()
                mods = event.modifiers()
                hit = self._hit_index(px, py) if self._points_visible() else None
                if hit is not None:
                    if hit != self._selected_idx:
                        self._selected_idx = hit
                        self.update()
                    self._show_point_info(hit, event.screenPos())
                else:
                    seg = self._hit_segment(px, py) if self._lines_visible() else None
                    if seg is not None:
                        mode = 'range' if mods & Qt.ShiftModifier else ('toggle' if mods & Qt.ControlModifier else 'replace')
                        self.select_segment(seg, mode)
                        self._selected_idx = None
                        self._announce_selection()
                    elif not (mods & (Qt.ShiftModifier | Qt.ControlModifier)):
                        self._selected_idx = None
                        if self._seg_sel is not None:
                            self.clear_segment_selection()
                            self._announce_selection()
                        self.update()
            self.initial_pos = event.scenePos()
            main_window = self._get_main_window()
            if main_window is None:
                super().mousePressEvent(event)
                return
            selected_items = main_window.layer_list.selectedItems()
            if selected_items:
                for item in selected_items:
                    layer = item.data(Qt.UserRole)
                    if layer and layer.current_item == self:
                        super().mousePressEvent(event)
                        return
                # not the selected layer -- let the view handle the click
                # (e.g. pan the canvas) instead of silently swallowing it
                event.ignore()
            else:
                super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton and self.initial_pos:
            delta = event.scenePos() - self.initial_pos
            self.initial_pos = event.scenePos()
            main_window = self._get_main_window()
            if main_window is None:
                return
            selected_items = main_window.layer_list.selectedItems()
            if selected_items:
                for item in selected_items:
                    layer = item.data(Qt.UserRole)
                    if layer and not layer._locked and layer.current_item:
                        if not layer._grid_locked:
                            layer.current_item.moveBy(delta.x(), delta.y())
            else:
                if not getattr(self.layer_ref, '_grid_locked', False):
                    super().mouseMoveEvent(event)

    def wheelEvent(self, event):
        try:
            if event.modifiers() == Qt.ControlModifier:
                layer = getattr(self, 'layer_ref', None)
                if layer and getattr(layer, '_grid_locked', False):
                    view = self.scene().views()[0]
                    view.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
                    factor = 1.2 if event.angleDelta().y() > 0 else 1 / 1.2
                    main_window = self._get_main_window()
                    if main_window is not None and hasattr(main_window, 'scale_view'):
                        # через главное окно -- учитываются пределы масштаба
                        main_window.scale_view(factor)
                    else:
                        view.scale(factor, factor)
                    event.accept()
                else:
                    super().wheelEvent(event)
        except Exception as e:
            logger.error(f'Error in wheel event: {e}', exc_info=True)


# ---------------------------------------------------------------------------
# GCodePointMarker  (kept for isinstance checks elsewhere; not instantiated)
# ---------------------------------------------------------------------------

class GCodePointMarker(QGraphicsItem):
    """Legacy class -- retained so external isinstance() checks don't break."""
    def __init__(self, point_data, parent=None, base_color=QColor(Qt.red)):
        super().__init__(parent)
        self.x, self.y, self.command = point_data
        self.size = 1.0
        self.base_color = QColor(base_color)
        self.setZValue(2)

    def boundingRect(self):
        return QRectF(
            self.x - self.size / 2, self.y - self.size / 2,
            self.size, self.size)

    def paint(self, painter, option, widget):
        pass
