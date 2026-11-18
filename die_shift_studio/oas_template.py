"""Read an optional static template in micrometres for streaming OAS output."""
from hashlib import sha256
from pathlib import Path

from .resources import cancel_check


def digest(path):
    result = sha256()
    with Path(path).open('rb') as stream:
        while chunk := stream.read(1024**2):
            cancel_check()
            result.update(chunk)
    return result.hexdigest()


def read_template(path, expected_sha256, dbu):
    import klayout.db as kl
    import shapely
    from .oas_stream import polygons, sint, string, uint
    import math

    path = Path(path)
    if not path.is_absolute() or path.suffix.lower() not in ('.gds', '.oas', '.oasis'):
        raise ValueError('模板图纸须为绝对路径的GDS或OAS文件')
    if digest(path) != expected_sha256:
        raise ValueError('模板图纸已改变，请重新创建任务')
    layout = kl.Layout()
    layout.read(str(path))
    roots = layout.top_cells()
    if not roots:
        raise ValueError('模板图纸没有顶层Cell')
    body = bytearray()
    layer_names = {}
    shape_count = 0
    for index in layout.layer_indexes():
        info = layout.get_info(index)
        key = f'{info.layer}/{info.datatype}'
        for root in roots:
            for item in root.begin_shapes_rec(index).each():
                cancel_check()
                shape_count += 1
                if shape_count > 500_000:
                    raise ValueError('模板展开超过500000个图形，未截断输出')
                shape = item.shape()
                transform = item.dtrans()
                layer_names[key] = info.name
                if shape.is_text():
                    label = shape.dtext.transformed(transform)
                    x = round(label.x / dbu)
                    y = round(label.y / dbu)
                    if max(abs(x), abs(y)) > 2**52:
                        raise ValueError('模板文字坐标超出范围')
                    body.extend(b'\x13\x5b' + string(label.string) + uint(info.layer)
                                + uint(info.datatype) + sint(x) + sint(y))
                    continue
                if not (shape.is_box() or shape.is_polygon() or shape.is_path()):
                    raise ValueError(f'模板图层 {key} 含有无法保留的图形类型')
                polygon = shape.dpolygon.transformed(transform)
                hull = [(p.x, p.y) for p in polygon.each_point_hull()]
                holes = [[(p.x, p.y) for p in polygon.each_point_hole(i)] for i in range(polygon.holes())]
                geometry = shapely.Polygon(hull, holes)
                if not math.isfinite(geometry.area) or not geometry.is_valid:
                    raise ValueError(f'模板图层 {key} 含有无效轮廓')
                encoded = polygons({key: [geometry.wkb]}, dbu)
                if not encoded:
                    raise ValueError(f'模板图层 {key} 的图形低于输出图纸精度')
                body.extend(encoded)
    if not body:
        raise ValueError('模板图纸没有可输出的内容')
    if digest(path) != expected_sha256:
        raise ValueError('读取过程中模板图纸发生变化')
    return bytes(body), layer_names
