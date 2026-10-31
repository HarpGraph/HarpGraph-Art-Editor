"""Turn a list of polyline subpaths into G-code, with an optional greedy
travel-distance optimisation pass (reduces total pen-up/rapid distance)."""
import math
import logging

logger = logging.getLogger(__name__)


def _dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def optimize_subpath_order(subpaths, start=(0.0, 0.0)):
    """Greedy nearest-neighbour reordering: at each step, jump to whichever
    remaining subpath's start OR end is closest (reversing it if the end
    is closer), then continue from there. Not globally optimal (TSP is
    NP-hard) but cuts travel distance drastically versus file order."""
    if len(subpaths) > 200:
        # the plain-Python loop below is O(n^2) with Python-level math --
        # unusable for tens of thousands of stroke blocks (hours); same
        # greedy algorithm vectorised with numpy finishes in seconds
        return _optimize_subpath_order_numpy(subpaths, start)
    remaining = list(range(len(subpaths)))
    ordered = []
    cur = start
    while remaining:
        best_idx = best_reverse = best_dist = None
        for idx in remaining:
            sp = subpaths[idx]
            d_fwd = _dist(cur, sp[0])
            if best_dist is None or d_fwd < best_dist:
                best_dist, best_idx, best_reverse = d_fwd, idx, False
            d_rev = _dist(cur, sp[-1])
            if d_rev < best_dist:
                best_dist, best_idx, best_reverse = d_rev, idx, True
        sp = subpaths[best_idx]
        if best_reverse:
            sp = list(reversed(sp))
        ordered.append(sp)
        cur = sp[-1]
        remaining.remove(best_idx)
    return ordered


def _optimize_subpath_order_numpy(subpaths, start=(0.0, 0.0)):
    """Same greedy nearest-neighbour as above, but distances to ALL remaining
    subpath starts/ends are computed per step as numpy array operations."""
    import numpy as np
    n = len(subpaths)
    starts = np.array([sp[0] for sp in subpaths], dtype=np.float64)
    ends   = np.array([sp[-1] for sp in subpaths], dtype=np.float64)
    used   = np.zeros(n, dtype=bool)
    cur    = np.asarray(start, dtype=np.float64)

    ordered = []
    for _ in range(n):
        # squared distances rank identically to true distances
        d_fwd = ((starts - cur) ** 2).sum(axis=1)
        d_rev = ((ends   - cur) ** 2).sum(axis=1)
        d_fwd[used] = np.inf
        d_rev[used] = np.inf
        i_f = int(d_fwd.argmin())
        i_r = int(d_rev.argmin())
        if d_fwd[i_f] <= d_rev[i_r]:
            idx, rev = i_f, False
        else:
            idx, rev = i_r, True
        used[idx] = True
        sp = subpaths[idx]
        ordered.append(list(reversed(sp)) if rev else sp)
        cur = starts[idx] if rev else ends[idx]
    return ordered


def subpaths_to_gcode_lines(subpaths, feed: float = 1000.0, travel_z: float = 5.0,
                             draw_z: float = 0.0, optimize: bool = True,
                             flip_y: bool = True):
    """Convert subpaths (scene-space points) into a list of G-code lines.

    `flip_y` negates Y so the output matches this app's own G-code
    convention (file Y-up, inverted for on-screen Qt Y-down display).
    """
    if optimize:
        subpaths = optimize_subpath_order(subpaths)

    def conv(pt):
        x, y = pt
        return (x, -y) if flip_y else (x, y)

    lines = ['G21', 'G90', f'G0 Z{travel_z:.3f}']
    for subpath in subpaths:
        if len(subpath) < 2:
            continue
        x0, y0 = conv(subpath[0])
        lines.append(f'G0 X{x0:.3f} Y{y0:.3f}')
        lines.append(f'G1 Z{draw_z:.3f} F{feed:.1f}')
        for pt in subpath[1:]:
            x, y = conv(pt)
            lines.append(f'G1 X{x:.3f} Y{y:.3f}')
        lines.append(f'G0 Z{travel_z:.3f}')
    return lines


# ---------------------------------------------------------------------------
# Работа с массивами вершин G-code слоя (numpy): оптимизация холостых ходов и запись файла
# ---------------------------------------------------------------------------

