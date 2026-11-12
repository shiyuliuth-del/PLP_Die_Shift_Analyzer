"""Geometry contracts: interior terminals, convex smooth pads, CPU/GPU parity."""
import math,json,unittest
from pathlib import Path
from dataclasses import replace
from unittest.mock import patch
import numpy as np
import shapely
from shapely.geometry import Point,box,LineString
from die_shift_studio.settings import ViewSettings
from die_shift_studio.preview import rigid_transform
from die_shift_studio.drc import rule_region

class V18Tests(unittest.TestCase):



    def test_gpu_formula_and_missing_runtime_fallback_match_rigid_endpoints(self):
        from die_shift_studio.acceleration import blend_points
        rng=np.random.default_rng(18);xy=rng.normal(size=(500,2))*100;center=np.array([37.,-45]);z=np.array([-35.,-35.,-.017])
        for method in ('bezier','arc'):
            for w in (np.zeros(500),np.ones(500),rng.random(500)):
                with patch('die_shift_studio.acceleration.capabilities',return_value={'backend':'cpu'}):
                    fallback=blend_points(xy,w,center,z,method,30,'cuda')
                expected=blend_points(xy,w,center,z,method,30,'cpu')
                np.testing.assert_allclose(fallback,expected,atol=1e-10)
                if np.all(w==0):np.testing.assert_allclose(expected,xy,atol=1e-10)
                if np.all(w==1):np.testing.assert_allclose(expected,rigid_transform(xy,center,z),atol=1e-10)

    def test_gpu_dispatch_and_invalid_device_output_fail_back_to_cpu(self):
        from types import SimpleNamespace
        from contextlib import nullcontext
        from die_shift_studio.acceleration import blend_points
        class Device:
            cuda=SimpleNamespace(Device=lambda n:nullcontext(),runtime=SimpleNamespace(memGetInfo=lambda:(10**9,10**9)))
            asnumpy=staticmethod(np.asarray)
            def __getattr__(self,key):return getattr(np,key)
        device=Device();xy=np.array([[3.,7.],[100,200]]);w=np.array([.3,.8]);center=np.array([10.,-5]);z=np.array([-35.,-35.,-.017])
        from die_shift_studio.acceleration import _CuPy
        with patch('die_shift_studio.acceleration._runtime',_CuPy(device,0)),patch('die_shift_studio.acceleration.capabilities',return_value={'backend':'cuda'}):
            for method in ('bezier','arc'):
                expected=blend_points(xy,w,center,z,method,30,'cpu')
                np.testing.assert_allclose(blend_points(xy,w,center,z,method,30,'cuda'),expected)
            device.asnumpy=lambda v:np.asarray(v)+1
            with self.assertLogs('die_shift_studio.acceleration',level='ERROR'):
                got=blend_points(xy,w,center,z,'arc',30,'cuda')
            np.testing.assert_allclose(got,expected)


if __name__=='__main__':unittest.main()
