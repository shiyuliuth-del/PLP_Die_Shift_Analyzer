"""Keep the upload branch's offline preflight without regressing GPU diagnostics."""
import json
from pathlib import Path
import runpy
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

ROOT=Path(__file__).resolve().parents[1]


class SourceEntryTests(unittest.TestCase):
    def run_entry(self,args,verify,launch=None):
        frozen=types.ModuleType('die_shift_studio.frozen_support')
        frozen.prepare_runtime=Mock();frozen.hide_owned_console=Mock();frozen.smoke=Mock(return_value=0)
        deps=types.ModuleType('die_shift_studio.dependencies');deps.verify=verify
        gui=types.ModuleType('die_shift_studio.gui');gui.launch=launch or Mock(return_value=0)
        gpu=types.ModuleType('die_shift_studio.acceleration')
        gpu.capabilities=Mock(return_value={'available':False});gpu.graphics_inventory=Mock(return_value=[])
        modules={m.__name__:m for m in (frozen,deps,gui,gpu)}
        with patch.dict(sys.modules,modules),patch.object(sys,'argv',[str(ROOT/'main.py'),*args]):
            with self.assertRaises(SystemExit) as result:runpy.run_path(str(ROOT/'main.py'),run_name='__main__')
        self.assertEqual(result.exception.code,0)
        return frozen,gui

    def test_source_launch_checks_dependencies_before_gui(self):
        order=[]
        verify=Mock(side_effect=lambda:order.append('verify'))
        launch=Mock(side_effect=lambda project:order.append('launch') or 0)
        self.run_entry(['--project','example.dshift.json'],verify,launch)
        self.assertEqual(order,['verify','launch'])
        launch.assert_called_once_with('example.dshift.json')

    def test_smoke_retains_preflight_and_console_output(self):
        verify=Mock()
        frozen,_=self.run_entry(['--smoke-test','out.json'],verify)
        verify.assert_called_once_with();frozen.smoke.assert_called_once_with('out.json')
        frozen.hide_owned_console.assert_not_called()

    def test_gpu_info_stays_available_without_gui_preflight(self):
        verify=Mock(side_effect=RuntimeError('GUI dependency missing'))
        with tempfile.TemporaryDirectory() as tmp:
            target=Path(tmp)/'gpu.json'
            frozen,gui=self.run_entry(['--gpu-info',str(target)],verify)
            self.assertEqual(json.loads(target.read_text()),{'available':False,'adapters':[]})
        verify.assert_not_called();gui.launch.assert_not_called();frozen.hide_owned_console.assert_not_called()
