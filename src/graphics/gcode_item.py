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
import logging

import numpy as np
from PyQt5.QtWidgets import QGraphicsItem, QGraphicsItemGroup, QGraphicsView
from PyQt5.QtGui import QPainterPath, QPen, QColor
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
        # OPT-7: one shared hover timer for the whole item
        self._hover_timer = QTimer()
        self._hover_timer.setSingleShot(True)
        self._hover_timer.setInterval(2000)
        self._hover_timer.timeout.connect(self._on_hover_timer)
        self._hovered_marker = None       # current marker under cursor
        self._bounding_rect = QRectF()
        self._chunks = []                 # OPT-12 chunk children
        # OPT-13: lazy sorted-by-X index for fast hover lookups
        self._xorder = None
        self._xsorted = None
        self._trim_overlay_item = None      # colour-tint preview overlay
        self._trim_kept_draw_item = None    # 'hide removed' preview -- kept draw strokes
        self._trim_kept_travel_item = None  # 'hide removed' preview -- kept travel strokes
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
    # OPT-7 / OPT-8 helpers
    # ------------------------------------------------------------------

    def _on_hover_timer(self):
        """OPT-7: shared timer callback -- delegate to the hovered marker."""
        if self._hovered_marker is not None:
            self._hovered_marker._show_hover()

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

            self.clear_trim_highlight()
            self._rebuild_chunks()

            self._hover_timer.stop()
            self._hovered_marker = None
            self.setPos(0, 0)
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
        self._hovered_marker = None
        self._hover_timer.stop()
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

        painter.save()
        pen = QPen(self.point_color, 1.0)      # 1 scene unit, like the old markers
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        pts = self.pts
        points = [QPointF(float(pts[i, 0]), float(pts[i, 1])) for i in idx]
        if points:
            painter.drawPoints(*points)

        marker = self._hovered_marker
        if marker is not None:
            hover_pen = QPen(self.point_color.lighter(150), 1.6)
            hover_pen.setCapStyle(Qt.RoundCap)
            painter.setPen(hover_pen)
            painter.drawPoint(QPointF(marker.x, marker.y))
        painter.restore()

    # ------------------------------------------------------------------
    # Hover (OPT-13: sorted-X index -> O(log n) nearest-point lookup)
    # ------------------------------------------------------------------

    def _ensure_hover_index(self):
        if self._xorder is None and len(self.pts):
            self._xorder = np.argsort(self.pts[:, 0]).astype(np.int64)
            self._xsorted = np.ascontiguousarray(self.pts[self._xorder, 0])

    def _command_for_index(self, i: int) -> str:
        g = 'G0' if bool(self.is_travel[i]) else 'G1'
        x, y = float(self.pts[i, 0]), float(self.pts[i, 1])
        return f'{g} X{x:.3f} Y{-y:.3f}'

    def hoverMoveEvent(self, event):
        if self.render_mode not in ('points', 'both', 'coords') or not len(self.pts):
            return
        self._ensure_hover_index()
        px, py = event.pos().x(), event.pos().y()
        lo = int(np.searchsorted(self._xsorted, px - 2.0))
        hi = int(np.searchsorted(self._xsorted, px + 2.0))
        best_marker = None
        if hi > lo:
            cand = self._xorder[lo:hi]
            dx = self.pts[cand, 0] - px
            dy = self.pts[cand, 1] - py
            d2 = dx * dx + dy * dy
            k = int(np.argmin(d2))
            if d2[k] < 4.0:   # (2 scene units)^2
                i = int(cand[k])
                best_marker = _PointProxy(float(self.pts[i, 0]), float(self.pts[i, 1]),
                                          self._command_for_index(i))

        if best_marker is not self._hovered_marker:
            self._hover_timer.stop()
            self._hovered_marker = best_marker
            if best_marker is not None:
                self._hover_timer.start()
            self.update()

    def hoverLeaveEvent(self, event):
        self._hovered_marker = None
        self._hover_timer.stop()
        self.update()

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

        if hide_removed:
            for ch in self._chunks:
                ch.setVisible(False)
            sel = self._preview_selection(start_idx, end_idx)
            kept_draw, kept_travel = _sel_to_paths(self.pts, self._trav_cum, sel)

            from PyQt5.QtWidgets import QGraphicsPathItem
            self._trim_kept_draw_item = QGraphicsPathItem(kept_draw, self)
            self._trim_kept_draw_item.setPen(self.line_pen)
            self._trim_kept_draw_item.setZValue(50)

            if not kept_travel.isEmpty():
                self._trim_kept_travel_item = QGraphicsPathItem(kept_travel, self)
                self._trim_kept_travel_item.setPen(self.travel_pen_solid)
                self._trim_kept_travel_item.setZValue(50)
            if not self.show_travel and self._trim_kept_travel_item:
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

    def clear_trim_highlight(self):
        self._clear_trim_items()
        self._restore_chunk_visibility()

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
        logger.debug(f'Render mode: {self.render_mode} -> {mode}')
        self.render_mode = mode
        trim_active = (self._trim_kept_draw_item is not None)
        if not trim_active:
            self._restore_chunk_visibility()
        self.update()

    def set_travel_visible(self, visible: bool):
        self.show_travel = visible
        for ch in self._chunks:
            ch.update()
        if self._trim_kept_travel_item:
            self._trim_kept_travel_item.setVisible(visible)

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
        self.setAcceptHoverEvents(not locked)

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
            self.setAcceptHoverEvents(not locked)
            logger.debug(f'Grid lock set to {locked}')
        except Exception as e:
            logger.error(f'Error setting grid lock: {e}', exc_info=True)

    # ------------------------------------------------------------------
    # Mouse events
    # ------------------------------------------------------------------

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
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
# _PointProxy  (lightweight stand-in used during hover -- no QGraphicsItem)
# ---------------------------------------------------------------------------

class _PointProxy:
    """Lightweight data holder used during hover -- no QGraphicsItem overhead."""
    __slots__ = ('x', 'y', 'command')

    def __init__(self, x: float, y: float, command: str):
        self.x = x
        self.y = y
        self.command = command

    def _show_hover(self):
        """Called by GCodeGraphicsItem._on_hover_timer."""
        from PyQt5.QtWidgets import QApplication
        app = QApplication.instance()
        if app is None:
            return
        # find main window via active QGraphicsView in focus
        for widget in app.topLevelWidgets():
            if hasattr(widget, 'update_coordinates'):
                widget.update_coordinates(self.command)
                break


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
