"""Zero-view details carry a real complete pose, including independent blockers."""
from dataclasses import replace
from itertools import product
from unittest.mock import patch
from types import SimpleNamespace
import math,unittest
import numpy as np
import shapely
from shapely.geometry import box
from die_shift_studio.core import Parameters
from die_shift_studio.fast_preview import PreviewEngine
from die_shift_studio.statistics import ExtremeCatalog
from die_shift_studio.settings import ViewSettings
from die_shift_studio.width_analysis import Wall,Gate,BLOCKED,NARROW,ADEQUATE,section_subset,compatible_faces
from die_shift_studio.width_summary import summarize,_solve_throat
from die_shift_studio.width_witness import pose_text,edge_witness
from die_shift_studio.coupled_filter import reconstruction_edge_scopes,scan
from die_shift_studio.extreme_filter import ProblemIndex
from tests.test_v21 import example


class V29Tests(unittest.TestCase):
    def test_worst_site_and_values_replay_on_every_source(self):
        data,job,case,p,old=example('示例O_运动网络三线路通道');old.close()
        for level in (0,2,math.inf):
            p=replace(p,target_nsigma=level,per_die_enabled=True,die_bounds=[[35,0,.7],[0,10,0]])
            engine=PreviewEngine(old.contact.cache,case,job['roles'],{},p,None,case['connections'])
            try:
                engine.configure_width(True,ViewSettings());grid=ExtremeCatalog(case,p);s=summarize(engine.width_analyzer,p)
                self.assertTrue(s['lines']);self.assertEqual(set(s['lines']),set(s['witnesses']))
                for ref,item in s['witnesses'].items():
                    z=grid[item['index']]['states'] if item['index']>=0 else np.zeros((grid.count,3))
                    w=engine.frame(z)['width_analysis'];text=pose_text(s['grid_key'],item['index'])
                    self.assertIn('DX',text);self.assertIn('θ',text)
                    if item['kind']=='edge':
                        self.assertTrue(any(e['layer']==ref[0] and ref[1] in e['indices'] and e['gap_um']==item['gap_um'] for e in w['edge_violations']));continue
                    candidates=[r for r in w['rows'] if ref in r['refs'] and r['pair']==item['pair'] and np.allclose(r['nominal_witness_um'],item['nominal_ends'])]
                    observed=next(r for r in candidates if r['status']==s['lines'][ref][0])
                    for key in ('original','required','current','gap_um','narrowing_um'):
                        self.assertAlmostEqual(item[key],observed[key])
                if level==0:
                    for digits in product(*(range(v) for v in grid.radices)):
                        z=np.array([v[i] for v,i in zip(grid.locals,digits)])
                        for row in engine.frame(z)['width_analysis']['rows']:
                            for ref in row['refs']:
                                if row['status']==ADEQUATE or (s['lines'][ref][0]!=row['status']):continue
                                slack=row['minimum_um' if row['status']==BLOCKED else 'keep_um']-row['gap_um']
                                self.assertGreaterEqual(s['witnesses'][ref]['shortfall_um']+1.e-8,slack)
            finally:engine.close()

    def test_witness_includes_third_die_that_opens_the_throat(self):
        data,job,c,p,old=example('示例O_运动网络三线路通道')
        p=replace(p,target_nsigma=0,per_die_enabled=True,die_bounds=[[35,0,0],[0,60,0]],line_width_um=10,line_space_um=10)
        e=PreviewEngine(old.contact.cache,c,job['roles'],{},p,None,c['connections']);old.close()
        try:
            e.configure_width(True,ViewSettings());wa=e.width_analyzer;grid=ExtremeCatalog(c,p);wa.inside=box(-500,-500,500,500)
            walls=[Wall('A','运动过孔孔盘',box(-5,-5,0,5),(),0),Wall('B','固定过孔孔盘',box(80,-5,85,5),()),Wall('C','运动过孔孔盘',box(54,-4,59,4),(),1)]
            gate=Gate((0,1),(0,1),np.array([[0.,0],[80.,0]]),False,(10.,10.),(37.,62.),((('5/0',0),),(('5/0',1),)),80.,(0,1,0,0))
            plane=dict(gates=[gate],pieces=np.array([w.geometry for w in walls],object),parents=np.arange(3),
                       local=np.zeros(3,bool),walls=walls,metal_count=3,keys=('5/0',))
            found={};witnesses={};clauses=set()
            _solve_throat(wa,'5',plane,grid,section_subset(plane,[0],{0,1}),{2},False,found,witnesses,clauses,True,lambda *_:None)
            item=witnesses['5/0',0];z=grid[item['index']]['states']
            self.assertEqual(z[0,0],35);self.assertNotEqual(z[1,1],0);self.assertEqual(item['current'],1)
            flags=ProblemIndex(grid,clauses)
            for ds in product(*(range(n) for n in grid.radices)):
                state=np.array([v[d] for v,d in zip(grid.locals,ds)])
                rows,_=wa._measure('5',plane,state,lambda *_:None)
                self.assertEqual(flags.matches(ds),any(r['status']==BLOCKED for r in rows))
        finally:e.close()

    def test_rebuild_certificate_allows_only_true_board_clearance(self):
        p=Parameters(single_bound_x_um=35,single_bound_y_um=0,bound_angle_deg=0,line_space_um=15)
        grid=ExtremeCatalog({'dies':[0]},p)
        node=dict(geometry=[box(-10,-5,10,5)])
        model=SimpleNamespace(roles={'5/0':'固定RDL'},case={'board':box(-90,-90,90,90)},cache=SimpleNamespace(layers={'5/0':node}),bound=SimpleNamespace(centers=np.zeros((1,2))))
        recon=SimpleNamespace(vias=[dict(owner=0,targets=[('5/0',0)])],width_um=0.,tol=.001,settings=ViewSettings())
        e=SimpleNamespace(p=p,contact=model,reconstructor=recon,deformer=None)
        check=lambda:not reconstruction_edge_scopes(e,grid,lambda *_:None)
        self.assertTrue(check())
        recon.width_um=10;self.assertFalse(check());recon.width_um=0
        model.case['board']=model.case['board'].difference(box(25,-6,30,6))
        self.assertFalse(check())
        model.case['board']=box(-45,-30,45,30);self.assertFalse(check())

    def test_reconstruction_fast_path_vs_concrete_copper(self):
        _,job,c,p,old=example('示例N_三线路窄通道宽度')
        p=replace(p,target_nsigma=math.inf,per_die_enabled=True,die_bounds=[[35,0,0],[0,0,0]])
        e=PreviewEngine(old.contact.cache,c,job['roles'],{},p,None,c['connections']);old.close();e.signature='scan'
        try:
            cfg=ViewSettings();e.configure_width(True,cfg);e.configure_reconstruction(True,cfg);g=ExtremeCatalog(c,p)
            with patch.object(e.contact,'current',side_effect=AssertionError('unused contact computation')):
                result=scan(e,g,cfg,True,None,lambda *_:None)
            self.assertTrue(result['edge_certified']);idx=ProblemIndex(g,result['red_factors'])
            from die_shift_studio.reconstruction_events import current_failures
            for i in range(-1,g.total):
                ds=[0]*g.count if i<0 else g.choices(i)
                z=np.array([v[k] for v,k in zip(g.locals,ds)])
                frame=e.frame(z);w=frame['width_analysis']
                self.assertEqual(idx.matches(ds),bool(current_failures(frame['reconstruction'])))
                self.assertFalse(w['edge_violations'])
        finally:e.close()

    def test_source_partition_reuses_own_pose_and_bypasses_changed_base(self):
        from tests.test_reconstruction import example as reconstruct_example,CHANNEL
        eng,_,case=reconstruct_example(CHANNEL);r=eng.reconstructor
        z=np.array([[35.,0.,0.],[-35.,0.,0.]])
        try:
            targets=[ref for ref,vs in r.vias_by_target.items() if all(v['owner']==0 for v in vs)]
            self.assertTrue(targets)
            subset=r.frame(z,targets=targets)
            full=r.frame(z)
            for layer,i in targets:
                select=lambda m:next(row['geometry'] for row in m['replacements'][layer] if i in row['indices'])
                self.assertTrue(select(subset).equals(select(full)))
            peer=z.copy();peer[1]=[5.,5.,.01]
            plans=r._plans
            again=r.frame(peer,targets=targets)
            self.assertIs(r._plans,plans)
            for layer,i in targets:self.assertTrue(select(again).equals(select(full)))
        finally:eng.close()

    def test_near_edge_uses_actual_replacement_and_its_full_pose(self):
        p=Parameters(multi_bound_x_um=35,multi_bound_y_um=0,bound_angle_deg=0,line_space_um=15)
        g=ExtremeCatalog({'dies':[0,1,2]},p);cfg=ViewSettings()
        original=box(10,0,23,6);layer='5/0'
        model=SimpleNamespace(case={'board':box(-60,-45,60,45)},roles={layer:'固定RDL'},data=SimpleNamespace(dbu_um=.001),
            cache=SimpleNamespace(layers={layer:dict(geometry=np.array([original],object))}),
            ids={(layer,0):0},nodes=[dict(owner=-1,net=0,poly=original)],bound=SimpleNamespace(centers=np.zeros((1,2))))
        def rebuild(z,_deform,_progress,*,targets):
            self.assertEqual(targets,[(layer,0)])
            return dict(replacements={layer:[dict(indices=(0,),geometry=box(10+z[0,0],0,23+z[0,0],6))]})
        recon=SimpleNamespace(vias=[dict(owner=0,targets=[(layer,0)])],width_um=0.,tol=.001,settings=cfg,frame=rebuild)
        base=dict(channel_lines={},channel_witnesses={},channel_factors=[],grid_key=tuple(v.tobytes() for v in g.locals),has_edge_risk=False)
        engine=SimpleNamespace(p=p,reconstructor=recon,deformer=None,contact=model,width_summary=lambda _:base,
            width_analyzer=SimpleNamespace(probability=None),final_copper=PreviewEngine.final_copper)
        # This narrow V29 unit checks the legacy straight-copper boundary
        # helper. V40's public scan is checked against real native frames above.
        from die_shift_studio.coupled_filter import scan_reconstruction
        result=scan_reconstruction(engine,g,cfg,base,lambda *_:None)
        self.assertEqual(result['scanned'],3);idx=ProblemIndex(g,result['red_factors'])
        expected=[i for i in range(g.total) if g[i]['states'][0,0]==35.]
        self.assertEqual([idx.at(i) for i in range(idx.total)],expected)
        self.assertEqual((result['scanned'],result['package_states']),(3,27))
        witness=result['witnesses'][layer,0]
        self.assertEqual((witness['kind'],witness['gap_um'],witness['required_um'],witness['index']),('edge',2,15,g.index([2,0,0])))
        self.assertIn('DX +35 μm',pose_text(result['grid_key'],witness['index']))

    def test_edge_reference_point_undoes_rotation_with_offset_pivot(self):
        m=SimpleNamespace(roles={'5/0':'运动RDL'},nodes=[{'owner':0}],ids={('5/0',3):0},bound=SimpleNamespace(centers=np.array([[5.,5.]])))
        item=edge_witness(dict(layer='5/0',indices=[3],point=[37.,-5.],gap_um=2.,required_um=15.),4,m,np.array([[35.,-10.,math.pi/2]]))
        np.testing.assert_allclose(item['nominal_point'],[5,8]);self.assertEqual(item['shortfall_um'],13)

    def test_shared_frame_ram_is_bounded_and_parking_keeps_disk_and_zero(self):
        from die_shift_studio.session_cache import EphemeralCache,ProjectCacheRegistry,SessionCache
        registry=ProjectCacheRegistry();group=registry.get('memory');group.frame_ram=EphemeralCache(2600,2)
        a=SessionCache(group=group,frame_ram=group.shared_frame_ram())
        b=SessionCache(group=group,frame_ram=group.shared_frame_ram())
        try:
            a['a']=b'x'*280;b['b']=b'y'*280
            epoch=group.revision
            with patch('die_shift_studio.session_cache.pickle.loads',side_effect=AssertionError('read from disk')):
                self.assertEqual(a['a'],b'x'*280)
            self.assertEqual(group.revision,epoch)
            a['c']=b'z'*280
            self.assertLessEqual(group.frame_ram.used,group.frame_ram.budget)
            self.assertNotIn((id(b),'b'),group.frame_ram.memory)
            a.pin('a');group.park_frames();self.assertEqual(group.frame_ram.used,0)
            epoch=group.revision
            with patch('die_shift_studio.session_cache.pickle.loads',side_effect=AssertionError('zero or marked frame was evicted')):
                self.assertEqual(a['a'],b'x'*280)
            self.assertEqual(b['b'],b'y'*280);self.assertEqual(group.revision,epoch)
            group.clear();self.assertEqual(group.frame_ram.used,0);self.assertFalse(a.memory)
        finally:registry.close()

    def test_closing_one_engine_keeps_shared_completed_frames(self):
        from die_shift_studio.session_cache import ProjectCacheRegistry
        _,job,case,p,old=example('示例O_运动网络三线路通道')
        registry=ProjectCacheRegistry();group=registry.get('same-engine-inputs')
        def engine():
            return PreviewEngine(old.contact.cache,case,job['roles'],{},p,None,case['connections'],group,'same-inputs')
        first,second=engine(),engine();old.close()
        try:
            z=np.zeros((len(case['dies']),3));z[0,0]=12.
            initial=first.frame(z)
            self.assertIs(first.frames,second.frames)
            epoch=group.revision;first.close()
            with patch.object(second.contact,'current',side_effect=AssertionError('shared pose was recomputed')):
                replay=second.frame(z)
            self.assertEqual(group.revision,epoch);np.testing.assert_array_equal(initial['states'],replay['states'])
        finally:first.close();second.close();registry.close()

    def test_tangent_prefilter_retains_seam_candidates(self):
        for delta in (-1.1e-6,0.,1.1e-6,math.pi/2,math.pi-.2e-6):
            n=np.array([math.sin(delta),-math.cos(delta)])
            period=round(math.pi*1e6);key=round((delta%math.pi)*1e6)%period
            index=np.array(sorted([key,period+(key+1)%period]))
            self.assertTrue(compatible_faces(0,np.array([1]),np.array([[[0,0],n]]),index)[0])
