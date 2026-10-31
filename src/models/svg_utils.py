"""SVG parsing helpers -- flatten any SVG shape into polyline subpaths.

Uses the `svgelements` package, which already resolves viewBox scaling and
nested <g transform="..."> matrices, so every point returned here is in a
single flat coordinate space.

For simple line-only SVGs (the BOCHKOVKA Orchestrator export format: root
size in mm, a background <rect> without stroke, one <g> per pen with
polylines inside) there is a dedicated fast numpy parser `parse_svg_pens` --
~30x faster than svgelements and it keeps per-pen colour/width, which the
generic parser throws away.
"""
import math
import re
import logging

logger = logging.getLogger(__name__)

# px -> mm at the CSS reference 96 dpi used by svgelements' default parse
PX_TO_MM = 25.4 / 96.0

_SVG_TAG_RE = re.compile(r'<svg\b[^>]*>', re.S)
# elements the fast parser can't turn into polylines -- their presence means
# "fall back to the generic svgelements parser"
_UNSUPPORTED_RE = re.compile(
    r'<(path|circle|ellipse|polygon|text|image|use|defs|symbol|style)\b', re.I)
_GROUP_RE = re.compile(r'<g\b([^>]*)>(.*?)</g>', re.S)
_POLYLINE_RE = re.compile(r'<polyline\b[^>]*\bpoints="([^"]+)"')
_RECT_RE = re.compile(r'<rect\b[^>]*/?>')


def _svg_root_attrs(text: str):
    """width/height/viewBox attributes of the root <svg> tag, or None."""
    m = _SVG_TAG_RE.search(text)
    if not m:
        return None
    tag = m.group(0)

    def attr(name):
        am = re.search(name + r'="([^"]*)"', tag)
        return am.group(1).strip() if am else None

    return attr('width'), attr('height'), attr('viewBox')


def sniff_svg_mm_size(filename: str):
    """Returns (width_mm, height_mm) if the root <svg> tag declares physical
    mm units, else None. Reads only the file header -- cheap for any size."""
    try:
        with open(filename, encoding='utf-8', errors='ignore') as f:
            head = f.read(4096)
    except OSError:
        return None
    attrs = _svg_root_attrs(head)
    if not attrs:
        return None
    w, h = attrs[0], attrs[1]
    if not (w and h and w.endswith('mm') and h.endswith('mm')):
        return None
    try:
        return float(w[:-2]), float(h[:-2])
    except ValueError:
        return None


def _parse_color(value: str):
    """Normalise 'rgb(r,g,b)' / '#hex' / named colour to a '#RRGGBB' string."""
    if not value:
        return '#000000'
    value = value.strip()
    m = re.match(r'rgb\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)', value, re.I)
    if m:
        return '#{:02X}{:02X}{:02X}'.format(*(min(255, int(g)) for g in m.groups()))
    return value


def parse_svg_pens(filename: str):
    """Fast parser for simple line-only SVGs (BOCHKOVKA Orchestrator export).

    Requirements for the fast path (otherwise returns None and the caller
    should fall back to `parse_svg_subpaths`):
      - root <svg> width/height in mm with a viewBox;
      - geometry is only <polyline> elements inside <g> pen groups;
      - any <rect> has no stroke (= sheet background, skipped: a pen never
        draws it).

    Returns a dict (all coordinates already in millimetres):
      subpaths   list[list[(x, y)]]   -- one entry per stroke block, file order
      pen_index  list[int]            -- pen number of each subpath
      pens       list[{'color': '#RRGGBB', 'width_mm': float}]
      size_mm    (width_mm, height_mm)  -- declared sheet size
      bbox       (min_x, min_y, max_x, max_y) -- of the strokes themselves
    """
    import numpy as np

    try:
        with open(filename, encoding='utf-8') as f:
            text = f.read()
    except (OSError, UnicodeDecodeError):
        return None

    attrs = _svg_root_attrs(text)
    if not attrs:
        return None
    w_attr, h_attr, viewbox = attrs
    if not (w_attr and h_attr and viewbox
            and w_attr.endswith('mm') and h_attr.endswith('mm')):
        return None
    try:
        width_mm, height_mm = float(w_attr[:-2]), float(h_attr[:-2])
        vb = [float(v) for v in viewbox.replace(',', ' ').split()]
    except ValueError:
        return None
    if len(vb) != 4 or vb[2] <= 0 or vb[3] <= 0:
        return None
    vb_x, vb_y, vb_w, vb_h = vb
    scale_x, scale_y = width_mm / vb_w, height_mm / vb_h

    if _UNSUPPORTED_RE.search(text):
        return None
    # a <rect> WITH a stroke would be drawable geometry we'd silently drop
    for rect_tag in _RECT_RE.findall(text):
        sm = re.search(r'stroke="([^"]*)"', rect_tag)
        if sm and sm.group(1).strip().lower() not in ('', 'none'):
            return None

    groups = _GROUP_RE.findall(text)
    if not groups:
        return None
    # polylines outside any group would be lost -- bail out to generic parser
    total_polylines = len(_POLYLINE_RE.findall(text))

    subpaths, pen_index, pens = [], [], []
    grouped_polylines = 0
    for attrs_str, body in groups:
        cm = re.search(r'stroke="([^"]*)"', attrs_str)
        wm = re.search(r'stroke-width="([^"]*)"', attrs_str)
        color = _parse_color(cm.group(1) if cm else None)
        try:
            width_pen = float(wm.group(1)) * scale_x if wm else 0.3
        except ValueError:
            width_pen = 0.3

        polys = _POLYLINE_RE.findall(body)
        grouped_polylines += len(polys)
        if not polys:
            continue
        counts = [p.count(',') for p in polys]
        joined = ' '.join(polys).replace(',', ' ')
        vals = np.array(joined.split(), dtype=np.float64)
        if vals.size != 2 * sum(counts) or vals.size == 0:
            return None   # unexpected points format -- let svgelements handle it
        pts = vals.reshape(-1, 2)
        pts[:, 0] = (pts[:, 0] - vb_x) * scale_x
        pts[:, 1] = (pts[:, 1] - vb_y) * scale_y

        pen_no = len(pens)
        pens.append({'color': color, 'width_mm': width_pen})
        for chunk in np.split(pts, np.cumsum(counts)[:-1]):
            if len(chunk) >= 2:
                subpaths.append([tuple(p) for p in chunk])
                pen_index.append(pen_no)

    if grouped_polylines != total_polylines or not subpaths:
        return None

    all_pts = np.concatenate([np.asarray(sp) for sp in subpaths])
    bbox = (float(all_pts[:, 0].min()), float(all_pts[:, 1].min()),
            float(all_pts[:, 0].max()), float(all_pts[:, 1].max()))
    return {
        'subpaths': subpaths,
        'pen_index': pen_index,
        'pens': pens,
        'size_mm': (width_mm, height_mm),
        'bbox': bbox,
    }


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
