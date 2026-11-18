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
    from .native_backend import backend
    import shapely
    from .oas_stream import polygons, sint, string, uint
    import math

    path = Path(path)
    if not path.is_absolute() or path.suffix.lower() not in ('.gds', '.oas', '.oasis'):
        raise ValueError('模板图纸须为绝对路径的GDS或OAS文件')
    if digest(path) != expected_sha256:
        raise ValueError('模板图纸已改变，请重新创建任务')
    body = bytearray()
    layer_names = {}
    for layer, datatype, name, geometry, labels in backend().execute(
        {'op': 'template_shapes', 'path': str(path)}
    ):
        key = f'{layer}/{datatype}'
        layer_names[key] = name
        for hull, holes in geometry:
            cancel_check()
            polygon = shapely.Polygon(hull, holes)
            if not math.isfinite(polygon.area) or not polygon.is_valid:
                raise ValueError(f'模板图层 {key} 含有无效轮廓')
            encoded = polygons({key: [polygon.wkb]}, dbu)
            if not encoded:
                raise ValueError(f'模板图层 {key} 的图形低于输出图纸精度')
            body.extend(encoded)
        for label, x_um, y_um in labels:
            cancel_check()
            x, y = round(x_um / dbu), round(y_um / dbu)
            if max(abs(x), abs(y)) > 2**52:
                raise ValueError('模板文字坐标超出范围')
            body.extend(b'\x13\x5b' + string(label) + uint(layer)
                        + uint(datatype) + sint(x) + sint(y))
    if not body:
        raise ValueError('模板图纸没有可输出的内容')
    if digest(path) != expected_sha256:
        raise ValueError('读取过程中模板图纸发生变化')
    return bytes(body), layer_names
