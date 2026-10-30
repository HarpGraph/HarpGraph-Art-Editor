# -*- coding: utf-8 -*-
"""Выделение областью (рамка/полигон) целыми блоками штрихов.

Блок штрихов -- подряд идущие рабочие линии между подъёмами инструмента.
Блок, который пересекает граница области, не перерезается: он целиком уходит
внутрь или остаётся снаружи.
"""
import numpy as np


def _min_edge_distance(px, py, poly):
    """Расстояние от точек до границы многоугольника (мин. по рёбрам)."""
    best = np.full(len(px), np.inf)
    x1, y1 = poly[-1]
    for x2, y2 in poly:
        ex, ey = x2 - x1, y2 - y1
        l2 = ex * ex + ey * ey
        if l2 > 0:
            t = np.clip(((px - x1) * ex + (py - y1) * ey) / l2, 0.0, 1.0)
        else:
            t = np.zeros(len(px))
        d = np.hypot(px - (x1 + t * ex), py - (y1 + t * ey))
        np.minimum(best, d, out=best)
        x1, y1 = x2, y2
    return best


def select_blocks_by_area(xs, ys, is_travel, poly, tol, points_in_polygon):
    """Маски линий (len = N-1) для блоков внутри и снаружи области.

    xs, ys -- координаты вершин в сцене (мм), poly -- (M,2).
    Для блока, который пересекает граница:
      * выступает наружу не больше, чем на tol мм  -> внутрь;
      * заходит внутрь не глубже, чем на tol мм    -> наружу;
      * иначе -- туда, где большая часть его длины.
    Холостые ходы в обе маски не входят: перед каждой серией при записи
    всё равно ставится холостой переезд.
    Возвращает (in_mask, out_mask, blocks_in, blocks_out, blocks_split) или None.
    """
    n = len(xs)
    if n < 2:
        return None
    nseg = n - 1
    draw = ~np.asarray(is_travel[1:], dtype=bool)
    if not draw.any():
        return None
    inside = points_in_polygon(xs, ys, poly)

    starts = draw & np.concatenate(([True], ~draw[:-1]))
    bid = np.cumsum(starts) - 1
    nb = int(starts.sum())
    d_idx = np.flatnonzero(draw)
    b = bid[d_idx]

    a_in, b_in = inside[:-1][d_idx], inside[1:][d_idx]
    cnt = np.bincount(b, minlength=nb)
    c_in = np.bincount(b, weights=(a_in & b_in), minlength=nb)
    c_out = np.bincount(b, weights=(~a_in & ~b_in), minlength=nb)
    block_in = c_in == cnt
    block_out = c_out == cnt
    cross = ~(block_in | block_out)

    take = block_in.copy()
    if cross.any():
        sel = cross[b]
        si, sb = d_idx[sel], b[sel]
        ax, ay, bx, by = xs[si], ys[si], xs[si + 1], ys[si + 1]
        length = np.hypot(bx - ax, by - ay)
        mid_in = points_in_polygon((ax + bx) / 2.0, (ay + by) / 2.0, poly)
        l_tot = np.bincount(sb, weights=length, minlength=nb)
        l_in = np.bincount(sb, weights=length * mid_in, minlength=nb)

        vx, vy = np.concatenate((ax, bx)), np.concatenate((ay, by))
        vin = np.concatenate((inside[si], inside[si + 1]))
        vb = np.concatenate((sb, sb))
        dist = _min_edge_distance(vx.astype(np.float64), vy.astype(np.float64), poly)
        over_out = np.zeros(nb)
        over_in = np.zeros(nb)
        if (~vin).any():
            np.maximum.at(over_out, vb[~vin], dist[~vin])
        if vin.any():
            np.maximum.at(over_in, vb[vin], dist[vin])
        majority = l_in * 2.0 >= l_tot
        decide = np.where(over_out <= tol, True, np.where(over_in <= tol, False, majority))
        take[cross] = decide[cross]

    in_mask = np.zeros(nseg, dtype=bool)
    out_mask = np.zeros(nseg, dtype=bool)
    in_mask[d_idx] = take[b]
    out_mask[d_idx] = ~take[b]
    return in_mask, out_mask, int(take.sum()), int(nb - take.sum()), int(cross.sum())
