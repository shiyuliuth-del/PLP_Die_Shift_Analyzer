"""Run in KLayout: klayout -b -rd input_file=/path/design.gds -rd output_file=/path/check.json -r this.py"""
import json
import pya
layout=pya.Layout();layout.read(input_file);cell=layout.top_cell();out={}
for number in (3,5,7):
    region=pya.Region(cell.begin_shapes_rec(layout.layer(number,0)))
    merged=region.merged()
    out[str(number)]=dict(input=region.count(),merged=merged.count(),area_um2=merged.area()*layout.dbu**2,dbu=layout.dbu)
with open(output_file,'w',encoding='utf-8') as handle:json.dump(out,handle,indent=2)
