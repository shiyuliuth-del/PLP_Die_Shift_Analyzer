"""Export-only layers and second-resolution runtime information."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

import gdstk
import numpy as np
import shapely
from shapely.geometry import box

from die_shift_studio import output_compensation
from die_shift_studio.core import build_case
from die_shift_studio.dxf_export import Snapshot,write
from die_shift_studio.dxf_settings import DxfSettings
from die_shift_studio.fast_preview import GeometryCache,PreviewEngine
from die_shift_studio.layer_roles import EXPORT_ONLY
from die_shift_studio.measured_core import Session,write_gds
from die_shift_studio.oas_stream import Store
from die_shift_studio.region_contract import _records
from die_shift_studio.region_export import write_regions
from die_shift_studio.runtime_log import LogBus
from die_shift_studio.settings import ViewSettings
from test_v38 import fixture


class V69Tests(TestCase):
    def test_region_export_uses_standard_dxf_entities_on_export_only_layers(self):
        copper=box(0,0,100,20)
        ring=box(140,0,180,40).difference(box(150,10,170,30))
        extras={'90/7':(ring.wkb,box(200,0,220,20).wkb)}
        snapshot=Snapshot({'5/0':(copper.wkb,),**extras,'91/0':(ring.wkb,)},'TOP',None,0)
        regions=dict(A=box(30,0,72,20),B=box(72,0,100,20),C=box(0,0,30,20))
        roles={'5/0':'固定RDL','90/7':EXPORT_ONLY,'91/0':'不参与'}
        settings=DxfSettings(scale=1.5,show_fill=True)
        compensation={'90/7':2.}

        def extra_records(path):
            return [(kind,[(code,value) for code,value in fields if code not in (5,330)])
                    for kind,fields in _records(path) if kind in ('LWPOLYLINE','HATCH')
                    and dict(fields).get(8)=='L90_D7']

        with TemporaryDirectory() as tmp:
            dest=Path(tmp)/'ABC.dxf'
            normal=Path(tmp)/'normal.dxf'
            info=write_regions(snapshot,{'pieces':[dict(layer='5/0',regions=regions)]},roles,dest,
                               settings=settings,compensation=compensation)
            standard=output_compensation.compensate_snapshot(Snapshot(extras,'TOP',None,0),compensation,.001)
            write(standard,normal,settings=settings)
            records=extra_records(dest)
            self.assertEqual(info['extra_layers'],1)
            self.assertEqual({kind for kind,_ in records},{'HATCH','LWPOLYLINE'})
            self.assertEqual(records,extra_records(normal))
            self.assertTrue(info['topology']['L5_D0']['closed'])
            self.assertNotIn('L91_D0',dest.read_text(encoding='ascii'))
            outline=next(fields for kind,fields in records if kind=='LWPOLYLINE')
            self.assertEqual({float(v) for code,v in outline if code==10},{198*1.5,222*1.5})

    def test_measured_exports_same_compensated_layer_in_each_pose(self):
        engine,data,_=fixture()
        tag='90/7'
        data.layers[tag]=[np.asarray(box(-15,-15,15,15).exterior.coords)]
        roles={**engine.roles,tag:EXPORT_ONLY}
        case=build_case(data,roles,layer_order=engine.connections.order)
        cache=GeometryCache(data)
        preview=PreviewEngine(cache,case,roles,{},engine.p,None,case['connections'])
        session=Session(data,case,roles,{},engine.p,ViewSettings(),cache,compensation={tag:2.})
        def geometry(layout,name):
            cell=next(cell for cell in layout.cells if cell.name==name)
            return shapely.union_all([shapely.Polygon(poly.points) for poly in cell.get_polygons(layer=90,datatype=7)])
        try:
            self.assertNotIn((tag,0),case['connections'].ids)
            self.assertNotIn(tag,preview.bound.transform(np.array([[4.,2.,0.]])))
            with patch.object(output_compensation,'sized',wraps=output_compensation.sized) as sizing, TemporaryDirectory() as tmp:
                session.prepare_output()
                nominal=session.nominal_snapshot()
                _,actual,_=session.run([dict(die=1,dx_um=4.,dy_um=2.,angle_deg=0.5)],preview=False)
                self.assertIs(nominal.layers[tag],actual.layers[tag])
                self.assertEqual(sizing.call_count,1)
                expected=box(-17,-17,17,17)
                dest=Path(tmp)/'pose.gds'
                write_gds(actual,dest,.001)
                self.assertTrue(geometry(gdstk.read_gds(dest),'MeasuredPackage').equals(expected))
                dest=Path(tmp)/'batch.oas'
                store=Store(tmp,.001)
                try:
                    store.add_nominal('Nominal',(0.,0.),(0.,0.),lambda:nominal.layers)
                    store.add('Shifted',actual.layers,(2000.,0.),(0.,0.))
                    store.write(dest)
                finally:store.close()
                layout=gdstk.read_oas(dest)
                for name in ('G_NOMINAL','Shifted'):
                    self.assertTrue(geometry(layout,name).equals(expected))
                top=next(cell for cell in layout.cells if cell.name=='TopCell')
                self.assertEqual({(ref.cell_name,ref.origin) for ref in top.references},
                                 {('G_NOMINAL',(0.,0.)),('Shifted',(2000.,0.))})
        finally:
            session.close()
            preview.close()
            engine.close()

    def test_runtime_timestamp_has_only_date_and_local_time(self):
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/'runtime.log'
            bus=LogBus(path=path)
            bus.emit('测试\n第二行','ERROR')
            _,seen=bus.read_after(0)
            self.assertRegex(seen[0],r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} \[ERROR\] 测试$')
            self.assertEqual(seen[0][:19],seen[1][:19])
            self.assertEqual(path.read_text(encoding='utf-8').splitlines(),seen)
