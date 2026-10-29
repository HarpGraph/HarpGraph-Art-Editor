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
