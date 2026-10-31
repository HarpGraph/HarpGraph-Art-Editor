"""Геометрия слоя G-code над массивами вершин (numpy): обрезка по прямоугольнику поля.
Все функции работают в координатах сцены (мм, Y вниз), как их видит художник."""
import numpy as np


def clip_strokes_to_rect(pts, is_travel, xmin, ymin, xmax, ymax):
    """Оставить только рисующие линии внутри прямоугольника; линии, пересекающие границу,
    обрезаются ровно по границе (Лианг-Барски). Всё, что лежит снаружи, исчезает;
    разрывы становятся холостым ходом.

    pts -- (N,2), is_travel -- (N,) bool (вершина с True: перо пришло в неё поднятым).
    Возвращает (new_pts float64, new_is_travel, removed, clipped) где removed -- сколько рисующих
    линий исчезло целиком, clipped -- сколько обрезано по границе; None, если внутри ничего нет."""
    pts = np.asarray(pts, dtype=np.float64)
    trav = np.asarray(is_travel, dtype=bool)
    if len(pts) < 2:
        return None
    a, b = pts[:-1], pts[1:]
    draw = ~trav[1:]
    dx, dy = b[:, 0] - a[:, 0], b[:, 1] - a[:, 1]
    t0 = np.zeros(len(a))
    t1 = np.ones(len(a))
    ok = draw.copy()
    for p, q in ((-dx, a[:, 0] - xmin), (dx, xmax - a[:, 0]),
                 (-dy, a[:, 1] - ymin), (dy, ymax - a[:, 1])):
        parallel = p == 0
        ok &= ~(parallel & (q < 0))
        with np.errstate(divide='ignore', invalid='ignore'):
            r = np.where(parallel, 0.0, q / np.where(parallel, 1.0, p))
        t0 = np.where((p < 0) & ~parallel, np.maximum(t0, r), t0)
        t1 = np.where((p > 0) & ~parallel, np.minimum(t1, r), t1)
    ok &= t0 <= t1 + 1e-12

    idx = np.flatnonzero(ok)
    if not len(idx):
        return None
    A = a[idx] + t0[idx, None] * np.column_stack((dx[idx], dy[idx]))
    B = a[idx] + t1[idx, None] * np.column_stack((dx[idx], dy[idx]))
    cont = np.zeros(len(idx), dtype=bool)   # линия продолжает предыдущую без разрыва
    cont[1:] = (idx[1:] == idx[:-1] + 1) & (t1[idx[:-1]] >= 1 - 1e-12) & (t0[idx[1:]] <= 1e-12)

    counts = np.where(cont, 1, 2)
    end_pos = np.cumsum(counts) - 1
    out = np.empty((int(counts.sum()), 2), dtype=np.float64)
    out_trav = np.zeros(len(out), dtype=bool)
    out[end_pos] = B
    new = ~cont
    out[end_pos[new] - 1] = A[new]
    out_trav[end_pos[new] - 1] = True

    removed = int(draw.sum() - ok.sum())
    clipped = int(((t0[idx] > 1e-12) | (t1[idx] < 1 - 1e-12)).sum())
    return out, out_trav, removed, clipped
