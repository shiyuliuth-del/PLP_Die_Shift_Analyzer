"""Straight-contour AC1015 records adapted from user-supplied File Bridge 1.16.2.

Only the database scaffold and straight HATCH/LWPOLYLINE writers are retained.
There is no KLayout runtime, curve fitting, polyline arc restoration or repair.
Reference SHA-256: d85bd2017cf79398ae10dd78da28c445c3aaed18239d3793100807746964ae2e
"""
AUTOCAD_DXF_VERSION="AC1015"
DXF_INSUNITS_MICRONS=13
DXF_MEASUREMENT_METRIC=1
STANDARD_TABLES=("VPORT","LTYPE","LAYER","STYLE","VIEW","UCS","APPID","DIMSTYLE","BLOCK_RECORD")

class HandleAllocator(object):
    def __init__(self, first=1):
        self._next = int(first)

    def take(self):
        value = "%X" % self._next
        self._next += 1
        return value

    def handseed(self):
        return "%X" % self._next

def _table_start(writer, name, handle, count, extra_subclass=None):
    writer.extend((
        (0, "TABLE"), (2, name), (5, handle), (330, "0"),
        (100, "AcDbSymbolTable"), (70, count),
    ))
    if extra_subclass:
        writer.add(100, extra_subclass)

def _table_end(writer):
    writer.add(0, "ENDTAB")

def _write_header(writer, handles, extents, fillmode):
    xmin, ymin, xmax, ymax = extents
    width = max(xmax - xmin, 1.0)
    height = max(ymax - ymin, 1.0)
    writer.extend(((0, "SECTION"), (2, "HEADER")))
    for name, values in (
            ("$ACADVER", ((1, AUTOCAD_DXF_VERSION),)),
            ("$DWGCODEPAGE", ((3, "ANSI_1252"),)),
            ("$INSBASE", ((10, 0.0), (20, 0.0), (30, 0.0))),
            ("$EXTMIN", ((10, xmin), (20, ymin), (30, 0.0))),
            ("$EXTMAX", ((10, xmax), (20, ymax), (30, 0.0))),
            ("$LIMMIN", ((10, xmin), (20, ymin))),
            ("$LIMMAX", ((10, xmin + width), (20, ymin + height))),
            ("$CLAYER", ((8, "0"),)),
            ("$TEXTSTYLE", ((7, "Standard"),)),
            ("$DIMSTYLE", ((2, "Standard"),)),
            ("$PSTYLEMODE", ((290, 1),)),
            ("$INSUNITS", ((70, DXF_INSUNITS_MICRONS),)),
            ("$MEASUREMENT", ((70, DXF_MEASUREMENT_METRIC),)),
            ("$FILLMODE", ((70, int(fillmode)),)),
            ("$HANDSEED", ((5, handles["handseed"]),))):
        writer.add(9, name)
        writer.extend(values)
    writer.add(0, "ENDSEC")

def _write_classes(writer):
    # ACDBDICTIONARYWDFLT is a custom ObjectDBX class in AC1015 and must be
    # declared before the object is used.  AutoCAD 2027 rejects an undeclared
    # class during DXF load even when simpler third-party parsers accept it.
    writer.extend(((0, "SECTION"), (2, "CLASSES")))
    for class_name, cpp_name in (
            ("ACDBDICTIONARYWDFLT", "AcDbDictionaryWithDefault"),
            ("ACDBPLACEHOLDER", "AcDbPlaceHolder")):
        # AutoCAD 2000/AC1015 CLASS records do not contain group 91.  Group
        # 91 appears in newer DXF versions and makes AutoCAD reject AC1015 as
        # "需要编组 280 ... 第 90 行".
        writer.extend((
            (0, "CLASS"), (1, class_name), (2, cpp_name),
            (3, "ObjectDBX Classes"),
            (90, 0), (280, 0), (281, 0),
        ))
    writer.add(0, "ENDSEC")

def _write_vport_table(writer, handles, extents):
    xmin, ymin, xmax, ymax = extents
    width = max(xmax - xmin, 1.0)
    height = max(ymax - ymin, 1.0)
    _table_start(writer, "VPORT", handles["table_VPORT"], 1)
    writer.extend((
        (0, "VPORT"), (5, handles["vport_active"]),
        (330, handles["table_VPORT"]),
        (100, "AcDbSymbolTableRecord"),
        (100, "AcDbViewportTableRecord"), (2, "*ACTIVE"), (70, 0),
        (10, 0.0), (20, 0.0), (11, 1.0), (21, 1.0),
        (12, (xmin + xmax) * 0.5), (22, (ymin + ymax) * 0.5),
        (13, 0.0), (23, 0.0), (14, 0.5), (24, 0.5),
        (15, 0.5), (25, 0.5), (16, 0.0), (26, 0.0), (36, 1.0),
        (17, 0.0), (27, 0.0), (37, 0.0), (40, height),
        (41, width / height), (42, 50.0), (43, 0.0), (44, 0.0),
        (50, 0.0), (51, 0.0), (71, 0), (72, 1000), (73, 1),
        (74, 3), (75, 0), (76, 0), (77, 0), (78, 0), (281, 0),
        (65, 0), (146, 0.0),
    ))
    _table_end(writer)

