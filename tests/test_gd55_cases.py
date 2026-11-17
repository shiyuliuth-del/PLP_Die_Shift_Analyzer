"""Bundled synthetic XLSX match the independent, deterministic pose manifest."""
import copy,json,tempfile,threading,unittest
from pathlib import Path
from contextlib import closing
from RealDataAnalyse.excel import import_files,DEFAULT_PROFILE
from RealDataAnalyse.storage import Ledger
from tools.gd55_stress_cases import cases,recovery_cases,ordinary_cases

class GD55Cases(unittest.TestCase):
 def test_extended_sheets_match_all_dies_and_positions(self):
  root=Path(__file__).resolve().parents[1];files=sorted((root/'Jobs/GD55').glob('*.xlsx'))
  self.assertEqual(len(files),3)
  for sheet,expected in [('Ordinary',ordinary_cases()),('Stress',cases()),('FailureRecovery',recovery_cases())]:
   with self.subTest(sheet=sheet),tempfile.TemporaryDirectory() as temp:
    profile=copy.deepcopy(DEFAULT_PROFILE);profile['sheet']=sheet;database=Path(temp)/'tasks.sqlite'
    info=import_files(files,database,3,3,threading.Event(),profile=profile)
    self.assertEqual(info['packages'],len(expected))
    with closing(Ledger(database)) as ledger:
     actual=[(key,json.loads(data)) for key,data in ledger.db.execute('select package,payload from packages order by seq')]
    for (key,data),case in zip(actual,expected):
     self.assertEqual(key,case['id']);self.assertEqual(data['position_um'],case['position_um'])
     for i,die in enumerate(data['dies']):
      self.assertEqual([die['dx_um'],die['dy_um'],die['angle_deg']],case['poses'][i])
      self.assertEqual(die.get('rotation_center_um'),case['centers'][i])
 def test_stress_has_42_distinct_cases_and_three_pinned_failure_inputs(self):
  rows=cases();self.assertEqual(len(rows),42);self.assertEqual(len({r['id'] for r in rows}),42)
  failures=[r for r in rows if 'ForeignPadContact' in r['name']];self.assertEqual(len(failures),3)
  self.assertEqual([r['poses'][i][:2] for i,r in enumerate(failures)],[[99.996,-111.600],[86.970,100.220],[115.653,-124.732]])
  recovery=recovery_cases();self.assertEqual(len(recovery),6)
  self.assertEqual(recovery[0]['poses'],recovery[-1]['poses']);self.assertTrue(all(v==0 for p in recovery[-1]['poses'] for v in p))

 def test_every_ordinary_position_and_die_has_independent_xyz(self):
  rows=ordinary_cases();self.assertEqual(len(rows),60)
  self.assertEqual(len({tuple(r['position_um']) for r in rows}),60)
  all_poses=[tuple(p) for r in rows for p in r['poses']]
  self.assertEqual(len(set(all_poses)),180)
  for die in range(3):
   for axis,limit in [(0,8),(1,8),(2,.3)]:
    values=[r['poses'][die][axis] for r in rows]
    self.assertEqual(len(set(values)),60)
    self.assertTrue(all(0<abs(v)<limit for v in values))
    self.assertTrue(min(values)<0<max(values))

if __name__=='__main__':unittest.main()
