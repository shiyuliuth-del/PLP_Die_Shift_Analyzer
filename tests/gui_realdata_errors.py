"""Real GD55 mixed batch: valid/failing/valid packages, ERROR ownership, no loss."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import hashlib,json,math,shutil,sqlite3,subprocess,sys,tempfile,time
from pathlib import Path
from contextlib import closing
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from die_shift_studio.local_ipc import call
from die_shift_studio.oas_validation import verify_crc
from RealDataAnalyse.excel import DEFAULT_PROFILE
import klayout.db as k
ROOT=Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='rda-error-output-') as temp:
 root=Path(temp).resolve();folder=root/'GD55';shutil.copytree(Path(os.environ.get('REALDATA_TEST_CASE',ROOT/'Jobs/GD55')),folder)
 source=folder/'GD55_1105.gds';before=hashlib.sha256(source.read_bytes()).hexdigest()
 profile=dict(DEFAULT_PROFILE,sheet=os.environ.get('REALDATA_TEST_SHEET','FailureRecovery'))
 count={'FailureRecovery':6,'Ordinary':60,'Stress':42}[profile['sheet']]
 database=folder/'Tasks'/'run.sqlite';endpoint=root/'endpoint.json';output=folder/'Output'/'GD55.oas';result=root/'result.json'
 command=[os.environ['REALDATA_TEST_EXE']] if os.environ.get('REALDATA_TEST_EXE') else [sys.executable,str(ROOT/'RealDataAnalyse/main.py')]
 config=root/'import.json';config.write_text(json.dumps(dict(paths=[str(p) for p in sorted(folder.glob('*.xlsx'))],database=str(database),die_count=3,workers=3,profile=profile)),encoding='utf8')
 subprocess.run(command+['--import-data',str(config),'--result',str(result)],check=True,timeout=120)
 config=root/'run.json';config.write_text(json.dumps(dict(database=str(database),endpoint=str(endpoint),project=str(folder/'GD55.dshift.json'),output=str(output),workers=3,memory_mb=2048,retries=1,timeout=0)),encoding='utf8')
 try:
  completed=subprocess.run(command+['--run',str(config),'--result',str(result)],timeout=2400)
  outcome=json.loads(result.read_text());assert completed.returncode==0 and outcome['state']=='completed',outcome
  assert verify_crc(output) and list(output.parent.iterdir())==[output]
  with closing(sqlite3.connect(database)) as db:
   rows=[(seq,package,json.loads(payload),json.loads(receipt),state) for seq,package,payload,receipt,state in db.execute('select seq,package,payload,result,state from packages order by seq')]
  assert len(rows)==count and all(row[4].startswith('committed') for row in rows)
  layout=k.Layout();layout.read(str(output));top=layout.top_cell();assert top.name=='TopCell';assert sum(1 for _ in top.each_inst())==count
  error_indices=[i for i in layout.layer_indexes() if layout.get_info(i).name=='ERROR'];assert len(error_indices)==1;error_index=error_indices[0]
  issue_packages=0;rectangles=0;failed=[]
  for seq,package,payload,receipt,state in rows:
   assert receipt['fixed_rdl_drc_checked'] is True
   if receipt.get('connection_failure_count'):
    failed.append(package);assert receipt['partial_output'] is True and receipt['omitted_region_count']>0
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

   expected={};unique={}
   for issue in issues:
    x0,y0,x1,y1=issue['bounds_um'];dbu=layout.dbu
    a,b=math.floor(x0/dbu),math.floor(y0/dbu);c,d=max(a+1,math.ceil(x1/dbu)),max(b+1,math.ceil(y1/dbu))
    for die in issue['die_ids']:
     expected.setdefault(die,k.Region()).insert(k.Box(a,b,c,d));unique.setdefault(die,set()).add((a,b,c,d))
   for die in range(4):
    cell=layout.cell('G_ERROR_'+payload['cell']+f'_DIE_{die}')
    actual=k.Region(cell.shapes(error_index)) if cell else k.Region()
    assert (actual^expected.get(die,k.Region())).is_empty(),(package,die,'marker location differs from full catalogue')
    assert actual.count()==len(unique.get(die,())),(package,die,'duplicate/missing rectangles')
    for shape in (cell.shapes(error_index).each() if cell else []):
     assert shape.is_box() or (shape.is_polygon() and shape.polygon.area()==shape.bbox().area()),'ERROR must be rectangular'
   assert receipt['error_rectangles']==sum(len(s) for s in unique.values())
   issue_packages+=bool(issues);rectangles+=receipt['error_rectangles']
  assert outcome['issue_packages']==issue_packages and outcome['error_rectangles']==rectangles
  if profile['sheet']=='FailureRecovery':
   assert {'RECOVERY_002','RECOVERY_004','RECOVERY_005'}.issubset(failed),failed
   assert not rows[0][3]['connection_failure_count'] and not rows[-1][3]['connection_failure_count']
   first,last=layout.cell(rows[0][2]['cell']),layout.cell(rows[-1][2]['cell'])
   for layer in layout.layer_indexes():
    if layer!=error_index:assert (k.Region(first.begin_shapes_rec(layer))^k.Region(last.begin_shapes_rec(layer))).is_empty(),'later zero pose lost or contaminated copper'
  assert hashlib.sha256(source.read_bytes()).hexdigest()==before
  print(json.dumps(dict(status='REALDATA_ERROR_OUTPUT_PASS',sheet=profile['sheet'],packages=count,connection_failure_packages=failed,issue_packages=issue_packages,error_rectangles=rectangles,scope='Actual GD55, complete catalogue vs ERROR rectangles and DIE cells, zero replay copper XOR, source unchanged'),ensure_ascii=False),flush=True)
 finally:
  if endpoint.exists():
   import psutil
   descriptor=json.loads(endpoint.read_text());pid=descriptor['pid']
   try:process=psutil.Process(pid)
   except psutil.NoSuchProcess:process=None
   call(endpoint,'app.shutdown');end=time.monotonic()+90
   while process is not None and time.monotonic()<end:
    try:
     if not process.is_running() or process.status()==psutil.STATUS_ZOMBIE:break
    except psutil.NoSuchProcess:break
    time.sleep(.1)
   else:
    if process is not None:raise AssertionError('server did not exit safely')
