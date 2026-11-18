"""Real GD55 mixed batch: valid/failing/valid packages, ERROR ownership, no loss."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import hashlib,json,shutil,sqlite3,subprocess,sys,tempfile,time
from pathlib import Path
from contextlib import closing
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from die_shift_studio.local_ipc import call
from die_shift_studio.oas_validation import verify_crc
from RealDataAnalyse.projects import load_folder
from openpyxl import load_workbook
import gdstk
import klayout.db as k
import numpy as np
import shapely
from die_shift_studio.oas_errors import rectangles as error_rectangles
ROOT=Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='rda-error-output-') as temp:
 root=Path(temp).resolve();folder=root/'GD55';shutil.copytree(Path(os.environ.get('REALDATA_TEST_CASE',ROOT/'Jobs/GD55')),folder)
 source=folder/'GD55_1105.gds';before=hashlib.sha256(source.read_bytes()).hexdigest()
 project_config=load_folder(folder)
 paths=[Path(f['path']) for f in project_config['files'] if f['enabled']]
 template=Path(project_config['template'])
 source_digests={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in [source,template,*paths]}
 excel_rows={}
 for path in paths:
  book=load_workbook(path,read_only=True)
  try:
   assert book.sheetnames==['Measurements']
   for pid,die,dx,dy,angle,x,y,cx,cy in book.active.iter_rows(min_row=2,max_col=9,values_only=True):
    entry=excel_rows.setdefault(pid,dict(position_um=[x,y],dies={}))
    assert entry['position_um']==[x,y] and die not in entry['dies']
    pose=dict(die=die,dx_um=dx,dy_um=dy,angle_deg=angle)
    if cx is not None or cy is not None:pose['rotation_center_um']=[cx,cy]
    entry['dies'][die]=pose
  finally:book.close()
 count=len(excel_rows);assert count==36 and len(paths)==3
 database=folder/'Tasks'/'run.sqlite';endpoint=root/'endpoint.json';output=folder/'Output'/'GD55.oas';result=root/'result.json'
 command=[os.environ['REALDATA_TEST_EXE']] if os.environ.get('REALDATA_TEST_EXE') else [sys.executable,str(ROOT/'RealDataAnalyse/main.py')]
 config=root/'import.json';config.write_text(json.dumps(dict(paths=list(map(str,paths)),database=str(database),die_count=3,workers=3,profile=project_config['profile'])),encoding='utf8')
 subprocess.run(command+['--import-data',str(config),'--result',str(result)],check=True,timeout=120)
 settings=project_config['controls']
 config=root/'run.json';config.write_text(json.dumps(dict(database=str(database),endpoint=str(endpoint),project=project_config['project'],output=str(output),template=str(template),workers=settings['workers'],memory_mb=settings['memory'],retries=1,timeout=0)),encoding='utf8')
 try:
  completed=subprocess.run(command+['--run',str(config),'--result',str(result)],timeout=1500)
  outcome=json.loads(result.read_text());assert completed.returncode==0 and outcome['state']=='completed',outcome
  assert verify_crc(output) and list(output.parent.iterdir())==[output]
  with closing(sqlite3.connect(database)) as db:
   rows=[(seq,package,json.loads(payload),json.loads(receipt),state) for seq,package,payload,receipt,state in db.execute('select seq,package,payload,result,state from packages order by seq')]
  assert len(rows)==count and all(row[4].startswith('committed') for row in rows)
  layout=k.Layout();layout.read(str(output));top=layout.top_cell();assert top.name=='TopCell';assert sum(1 for inst in top.each_inst() if inst.cell.name.startswith('Package_'))==count
  assert sum(1 for inst in top.each_inst() if inst.cell.name=='G_TEMPLATE')==1
  error_indices=[i for i in layout.layer_indexes() if layout.get_info(i).name=='ERROR'];assert len(error_indices)==1;error_index=error_indices[0]
  server_info=call(endpoint,'info')['result'];board=server_info['board_bounds_um']
  assert server_info['parameters']['minimum_area_um2']==1
  assert json.loads(Path(project_config['project']).read_text())['settings']['drc_tolerance_um']==.5
  template_db=k.Layout();template_db.read(str(template))
  template_cell=layout.cell('G_TEMPLATE')
  for info in template_db.layer_infos():
   src=template_db.layer(info.layer,info.datatype);dst=layout.layer(info.layer,info.datatype)
   assert (k.Region(template_db.top_cell().begin_shapes_rec(src))^k.Region(template_cell.shapes(dst))).is_empty()
  assert sum(1 for q in template_cell.shapes(layout.layer(60001,0)).each() if q.is_text())==4
  placements={inst.cell.name:inst.dcplx_trans.disp for inst in top.each_inst()}
  fail_indices=[i for i in layout.layer_indexes() if layout.get_info(i).name=='FAIL'];assert len(fail_indices)==1
  fail_index=fail_indices[0];assert fail_index!=error_index
  original=k.Layout();original.read(str(source));original_top=original.cell(server_info['top_cell'])
  assert abs(original.dbu-layout.dbu)<1e-12
  expected_board=k.Region()
  for key,role in server_info['roles'].items():
   if role=='封装边框':expected_board+=k.Region(original_top.begin_shapes_rec(original.layer(*map(int,key.split('/')))))
  expected_board.merge();assert not expected_board.is_empty()
  expected_fail=k.Region()
  payload_cells={c.name:c for c in gdstk.read_oas(output).cells}
  issue_packages=0;rectangles=0;failed=[];rotated=0;retained_subgrid_angles=0
  for seq,package,payload,receipt,state in rows:
   assert receipt['fixed_rdl_drc_checked'] is True
   assert payload['position_um']==excel_rows[package]['position_um']
   assert {d['die']:d for d in payload['dies']}==excel_rows[package]['dies']
   origin=placements[payload['cell']]
   assert abs(origin.x-(payload['position_um'][0]-board[0]))<=layout.dbu
   assert abs(origin.y-(payload['position_um'][1]-board[1]))<=layout.dbu
   if receipt.get('connection_failure_count'):
    failed.append(package);assert receipt['partial_output'] is True and receipt['omitted_region_count']>0
   fail_cell=layout.cell('G_FAIL_'+payload['cell'])
   if receipt.get('connection_failure_count'):
    assert receipt['failed_packages']==1 and receipt['failure_layer']['name']=='FAIL'
    assert fail_cell and (k.Region(fail_cell.shapes(fail_index))^expected_board).is_empty(),'FAIL must preserve the real package outline'
    expected_fail+=expected_board.transformed(k.Trans(round(origin.x/layout.dbu),round(origin.y/layout.dbu)))
   else:assert fail_cell is None and receipt.get('failed_packages',0)==0,'DRC-only package must not enter FAIL'
   catalog=receipt['issue_catalog'];path=Path(catalog['path']).resolve();assert path.is_relative_to(folder/'Cache'),path
   with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
    issues=[json.loads(row[0]) for row in db.execute('select payload from issues where task_id=? order by ordinal',(catalog['task_id'],))]
   assert len(issues)==receipt['drc_count']==catalog['total']
   paged=[];offset=0
   while True:
    reply=call(endpoint,'task.issues',dict(task_id=catalog['task_id'],offset=offset,limit=128))
    assert reply['status']=='completed',reply
    page=reply['result'];paged.extend(page['items'])
    if page['next_offset'] is None:break
    assert page['next_offset']>offset;offset=page['next_offset']
   assert paged==issues,'IPC pagination omitted or changed diagnostic positions'

   # Complete-catalogue/ownership audit. Independent orientation and pose
   # expectations are covered by test_oriented_oas_errors' two-reader fixtures.
   expected={};unique={}
   for die,blobs in error_rectangles(issues,server_info['die_count'],layout.dbu,with_placements=True).items():
    region=k.Region();keys=set()
    for blob,offset,angle in blobs:
     xy=np.rint(shapely.get_coordinates(shapely.from_wkb(blob))/layout.dbu).astype(np.int64)
     polygon=k.Polygon([k.Point(int(x),int(y)) for x,y in xy])
     if angle:
      transform=k.ICplxTrans(1.,angle,False,round(offset[0]/layout.dbu),round(offset[1]/layout.dbu))
      polygon=polygon.transformed(transform)
     region.insert(polygon);keys.add((blob,offset,angle))
    expected[die]=region;unique[die]=keys
   for die in range(server_info['die_count']+1):
    cell=layout.cell('G_ERROR_'+payload['cell']+f'_DIE_{die}')
    actual=k.Region(cell.begin_shapes_rec(error_index)) if cell else k.Region()
    assert (actual^expected.get(die,k.Region())).is_empty(),(package,die,'marker location differs from full catalogue')
    if cell:
     pcell=payload_cells[cell.name]
     for ref in pcell.references:
      assert abs(ref.rotation)>0,'Subgrid ERROR lost its rotation'
      retained_subgrid_angles+=1
     polygons=pcell.get_polygons()
     assert len(polygons)==len(unique.get(die,())),(package,die,'duplicate/missing polygons')
     for polygon in polygons:
      assert len(polygon.points)==4,'ERROR must remain a quadrilateral'
    for shape in (cell.shapes(error_index).each() if cell else []):
     assert shape.is_box() or (shape.is_polygon() and shape.polygon.num_points_hull()==4),'ERROR must retain four oriented corners'
     if shape.is_polygon() and shape.polygon.area()<shape.bbox().area():rotated+=1
   assert receipt['error_rectangles']==sum(len(s) for s in unique.values())
   issue_packages+=bool(issues);rectangles+=receipt['error_rectangles']
  assert outcome['issue_packages']==issue_packages and outcome['error_rectangles']==rectangles
  assert outcome['failed_packages']==len(failed)
  assert (k.Region(top.begin_shapes_rec(fail_index))^expected_fail).is_empty(),'FAIL contains extra/missing package outlines'
  assert {'GD55_034','GD55_035','GD55_036'}.issubset(failed),failed
  assert not rows[0][3]['connection_failure_count'],rows[0][3]
  assert rotated>0,'No rotated ERROR polygons found in actual OAS'
  assert all(hashlib.sha256(p.read_bytes()).hexdigest()==digest for p,digest in source_digests.items())
  if os.environ.get('REALDATA_EVIDENCE_DIR'):
   evidence=Path(os.environ['REALDATA_EVIDENCE_DIR']);evidence.mkdir(parents=True,exist_ok=True)
   shutil.copy2(output,evidence/'GD55.oas')
   shutil.copy2(folder/'logs/RealDataAnalyse.log',evidence/'RealDataAnalyse.log')
  print(json.dumps(dict(status='REALDATA_ERROR_OUTPUT_PASS',packages=count,connection_failure_packages=failed,issue_packages=issue_packages,error_rectangles=rectangles,rotated_error_polygons=rotated,subgrid_rotated_references=retained_subgrid_angles,scope='Actual Excel read-only import, template once, complete ERROR/FAIL, exact package poses and positioning'),ensure_ascii=False),flush=True)
 finally:
  if endpoint.exists():
   import psutil
   descriptor=json.loads(endpoint.read_text());pid=descriptor['pid']
   processes=[]
   try:
    process=psutil.Process(pid);processes.append(process)
    parent=process.parent()
    if os.environ.get('DIE_SHIFT_TEST_EXE') and parent and Path(parent.exe()).resolve()==Path(os.environ['DIE_SHIFT_TEST_EXE']).resolve():processes.append(parent)
   except psutil.NoSuchProcess:pass
   call(endpoint,'app.shutdown');end=time.monotonic()+90
   while time.monotonic()<end:
    alive=[]
    for process in processes:
     try:
      if process.is_running() and process.status()!=psutil.STATUS_ZOMBIE:alive.append(process)
     except psutil.NoSuchProcess:pass
    if not alive:break
    time.sleep(.1)
   else:raise AssertionError('server and frozen launcher did not exit safely')
