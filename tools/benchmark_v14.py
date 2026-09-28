"""Compare native module and persistent desktop cold/repeat preview costs."""
import os,sys,json,time,statistics
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from tools.benchmark_v13 import run
from die_shift_studio import native_backend
from die_shift_studio.resources import resource_profile
import numpy as np

def main():
    q=json.loads((ROOT/'examples/示例G_小偏移与三段追溯.json').read_text());result=dict(version='V14',hardware=resource_profile(),scope='Linux CPU, sample G two nets; desktop uses the installed KLayout executable. Timings exclude drawing and are single cold runs.')
    for backend in ('module','desktop'):
        native_backend.close();os.environ['DIE_SHIFT_NATIVE_BACKEND']=backend
        start=time.perf_counter();info=native_backend.backend().info;startup=(time.perf_counter()-start)*1000
        result[backend]=dict(info=info,startup_ms=startup,**run(ROOT/'examples'/q['gds_path'],q,np.array([[1.,1,0],[0,35,0]]),version='V14'))
    native_backend.close();os.environ.pop('DIE_SHIFT_NATIVE_BACKEND',None)
    (ROOT/'results/performance_V14.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print('V14_BACKEND_BENCHMARK_PASS')
if __name__=='__main__':main()