def _write_ltype_table(writer, handles):
    _table_start(writer, "LTYPE", handles["table_LTYPE"], 3)
    for key, name in (
            ("ltype_byblock", "BYBLOCK"),
            ("ltype_bylayer", "BYLAYER"),
            ("ltype_continuous", "CONTINUOUS")):
        writer.extend((
            (0, "LTYPE"), (5, handles[key]),
            (330, handles["table_LTYPE"]),
            (100, "AcDbSymbolTableRecord"),
            (100, "AcDbLinetypeTableRecord"), (2, name), (70, 0),
            (3, ""), (72, 65), (73, 0), (40, 0.0),
        ))
    _table_end(writer)

def _write_layer_table(writer, handles, layers):
    _table_start(writer, "LAYER", handles["table_LAYER"], len(layers))
    for layer in layers:
        writer.extend((
            (0, "LAYER"), (5, layer["handle"]),
            (330, handles["table_LAYER"]),
            (100, "AcDbSymbolTableRecord"),
            (100, "AcDbLayerTableRecord"), (2, layer["encoded_name"]),
            (70, 0), (62, layer["color"]), (6, "CONTINUOUS"),
            (290, 1), (370, -3), (390, handles["plot_normal"]),
        ))
    _table_end(writer)

def _write_style_table(writer, handles):
    _table_start(writer, "STYLE", handles["table_STYLE"], 1)
    writer.extend((
        (0, "STYLE"), (5, handles["style_standard"]),
        (330, handles["table_STYLE"]),
        (100, "AcDbSymbolTableRecord"),
        (100, "AcDbTextStyleTableRecord"), (2, "Standard"), (70, 0),
        (40, 0.0), (41, 1.0), (50, 0.0), (71, 0), (42, 2.5),
        (3, "txt"), (4, ""),
    ))
    _table_end(writer)

def _write_empty_table(writer, handles, name):
    _table_start(writer, name, handles["table_" + name], 0)
    _table_end(writer)

def _write_appid_table(writer, handles):
    _table_start(writer, "APPID", handles["table_APPID"], 1)
    writer.extend((
        (0, "APPID"), (5, handles["appid_acad"]),
        (330, handles["table_APPID"]),
        (100, "AcDbSymbolTableRecord"),
        (100, "AcDbRegAppTableRecord"), (2, "ACAD"), (70, 0),
    ))
    _table_end(writer)

def _write_dimstyle_table(writer, handles):
    _table_start(
        writer, "DIMSTYLE", handles["table_DIMSTYLE"], 1,
        extra_subclass="AcDbDimStyleTable",
    )
    writer.extend((
        (0, "DIMSTYLE"), (105, handles["dimstyle_standard"]),
        (330, handles["table_DIMSTYLE"]),
        (100, "AcDbSymbolTableRecord"),
        (100, "AcDbDimStyleTableRecord"), (2, "Standard"), (70, 0),
        (3, ""), (4, ""), (40, 1.0), (41, 2.5), (42, 0.625),
        (43, 3.75), (44, 1.25), (45, 0.0), (46, 0.0), (47, 0.0),
        (48, 0.0), (140, 2.5), (141, 2.5), (142, 0.0),
        (143, 0.03937007874), (144, 1.0), (145, 0.0), (146, 1.0),
        (147, 0.625), (148, 0.0), (71, 0), (72, 0), (73, 0),
        (74, 0), (75, 0), (76, 0), (77, 1), (78, 8), (79, 3),
        (170, 0), (171, 3), (172, 1), (173, 0), (174, 0),
        (175, 0), (176, 0), (177, 0), (178, 0), (179, 2),
        (271, 2), (272, 2), (273, 2), (274, 3), (275, 0),
        (276, 0), (277, 2), (278, 44), (279, 0), (280, 0),
        (281, 0), (282, 0), (283, 0), (284, 8), (285, 0),
        (286, 0), (288, 0), (289, 3), (340, handles["style_standard"]),
        (371, -2), (372, -2),
    ))
    _table_end(writer)

def _write_block_record_table(writer, handles):
    _table_start(writer, "BLOCK_RECORD", handles["table_BLOCK_RECORD"], 2)
    for key, name in (
            ("block_record_model", "*Model_Space"),
            ("block_record_paper", "*Paper_Space")):
        writer.extend((
            (0, "BLOCK_RECORD"), (5, handles[key]),
            (330, handles["table_BLOCK_RECORD"]),
            (100, "AcDbSymbolTableRecord"),
            (100, "AcDbBlockTableRecord"), (2, name),
        ))
    _table_end(writer)

