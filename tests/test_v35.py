"""Rare-event weighting, exact finite-grid unions and straight DXF output."""
from dataclasses import replace
from itertools import product
from pathlib import Path
import builtins,math,pickle,tempfile,unittest
from unittest.mock import patch
import numpy as np
import shapely
from shapely.geometry import box
from scipy.special import ndtr
from die_shift_studio.settings import ViewSettings
from die_shift_studio.core import Parameters
from die_shift_studio.width_sampling import mixture_batches
from die_shift_studio.width_grid import WidthGrid
from die_shift_studio.width_yield import packed_events,policy_yields,measure_width_yield,policy_texts
from die_shift_studio.width_analysis import ADEQUATE
from die_shift_studio.dxf_export import Snapshot,snapshot,write
from test_v21 import example


class SamplingTests(unittest.TestCase):
    def test_rare_tail_mass_independent_dies_and_shared_state(self):
        p=replace(Parameters(),multi_bound_x_um=6,multi_bound_y_um=0,bound_angle_deg=0,target_nsigma=6)
        cfg=replace(ViewSettings(),width_yield_exponent=16)
        batches=list(mixture_batches(p,2,cfg))
        states=np.concatenate([x[0] for x in batches]);w=np.concatenate([x[1] for x in batches])
        self.assertTrue(np.all(abs(states[:,:,0])<=6));self.assertTrue(np.all(states[:,:,1:]==0))
        self.assertTrue(np.all(w>0));self.assertLessEqual(w.max(),batches[0][2])
        self.assertAlmostEqual(w.mean(),1,delta=.015)
        joint=np.average((states[:,0,0]>0)&(states[:,1,0]>0),weights=w)
        self.assertAlmostEqual(joint,.25,delta=.012)
        tail=np.average(states[:,0,0]>5,weights=w)
        expected=(ndtr(-5)-ndtr(-6))/(ndtr(6)-ndtr(-6))
        self.assertGreater(np.count_nonzero(states[:,0,0]>5),500)
        self.assertAlmostEqual(tail,expected,delta=expected*.14)

    def test_correlated_xy_and_singular_support(self):
        from scipy.integrate import quad
        k=2.;q=ndtr(k)-ndtr(-k)
        for rho in (.7,1.,-1.):
            p=replace(Parameters(),single_bound_x_um=2,single_bound_y_um=2,bound_angle_deg=0,target_nsigma=k,rho_xy=rho)
            z=[];weights=[]
            for states,w,bound,_ in mixture_batches(p,1,replace(ViewSettings(),width_yield_exponent=15)):
                z.extend(states[:,0]);weights.extend(w)
                self.assertLessEqual(w.max(),bound*(1+1e-12))
            z=np.array(z);weights=np.array(weights)
            if abs(rho)==1:
                np.testing.assert_allclose(z[:,1],rho*z[:,0],atol=1e-12)
                expected=.5 if rho==1 else 0.
            else:
                s=math.sqrt(1-rho*rho)
                f=lambda x:math.exp(-x*x/2)/math.sqrt(2*math.pi)
                norm=quad(lambda x:f(x)*(ndtr((k-rho*x)/s)-ndtr((-k-rho*x)/s)),-k,k)[0]
                expected=quad(lambda x:f(x)*(ndtr((k-rho*x)/s)-ndtr(-rho*x/s)),0,k)[0]/norm
            actual=np.average((z[:,0]>0)&(z[:,1]>0),weights=weights)
            self.assertAlmostEqual(actual,expected,delta=.012)
        p=replace(p,per_die_enabled=True,die_bounds=[[0,0,0],[3,0,0]],mean_x_um=9)
        z=np.concatenate([x[0] for x in mixture_batches(p,2,replace(ViewSettings(),width_yield_exponent=8))])
        self.assertFalse(z[:,0].any());self.assertFalse(z[:,1,1:].any())

    def test_ignore_growth_is_monotonic_but_correlated_events_can_equal_early(self):
        a,b,c=('5/0',0),('5/0',1),('5/0',2)
        bank=dict(method='importance',total=4,weight_bound=2.,
                  chunks=(packed_events([set(),{a},{a,b},{c}],[set(),set(),set(),set()],np.array([1.,.2,.1,1e-20])),))
        q=[policy_yields(bank,s) for s in ((),(b,),(a,b),(a,b,c))]
        self.assertTrue(all(q[i]['values'][0]>=q[i+1]['values'][0] for i in range(3)))
        self.assertGreater(q[2]['gain_probability'],0)  # independently accumulated tiny tail
        self.assertEqual(q[3]['gain_probability'],0)
        self.assertEqual(q[3]['values'][0],q[3]['values'][1])
        same=dict(method='exact',total=2,chunks=(packed_events([{a,b},set()],[set(),set()]),))
        # This is a legitimate equality with only one of two lines ignored.
        np.testing.assert_allclose(policy_yields(same,[a])['values'],[.5,.5])