def nearest_neighbour_order(starts, ends):
    """Жадный обход штрихов по ближайшему концу (штрих при необходимости разворачивается).
    Первый штрих остаётся на месте. starts/ends -- (n,2); возвращает (order, reverse):
    порядок штрихов и флаги разворота. Для больших n используется сетка ячеек
    (без scipy) -- сотни тысяч штрихов за секунды, а не часы."""
    import numpy as np
    n = len(starts)
    if n == 0:
        return np.empty(0, dtype=np.int64), np.empty(0, dtype=bool)
    pts = np.empty((2 * n, 2), dtype=np.float64)
    pts[0::2] = starts
    pts[1::2] = ends
    x0, y0 = pts.min(axis=0)
    x1, y1 = pts.max(axis=0)
    w, h = max(x1 - x0, 1e-9), max(y1 - y0, 1e-9)
    cell = max(math.sqrt(w * h / n) * 1.5, max(w, h) / n * 1.5, 1e-9)
    nx, ny = int(w / cell) + 3, int(h / cell) + 3
    cx = ((pts[:, 0] - x0) // cell).astype(np.int64).tolist()
    cy = ((pts[:, 1] - y0) // cell).astype(np.int64).tolist()
    keys = [a + b * nx for a, b in zip(cx, cy)]
    plist = pts.tolist()

    cells = {}
    for e, k in enumerate(keys):
        cells.setdefault(k, []).append(e)
    alive = np.ones(n, dtype=bool)

    def take(s):
        alive[s] = False
        for e in (2 * s, 2 * s + 1):
            lst = cells[keys[e]]
            lst.remove(e)
            if not lst:
                del cells[keys[e]]

    order = [0]
    rev = [False]
    take(0)
    cur = plist[1]
    remaining = n - 1
    BRUTE_LIMIT = 1500   # хвост, где точек мало -- проще перебрать массивом

    while remaining > 0:
        if remaining <= BRUTE_LIMIT:
            left = np.flatnonzero(alive)
            S, E = pts[0::2][left], pts[1::2][left]
            cur_a = np.asarray(cur)
            used = np.zeros(len(left), dtype=bool)
            for _ in range(len(left)):
                d_f = ((S - cur_a) ** 2).sum(axis=1)
                d_r = ((E - cur_a) ** 2).sum(axis=1)
                d_f[used] = np.inf
                d_r[used] = np.inf
                i_f, i_r = int(d_f.argmin()), int(d_r.argmin())
                if d_f[i_f] <= d_r[i_r]:
                    k, r = i_f, False
                else:
                    k, r = i_r, True
                used[k] = True
                order.append(int(left[k]))
                rev.append(r)
                cur_a = E[k] if not r else S[k]
            break

        qx, qy = cur
        ccx, ccy = int((qx - x0) // cell), int((qy - y0) // cell)
        best, best_d = None, math.inf
        r = 0
        limit = max(nx, ny) + 2
        while r <= limit:
            if r == 0:
                ring = ((ccx, ccy),)
            else:
                ring = []
                for d in range(-r, r + 1):
                    ring.append((ccx + d, ccy - r))
                    ring.append((ccx + d, ccy + r))
                for d in range(-r + 1, r):
                    ring.append((ccx - r, ccy + d))
                    ring.append((ccx + r, ccy + d))
            for ix, iy in ring:
                if 0 <= ix < nx and 0 <= iy < ny:
                    lst = cells.get(ix + iy * nx)
                    if lst:
                        for e in lst:
                            px, py = plist[e]
                            dd = (px - qx) ** 2 + (py - qy) ** 2
                            if dd < best_d:
                                best_d, best = dd, e
            if best is not None and math.sqrt(best_d) <= r * cell:
                break
            r += 1
        s = best >> 1
        is_end = best & 1
        order.append(s)
        rev.append(bool(is_end))
        take(s)
        cur = plist[2 * s] if is_end else plist[2 * s + 1]
        remaining -= 1

    return np.asarray(order, dtype=np.int64), np.asarray(rev, dtype=bool)


def optimize_travel(pts, is_travel, join_tol: float = 1e-3):
    """Оптимизация холостых ходов слоя: штрихи (серии рисующих линий между подъёмами)
    переставляются так, чтобы путь пера между ними был короче. pts -- (N,2), is_travel -- (N,)
    (вершина с is_travel=True -- перо пришло в неё поднятым, то есть с неё начинается штрих).
    Первый штрих остаётся первым. Штрихи, которые теперь стыкуются (<= join_tol мм), склеиваются.
    Возвращает (new_pts, new_is_travel, travel_before, travel_after) или None, если оптимизировать нечего."""
    import numpy as np
    pts = np.asarray(pts)
    n = len(pts)
    if n < 3:
        return None
    start_flag = np.asarray(is_travel, dtype=bool).copy()
    start_flag[0] = True
    s_idx = np.flatnonzero(start_flag)
    e_idx = np.append(s_idx[1:] - 1, n - 1)
    keep = e_idx > s_idx   # штрих без рисующих линий -- просто перемещение, выбрасываем
    s_idx, e_idx = s_idx[keep], e_idx[keep]
    m = len(s_idx)
    if m < 2:
        return None

    S = pts[s_idx].astype(np.float64)
    E = pts[e_idx].astype(np.float64)
    gap = S[1:] - E[:-1]
    before = float(np.hypot(gap[:, 0], gap[:, 1]).sum())

    order, rev = nearest_neighbour_order(S, E)
    S_o = np.where(rev[:, None], E[order], S[order])   # где штрих начинается после разворота
    E_o = np.where(rev[:, None], S[order], E[order])
    gap = S_o[1:] - E_o[:-1]
    dist = np.hypot(gap[:, 0], gap[:, 1])
    after = float(dist.sum())

    # сборка вершин: диапазоны штрихов в новом порядке, развёрнутые -- задом наперёд
    lens = (e_idx - s_idx + 1)[order]
    offsets = np.concatenate(([0], np.cumsum(lens)[:-1]))
    total = int(lens.sum())
    sid = np.repeat(np.arange(m), lens)
    within = np.arange(total) - offsets[sid]
    s_o, e_o = s_idx[order], e_idx[order]
    src = np.where(rev[sid], e_o[sid] - within, s_o[sid] + within)

    new_trav = np.zeros(total, dtype=bool)
    new_trav[offsets] = True
    joined = np.concatenate(([False], dist <= join_tol))   # штрих стыкуется с предыдущим
    drop = np.zeros(total, dtype=bool)
    drop[offsets[joined]] = True
    new_trav[offsets[joined]] = False
    new_trav[0] = bool(is_travel[0])
    keep_v = ~drop
    return pts[src][keep_v], new_trav[keep_v], before, after


def write_gcode_layer(filename, file_pts, is_travel, travel_z=5.0, draw_z=0.0, feed=1000.0,
                      with_z=True):
    """Записать вершины слоя (координаты файла, Y вверх) в G-code.
    with_z=True: подъём/опускание пера командами Z (travel_z/draw_z), рисующие -- G1 с подачей;
    with_z=False: только G0/G1 X Y. Возвращает numpy-массив номеров строк файла для каждой вершины
    (нужен слою, чтобы связать вершины со строками записанного файла)."""
    import numpy as np
    xs = file_pts[:, 0].astype(np.float64).tolist()
    ys = file_pts[:, 1].astype(np.float64).tolist()
    trav = np.asarray(is_travel, dtype=bool).tolist()
    n = len(xs)
    lines = []
    linenos = np.empty(n, dtype=np.int32)
    if not with_z:
        for i in range(n):
            lines.append(f"{'G0' if (trav[i] or i == 0) else 'G1'} X{xs[i]:.3f} Y{ys[i]:.3f}")
        linenos[:] = np.arange(1, n + 1, dtype=np.int32)
    else:
        lines.extend(['G21', 'G90', f'G0 Z{travel_z:.3f}'])
        pen_down = False
        for i in range(n):
            if trav[i] or i == 0:
                if pen_down:
                    lines.append(f'G0 Z{travel_z:.3f}')
                    pen_down = False
                lines.append(f'G0 X{xs[i]:.3f} Y{ys[i]:.3f}')
            else:
                if not pen_down:
                    lines.append(f'G1 Z{draw_z:.3f} F{feed:.1f}')
                    pen_down = True
                lines.append(f'G1 X{xs[i]:.3f} Y{ys[i]:.3f}')
            linenos[i] = len(lines)
        if pen_down:
            lines.append(f'G0 Z{travel_z:.3f}')
    with open(filename, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
        f.write('\n')
    return linenos
