"""Whole-workspace save/restore -- every layer, its source file, colours,
position and display options, so a half-finished session can be reopened
exactly as it was left (see GcodeEditor project chat: "работал, не доделал,
пришлось всё выключать -- нужен пресет")."""
import os
import json
import logging
from PyQt5.QtGui import QColor
from .layer_types import LayerType
from .gcode_layer import GCodeLayer
from .image_layer import ImageLayer
from .svg_layer import SvgLayer

logger = logging.getLogger(__name__)

PROJECT_VERSION = 1


def _layer_common(layer):
    pos = [0.0, 0.0]
    item = getattr(layer, 'current_item', None)
    if item is not None:
        p = item.pos()
        pos = [p.x(), p.y()]
    return {
        'name': layer.name,
        'visible': layer.visible,
        'opacity': layer.opacity,
        'locked': layer.locked,
        'grid_locked': getattr(layer, '_grid_locked', False),
        'pos': pos,
    }


def _serialize_layer(layer):
    if isinstance(layer, GCodeLayer):
        item = layer.current_item
        entry = {
            'type': 'GCODE',
            'file_path': layer.file_path,
            'line_color': layer.line_color.name(),
            'point_color': layer.point_color.name(),
            'travel_color': layer.travel_color.name(),
            'travel_z': layer.travel_z,
            'show_travel': layer.show_travel,
            'render_mode': item.render_mode if item else 'lines',
            'scale_x': item.transform().m11() if item else getattr(layer, 'scale_x', 1.0),
            'scale_y': item.transform().m22() if item else getattr(layer, 'scale_y', 1.0),
        }
    elif isinstance(layer, SvgLayer):
        w, h = layer.svg_size_mm
        entry = {
            'type': 'SVG',
            'file_path': layer.file_path,
            'line_color': layer.line_color.name(),
            'width_mm': w, 'height_mm': h,
            'sheet_frame': bool(layer.sheet_frame),
        }
    elif isinstance(layer, ImageLayer):
        w, h = layer.image_size_mm
        entry = {
            'type': 'IMAGE',
            'file_path': layer.file_path,
            'width_mm': w, 'height_mm': h,
        }
    else:
        return None
    entry.update(_layer_common(layer))
    return entry


def save_project(main_window, filename: str) -> bool:
    try:
        data = {
            'version': PROJECT_VERSION,
            'grid': {
                'visible': main_window.grid.isVisible(),
                'step': main_window.grid.grid_step,
                'opacity': main_window.grid.grid_opacity,
                'color': main_window.grid.grid_color.name(),
            },
            'field': {
                'width_mm': main_window.field_w_mm,
                'height_mm': main_window.field_h_mm,
            },
            'layers': [e for e in (_serialize_layer(l) for l in main_window.layers) if e is not None],
        }
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logger.error(f'Error saving project: {e}', exc_info=True)
        return False


def _apply_post_load(layer, entry):
    """Restore properties that only exist once current_item has been built
    (position / grid-lock / render mode) -- called synchronously for
    ImageLayer, or via load_finished for the async GCode/SVG layers."""
    item = getattr(layer, 'current_item', None)
    if item is None:
        return
    pos = entry.get('pos')
    if pos:
        item.setPos(pos[0], pos[1])
    if entry.get('grid_locked') and hasattr(item, 'set_grid_lock'):
        item.set_grid_lock(True)
    render_mode = entry.get('render_mode')
    if render_mode and hasattr(item, 'set_render_mode'):
        item.set_render_mode(render_mode)