class PruningTests(unittest.TestCase):
    def test_reachable_wall_pruning_matches_full_wall_oracle(self):
        from die_shift_studio.width_yield_geometry import WidthEvents
        from die_shift_studio.width_analysis import WidthAnalyzer
        from die_shift_studio.statistics import mean_and_bounds
        for name in ('示例O_运动网络三线路通道','示例P_单点PATH与相邻孔盘','示例I_旋转中心与全范围概率'):
            _,_,case,p,e=example(name)
            a=WidthAnalyzer(e.contact,p,ViewSettings());b=WidthAnalyzer(e.contact,p,ViewSettings())
            try:
                compact=WidthEvents(a,p,lambda *_:None)
                with patch('die_shift_studio.width_yield_geometry.relevant_walls',side_effect=lambda plane,*args:set(range(len(plane['walls'])))):
                    complete=WidthEvents(b,p,lambda *_:None)
                count=len(case['dies'])
                mu,bounds=np.array([mean_and_bounds(p.for_die(i),count) for i in range(count)]).transpose(1,0,2)
                z=mu+np.random.default_rng(35).uniform(-1,1,(40,count,3))*bounds
                z[0]=0;z[1]=mu+bounds;z[2]=mu-bounds
                self.assertEqual(compact.classify(z),complete.classify(z),name)
            finally:a.close();b.close();e.close()


class GridTests(unittest.TestCase):
    def test_whole_grid_equals_explicit_joint_event_enumeration(self):
        _,_,case,p,e=example('示例O_运动网络三线路通道')
        p=replace(p,multi_bound_y_um=0,bound_angle_deg=0,target_nsigma=2)
        cfg=replace(ViewSettings(),width_yield_method='grid',width_grid_nodes=5)
        try:
            with patch('numpy.random.default_rng',side_effect=AssertionError('random sampling in exhaustive mode')):
                bank=measure_width_yield(e.contact,p,cfg,None,lambda *_:None)
            e.configure_width(True,cfg)
            grid=bank['grid'];oracle={};all_mass=0.
            for digits in product(*(range(r) for r in grid.radices)):
                z=np.array([q[i] for q,i in zip(grid.locals,digits)])
                frame=e.frame(z)['width_analysis']
                refs={ref for row in frame['rows'] if row['status']!=ADEQUATE for ref in row['refs']}
                refs.update((r['layer'],i) for r in frame['edge_violations'] for i in r['indices'])
                mass=math.prod(w[i] for w,i in zip(grid.weights,digits))
                if refs:all_mass+=mass
                oracle[digits]=(refs,mass)
                for ref,clauses in bank['by_line'].items():
                    present=any(all(digits[axis] in allowed for axis,allowed in clause.allowed) for clause in clauses)
                    self.assertEqual(present,ref in refs,(digits,ref))
            refs=sorted(bank['by_line'])
            for ignored in ((),refs[:1],refs):
                q=policy_yields(bank,ignored)
                loss=sum(m for r,m in oracle.values() if set(ignored)&r)
                np.testing.assert_allclose(q['failure_probability'],[loss,all_mass],atol=1e-12)
            reread=pickle.loads(pickle.dumps(bank))
            np.testing.assert_allclose(policy_yields(reread,refs)['values'],policy_yields(bank,refs)['values'])
        finally:e.close()

    def test_grid_probability_weights_and_old_settings(self):
        p=replace(Parameters(),per_die_enabled=True,die_bounds=[[6,0,0],[0,0,0]],target_nsigma=6,mean_x_um=2)
        g=WidthGrid(p,2,5)
        self.assertEqual(g.combinations,5)
        self.assertEqual(g.radices[1],1)
        for w in g.weights:self.assertAlmostEqual(w.sum(),1.)
        positive=g.weights[0]>0;z=g.locals[0][positive,0]
        np.testing.assert_allclose(z,[-4,-1,2,5,8])
        expected=np.diff(ndtr(np.array([-6,-4.5,-1.5,1.5,4.5,6])))/(ndtr(6)-ndtr(-6))
        np.testing.assert_allclose(g.weights[0][positive],expected,atol=1e-15)
        old=ViewSettings.from_dict({'yield_method':'partition','yield_sample_exponent':10,'random_seed':17})
        self.assertEqual((old.width_yield_method,old.width_yield_exponent,old.width_yield_seed),('grid',10,17))
        for rho in (1,-1):
            h=WidthGrid(replace(Parameters(),single_bound_x_um=2,single_bound_y_um=2,bound_angle_deg=0,rho_xy=rho,target_nsigma=2),1,5)
            self.assertEqual(h.combinations,5)
            np.testing.assert_allclose(h.locals[0][:,1],rho*h.locals[0][:,0],atol=1e-10)


