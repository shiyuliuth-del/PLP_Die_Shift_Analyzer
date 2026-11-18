"""Two real projects, sequential queue, simultaneous IPC and restart-safe receipts."""
import json
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from RealDataAnalyse.plans import Plans, capture
from RealDataAnalyse.excel import DEFAULT_PROFILE, generate_example
from RealDataAnalyse.storage import sha256
from die_shift_studio.oas_validation import verify_crc
import gdstk
ROOT=Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='rda-plans-process-') as name:
    root=Path(name).resolve();plans=Plans(root/'plans.sqlite');ids=[];sources=[]
    for index in range(2):
        folder=root/f'GD55 project {index+1}'
        shutil.copytree(Path(os.environ.get('REALDATA_TEST_CASE',ROOT/'Jobs/GD55')),folder)
        sources.append((folder/'GD55_1105.gds',sha256(folder/'GD55_1105.gds')))
        # Independent compact scheduling fixture. The shipped 36 rows are
        # imported once by the production acceptance, including all FAIL poses.
        files=generate_example(folder/'RealDataAnalyse/Excel/PlanInputs',count=2,die_count=3,pitch=(12000,12000))
        config=dict(folder=str(folder),project=str(folder/'GD55.dshift.json'),
                    template=str(folder/'RealDataAnalyse/Template/GD55_Template.gds'),
                    files=[dict(path=str(p),enabled=True) for p in files],
                    profile=DEFAULT_PROFILE,shared_mapping=True,
                    controls=dict(dies=3,workers=2,memory=512,retries=1,timeout=0))
        ids.append(plans.add(capture(config,threading.Event())))
    command=[os.environ['REALDATA_TEST_EXE']] if os.environ.get('REALDATA_TEST_EXE') else [sys.executable,str(ROOT/'RealDataAnalyse/main.py')]
    result=root/'result.json'
    completed=subprocess.run(command+['--run-plans',str(plans.path),'--result',str(result)],timeout=600)
    outcome=json.loads(result.read_text());assert completed.returncode==0 and outcome['completed']==2,outcome
    previous_end=0
    before=[]
    for plan_id in ids:
        row=plans.get(plan_id);output=Path(row['output'])
        assert row['status']=='completed' and row['progress']==100,row
        assert previous_end<=row['started']<=row['ended'];previous_end=row['ended']
        assert list(output.parent.iterdir())==[output],list(output.parent.iterdir())
        assert verify_crc(output)
        layout=gdstk.read_oas(output);top=layout.top_level()[0]
        assert top.name=='TopCell' and len(top.references)==3
        assert len({ref.cell_name for ref in top.references})==3 and any(ref.cell_name=='G_TEMPLATE' for ref in top.references)
        before.append((output,sha256(output),output.stat().st_mtime_ns))
        conversation=(Path(row['folder'])/'RealDataAnalyse/logs/IPC-conversation.log').read_text()
        assert 'batch.submit' in conversation and 'Die Shift Analyze → RealDataAnalyse' in conversation
        assert 'app.shutdown' in conversation
    repeated=subprocess.run(command+['--run-plans',str(plans.path),'--result',str(result)],timeout=60)
    assert repeated.returncode==0 and json.loads(result.read_text())['completed']==0
    for path,digest,mtime in before:assert sha256(path)==digest and path.stat().st_mtime_ns==mtime
    for path,digest in sources:assert sha256(path)==digest
print('GUI_REALDATA_PLANS_PROCESS_PASS: actual client sequential two projects, 4 packages, one template per project, independent OAS read/CRC, no extra Output files, no repeat, source unchanged')
