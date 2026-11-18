"""Contract checks for the shipped production-style GD55 case."""
import hashlib
import json
from pathlib import Path
import unittest

from openpyxl import load_workbook
from RealDataAnalyse.projects import load_folder, GD55_INPUTS


class GD55Cases(unittest.TestCase):
    def test_single_sheet_36_ordered_positions_and_explicit_independent_poses(self):
        root=Path(__file__).resolve().parents[1]/'Jobs/GD55'
        config=load_folder(root)
        self.assertEqual(config['profile']['sheet'],'Measurements')
        files=[Path(row['path']) for row in config['files'] if row['enabled']]
        self.assertEqual(len(files),3)
        self.assertTrue(all(path.parent==root/'Excel' for path in files))
        self.assertEqual({p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()},set(GD55_INPUTS))
        rows=[]
        for number,path in enumerate(files,1):
            digest=hashlib.sha256(path.read_bytes()).hexdigest()
            workbook=load_workbook(path,read_only=True,data_only=False)
            try:
                self.assertEqual(workbook.sheetnames,['Measurements'])
                data=list(workbook.active.iter_rows(min_row=2,max_col=9,values_only=True))
            finally:
                workbook.close()
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),digest)
            self.assertEqual(len(data),36)
            for i,row in enumerate(data):
                self.assertEqual(row[:2],(f'GD55_{i+1:03}',number))
                self.assertEqual(row[5:7],(i%6*12000,i//6*12000))
                self.assertTrue(all(type(v) in (int,float) for v in row[2:5]))
            rows.append(data)
        poses=[row[2:5] for data in rows for row in data]
        self.assertEqual(len(set(poses)),108)
        severity=[max((r[2]**2+r[3]**2+(r[4]/.035)**2)**.5 for r in group) for group in zip(*rows)]
        self.assertTrue(all(b>a for a,b in zip(severity,severity[1:])))
        self.assertGreater(max(abs(p[0]) for p in poses),15)
        self.assertGreater(max(abs(p[1]) for p in poses),15)
        self.assertGreater(max(abs(p[2]) for p in poses),1)
        self.assertEqual(config['template'],str(root/'GD55_Template.gds'))
        project=json.loads(Path(config['project']).read_text())
        self.assertEqual(project['parameters']['minimum_area_um2'],1)
        self.assertEqual(project['settings']['drc_tolerance_um'],.5)