def load_project(main_window, filename: str):
    """Returns (added, failed) -- added is how many layers were queued for
    loading, failed is a list of (name, file_path) that could not be loaded
    (missing file on disk, etc)."""
    with open(filename, 'r', encoding='utf-8') as f:
        data = json.load(f)

    main_window.clear_all_layers()

    grid = data.get('grid') or {}
    if grid:
        main_window.grid.setVisible(grid.get('visible', True))
        main_window.grid.set_grid_params(
            step=grid.get('step', 10.0),
            opacity=grid.get('opacity', 0.3),
            color=QColor(grid.get('color', '#808080')))
        main_window.grid_action.setChecked(main_window.grid.isVisible())
        main_window.save_grid_settings()

    field = data.get('field') or {}
    if field:
        main_window.field_w_mm = float(field.get('width_mm', main_window.field_w_mm))
        main_window.field_h_mm = float(field.get('height_mm', main_window.field_h_mm))
        main_window.field_w_spin.blockSignals(True)
        main_window.field_h_spin.blockSignals(True)
        main_window.field_w_spin.setValue(int(main_window.field_w_mm))
        main_window.field_h_spin.setValue(int(main_window.field_h_mm))
        main_window.field_w_spin.blockSignals(False)
        main_window.field_h_spin.blockSignals(False)
        main_window._update_field_visual()
        main_window.save_field_settings()

    added = 0
    failed = []
    for entry in data.get('layers', []):
        ltype = entry.get('type')
        file_path = entry.get('file_path')
        name = entry.get('name') or ltype or 'Layer'
        if ltype not in ('GCODE', 'IMAGE', 'SVG') or not file_path:
            continue
        if not os.path.isfile(file_path):
            logger.error(f'Project file missing, skipping layer "{name}": {file_path}')
            failed.append((name, file_path))
            continue

        layer = main_window.add_layer(name, LayerType[ltype])
        if layer is None:
            failed.append((name, file_path))
            continue
        # the item was just appended -- update_layer_item_text() only refreshes
        # the *selected* item, and nothing is selected during a batch restore,
        # so grab it directly instead of relying on that.
        list_item = main_window.layer_list.item(main_window.layer_list.count() - 1)

        layer.visible = entry.get('visible', True)
        layer.opacity = entry.get('opacity', 1.0)
        layer.locked = entry.get('locked', False)
        layer._grid_locked = entry.get('grid_locked', False)

        if ltype == 'GCODE':
            layer.line_color = QColor(entry.get('line_color', '#000000'))
            layer.point_color = QColor(entry.get('point_color', '#ff0000'))
            layer.travel_color = QColor(entry.get('travel_color', '#aa66cc'))
            layer.travel_z = float(entry.get('travel_z', 5.0))
            layer.show_travel = entry.get('show_travel', True)
            layer.scale_x = float(entry.get('scale_x', 1.0))
            layer.scale_y = float(entry.get('scale_y', 1.0))
            layer.load_finished.connect(
                lambda ok, _l=layer, _e=entry: _apply_post_load(_l, _e) if ok else None)
            layer.load_file(file_path)   # sets layer.file_path synchronously before parsing
            if list_item:
                main_window.update_layer_item_text(list_item, layer)
            added += 1
        elif ltype == 'SVG':
            layer.line_color = QColor(entry.get('line_color', '#000000'))
            w = float(entry.get('width_mm', 100.0))
            h = float(entry.get('height_mm', 100.0))
            layer.load_finished.connect(
                lambda ok, _l=layer, _e=entry: _apply_post_load(_l, _e) if ok else None)
            # проекты, сохранённые до привязки к листу, открываются по-старому (без поля sheet_frame)
            layer.load_file(file_path, w, h, sheet_frame=bool(entry.get('sheet_frame', False)))
            if list_item:
                main_window.update_layer_item_text(list_item, layer)
            added += 1
        elif ltype == 'IMAGE':
            w = float(entry.get('width_mm', 100.0))
            h = float(entry.get('height_mm', 100.0))
            if layer.load_file(file_path, w, h):
                _apply_post_load(layer, entry)
                if list_item:
                    main_window.update_layer_item_text(list_item, layer)
                added += 1
            else:
                failed.append((name, file_path))

    main_window.update_layers_z_values()
    return added, failed
