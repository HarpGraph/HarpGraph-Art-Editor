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