class DxfTests(unittest.TestCase):
    def test_holes_units_ascii_and_exact_straight_nodes(self):
        a=box(-20,-10,20,10).difference(box(-5,-3,5,3))
        b=shapely.Polygon([(40.,0),(55.125,0),(55.125,12.875),(40.,12.875)])
        snap=Snapshot({'5/0':(a.wkb,b.wkb)},'测试顶层',None,0)
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'测试 输出.dxf'
            original=builtins.__import__
            def no_klayout(name,*args,**kwargs):
                if name=='pya' or name.startswith('klayout'):raise AssertionError('DXF must not require KLayout')
                return original(name,*args,**kwargs)
            with patch('builtins.__import__',side_effect=no_klayout):r=write(snap,p)
            self.assertEqual(r['polygons'],2)
            raw=p.read_bytes();self.assertNotIn(b'\n',raw.replace(b'\r\n',b''));raw.decode('ascii')
            for kind in (b'\r\nARC\r\n',b'\r\nCIRCLE\r\n'):self.assertNotIn(kind,raw)
            import klayout.db as k
            options=k.LoadLayoutOptions();options.dxf_polyline_mode=2
            layout=k.Layout();layout.read(str(p),options);top=layout.top_cell()
            index=next(i for i in layout.layer_indices() if layout.get_info(i).layer==5 and layout.get_info(i).datatype==0)
            result=k.Region(top.begin_shapes_rec(index))
            expected=k.Region()
            for poly in (a,b):
                target=k.DPolygon([k.DPoint(*xy) for xy in poly.exterior.coords[:-1]])
                for hole in poly.interiors:target.insert_hole([k.DPoint(*xy) for xy in hole.coords[:-1]])
                expected.insert(target.to_itype(layout.dbu))
            self.assertTrue((result^expected).is_empty())
            try:import ezdxf
            except ImportError:return
            document=ezdxf.readfile(p)
            self.assertEqual(document.units,13);self.assertFalse(document.audit().errors)
            line=list(document.modelspace().query('LWPOLYLINE'))[0]
            np.testing.assert_array_equal(np.array(line.get_points('xy')),np.array(b.exterior.coords)[:-1])
            self.assertTrue(line.closed)

    def test_snapshot_uses_edits_pose_replacements_patches_and_all_layers(self):
        from die_shift_studio.gds_shapes import edited_snapshot,node_fingerprint,shape_nodes
        from die_shift_studio.fast_preview import GeometryCache
        from types import SimpleNamespace
        data,_,case,p,e=example('示例O_运动网络三线路通道')
        try:
            z=np.zeros((len(case['dies']),3));z[0]=[11,-7,math.radians(3)]
            f=e.frame(z);d=snapshot(data,e.contact.cache,f)
            for key,groups in d.layers.items():
                for i,wkb in enumerate(groups):
                    expected=shapely.Polygon(f['layers'].get(key,{}).get('polygons',data.layers[key])[i])
                    actual=shapely.from_wkb(wkb)
                    self.assertLess(expected.symmetric_difference(actual).area,1e-7)
            kind,xy,source=shape_nodes(data,'4/0',0)
            edit=dict(layer='4/0',index=0,original=node_fingerprint(kind,xy),nodes=(xy+[2.,0]).tolist())
            edited=edited_snapshot(data,[edit]);changed=snapshot(edited,GeometryCache(edited))
            self.assertTrue(shapely.from_wkb(changed.layers['4/0'][0]).equals(shapely.Polygon(edited.layers['4/0'][0])))
            np.testing.assert_array_equal(shape_nodes(data,'4/0',0)[1],xy)
            changes={'5/0':[dict(indices=(0,),geometry=box(210,200,230,220))]}
            f=dict(f,deformation={'replacements':changes})
            repair=SimpleNamespace(layer='5/0',patch=box(225,200,240,210))
            d=snapshot(data,e.contact.cache,f,deformation=True,repairs={'repairs':[repair]})
            copper=[shapely.from_wkb(wkb) for wkb in d.layers['5/0']]
            self.assertTrue(any(g.equals(changes['5/0'][0]['geometry']) for g in copper))
            self.assertTrue(any(g.equals(repair.patch) for g in copper))
            self.assertEqual(len(copper),len(data.layers['5/0'])+1)
            self.assertEqual(set(d.layers),set(data.layers))
        finally:e.close()

    def test_failed_export_preserves_existing_file(self):
        s=Snapshot({'1/0':(box(0,0,10,10).wkb,)},'TOP',None,0)
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'old.dxf';p.write_bytes(b'previous file')
            def cancel(n,text):
                if n>=15:raise InterruptedError('cancel')
            with self.assertRaises(InterruptedError):write(s,p,cancel)
            self.assertEqual(p.read_bytes(),b'previous file')
            self.assertEqual(list(Path(folder).iterdir()),[p])


if __name__=='__main__':unittest.main()