def _write_tables(writer, handles, layers, extents):
    writer.extend(((0, "SECTION"), (2, "TABLES")))
    _write_vport_table(writer, handles, extents)
    _write_ltype_table(writer, handles)
    _write_layer_table(writer, handles, layers)
    _write_style_table(writer, handles)
    _write_empty_table(writer, handles, "VIEW")
    _write_empty_table(writer, handles, "UCS")
    _write_appid_table(writer, handles)
    _write_dimstyle_table(writer, handles)
    _write_block_record_table(writer, handles)
    writer.add(0, "ENDSEC")

def _write_blocks(writer, handles):
    writer.extend(((0, "SECTION"), (2, "BLOCKS")))
    for prefix, name, owner in (
            ("model", "*Model_Space", handles["block_record_model"]),
            ("paper", "*Paper_Space", handles["block_record_paper"])):
        writer.extend((
            (0, "BLOCK"), (5, handles["block_" + prefix]),
            (330, owner), (100, "AcDbEntity"), (8, "0"),
            (100, "AcDbBlockBegin"), (2, name), (70, 0),
            (10, 0.0), (20, 0.0), (30, 0.0), (3, name), (1, ""),
            (0, "ENDBLK"), (5, handles["endblk_" + prefix]),
            (330, owner), (100, "AcDbEntity"), (8, "0"),
            (100, "AcDbBlockEnd"),
        ))
    writer.add(0, "ENDSEC")

def _write_hatch(writer, hatch, owner):
    writer.extend((
        (0, "HATCH"), (5, hatch["handle"]), (330, owner),
        (100, "AcDbEntity"), (8, hatch["encoded_layer"]),
        (410, "Model"), (370, -1), (100, "AcDbHatch"),
        (10, 0.0), (20, 0.0), (30, 0.0),
        (210, 0.0), (220, 0.0), (230, 1.0),
        (2, "SOLID"), (70, 1), (71, 0), (91, len(hatch["loops"])),
    ))
    for loop in hatch["loops"]:
        vertices = loop["vertices"]
        writer.add(92, int(loop["flags"]) & ~2)
        writer.add(93, len(vertices))
        for index, first in enumerate(vertices):
            second = vertices[(index + 1) % len(vertices)]
            writer.extend((
                (72, 1), (10, first[0]), (20, first[1]),
                (11, second[0]), (21, second[1]),
            ))
        writer.extend(((97, 0),))
    writer.extend((
        (75, 0), (76, 1), (98, 1),
        (10, hatch["seed"][0]), (20, hatch["seed"][1]),
    ))

def _write_outline(writer, loop, layer, handle, owner):
    vertices = loop["vertices"]
    writer.extend((
        (0, "LWPOLYLINE"), (5, handle), (330, owner),
        (100, "AcDbEntity"), (8, layer), (410, "Model"),
        (6, "CONTINUOUS"), (370, -1), (100, "AcDbPolyline"),
        (90, len(vertices)), (70, 1), (43, 0.0), (38, 0.0), (39, 0.0),
    ))
    for point in vertices:
        writer.extend(((10, point[0]), (20, point[1])))
    writer.extend(((210, 0.0), (220, 0.0), (230, 1.0)))

def _write_objects(writer, handles):
    writer.extend(((0, "SECTION"), (2, "OBJECTS")))
    writer.extend((
        (0, "DICTIONARY"), (5, handles["root_dictionary"]), (330, "0"),
        (100, "AcDbDictionary"), (281, 1),
        (3, "ACAD_GROUP"), (350, handles["group_dictionary"]),
        (3, "ACAD_PLOTSTYLENAME"),
        (350, handles["plotstyle_dictionary"]),
        (0, "DICTIONARY"), (5, handles["group_dictionary"]),
        (102, "{ACAD_REACTORS"),
        (330, handles["root_dictionary"]), (102, "}"),
        (330, handles["root_dictionary"]), (100, "AcDbDictionary"),
        (281, 1),
        (0, "ACDBDICTIONARYWDFLT"),
        (5, handles["plotstyle_dictionary"]),
        (102, "{ACAD_REACTORS"),
        (330, handles["root_dictionary"]), (102, "}"),
        (330, handles["root_dictionary"]), (100, "AcDbDictionary"),
        (281, 1), (3, "Normal"), (350, handles["plot_normal"]),
        (100, "AcDbDictionaryWithDefault"),
        (340, handles["plot_normal"]),
        (0, "ACDBPLACEHOLDER"), (5, handles["plot_normal"]),
        (102, "{ACAD_REACTORS"),
        (330, handles["plotstyle_dictionary"]), (102, "}"),
        (330, handles["plotstyle_dictionary"]),
    ))
    writer.add(0, "ENDSEC")
    writer.add(0, "EOF")
