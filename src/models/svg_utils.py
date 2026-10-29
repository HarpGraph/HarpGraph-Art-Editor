"""SVG parsing helpers -- flatten any SVG shape into polyline subpaths.

Uses the `svgelements` package, which already resolves viewBox scaling and
nested <g transform="..."> matrices, so every point returned here is in a
single flat coordinate space.
"""
import math
import logging

logger = logging.getLogger(__name__)


def parse_svg_subpaths(filename: str, max_seg_fraction: float = 1.0 / 300.0):
    """Parse an SVG file into a list of subpaths (each a list of (x, y) tuples).

    Curves (Bezier/arc) are flattened adaptively relative to the drawing's
    own bounding-box diagonal, so resolution stays sane regardless of the
    document's native units (px/mm/in/...).

    Returns (subpaths, bbox) where bbox = (min_x, min_y, max_x, max_y),
    or ([], None) if the file has no drawable shapes.
    """
    try:
        import svgelements as se
    except ImportError as exc:
        raise RuntimeError(
            "Библиотека 'svgelements' не установлена (pip install svgelements)"
        ) from exc

    svg = se.SVG.parse(filename)

    raw_subpaths = []
    for element in svg.elements():
        if not isinstance(element, se.Shape) or isinstance(element, se.SVG):
            continue
        try:
            path = se.Path(element)
        except Exception:
            continue
        for subpath in path.as_subpaths():
            raw_subpaths.append(subpath)

    if not raw_subpaths:
        return [], None

    # bbox first, so curve flattening resolution can scale with drawing size
    min_x = min_y = math.inf
    max_x = max_y = -math.inf
    for subpath in raw_subpaths:
        bbox = subpath.bbox()
        if bbox is None:
            continue
        x0, y0, x1, y1 = bbox
        min_x, min_y = min(min_x, x0, x1), min(min_y, y0, y1)
        max_x, max_y = max(max_x, x0, x1), max(max_y, y0, y1)

    if not math.isfinite(min_x):
        return [], None

    diagonal = math.hypot(max_x - min_x, max_y - min_y) or 1.0
    max_seg = max(diagonal * max_seg_fraction, 1e-6)

    subpaths = []
    for subpath in raw_subpaths:
        pts = _flatten_subpath(subpath, max_seg)
        if len(pts) >= 2:
            subpaths.append(pts)

    return subpaths, (min_x, min_y, max_x, max_y)


def _flatten_subpath(subpath, max_seg: float):
    pts = []
    for seg in subpath.segments():
        kind = type(seg).__name__
        if kind in ('Move', 'Close', 'Line'):
            # straight segments need only their endpoint, no subdivision
            pts.append((float(seg.end.x), float(seg.end.y)))
            continue
        length = seg.length()
        steps = max(1, int(math.ceil(length / max_seg))) if length else 1
        for i in range(1, steps + 1):
            p = seg.point(i / steps)
            pts.append((float(p.x), float(p.y)))
    return pts


def get_svg_bbox(filename: str):
    """Cheap bbox-only parse (no curve flattening) -- used by the size dialog
    so it doesn't have to wait for a full parse just to show an aspect ratio.
    Returns (min_x, min_y, max_x, max_y) or None if there's nothing drawable.
    """
    try:
        import svgelements as se
    except ImportError as exc:
        raise RuntimeError(
            "Библиотека 'svgelements' не установлена (pip install svgelements)"
        ) from exc

    svg = se.SVG.parse(filename)
    min_x = min_y = math.inf
    max_x = max_y = -math.inf
    for element in svg.elements():
        if not isinstance(element, se.Shape) or isinstance(element, se.SVG):
            continue
        bbox = element.bbox()
        if bbox is None:
            continue
        x0, y0, x1, y1 = bbox
        min_x, min_y = min(min_x, x0, x1), min(min_y, y0, y1)
        max_x, max_y = max(max_x, x0, x1), max(max_y, y0, y1)

    if not math.isfinite(min_x):
        return None
    return (min_x, min_y, max_x, max_y)
