"""Native ABI simulation, independent convolution/reference checks and no-driver fallback."""
import concurrent.futures,ctypes,hashlib,os,shutil,subprocess,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
from scipy.signal import fftconvolve
from die_shift_studio import acceleration as acc
from die_shift_studio.cuda_driver import API,Runtime
from die_shift_studio.probability_overlay import _add_footprint,_deposit

class SparseAndCpuTests(unittest.TestCase):
    def test_linear_footprint_matches_dense_fft_including_even_edges(self):
        rng=np.random.default_rng(32)
        for h,w in ((5,7),(4,2),(21,19)):
            footprint=rng.random((h,w));center=np.array([2.75,7.125]);origin=np.array([-1.,-2.])
            grid=np.zeros((11,15))
            _add_footprint(grid,footprint,center,origin,1.,.37)
            expected=fftconvolve(_deposit(grid.shape,center[None,:],np.ones(1),origin,1.)*.37,footprint,'same')
            np.testing.assert_allclose(grid,expected,atol=1e-14)

    def test_cpu_shortcuts_preserve_fixed_points_and_true_pivots(self):
        rng=np.random.default_rng(32);p=rng.random((2048,2))*200.;c=np.array([[4.,60.],[20.,16.]])
        for t in (0.,.03):
            z=np.array([[35.,-12.,t],[-35.,0.,-t]])
            for owner in (np.zeros(len(p),int),np.full(len(p),-1),rng.integers(-1,2,len(p))):
                np.testing.assert_allclose(acc.rigid_points(p,owner,c,z,'cpu'),acc._rigid_kernel(np,p,owner,c,z),atol=1e-12)
            np.testing.assert_allclose(acc.rigid_pose_batch(p,c[0],z,'cpu'),acc._pose_kernel(np,p,c[0],z),atol=1e-12)

    def test_ptx_matches_reviewable_source(self):
        root=Path(acc.__file__).parent;source=(root/'cuda_kernels.cu').read_bytes().replace(b'\r\n',b'\n')
        ptx=(root/'cuda_kernels.ptx').read_text()
        self.assertIn(hashlib.sha256(source).hexdigest(),ptx.splitlines()[0])
        self.assertIn('.version 8.0',ptx)
        for name in ('rigid','poses','blend','fft_load','fft_step','fft_product','fft_crop'):
            self.assertIn('.entry '+name+'(',ptx)

    def test_frozen_no_driver_does_not_attempt_cupy_or_block_cpu(self):
        acc._probe.cache_clear()
        try:
            with patch.object(sys,'frozen',True,create=True),patch('die_shift_studio.cuda_driver.API',side_effect=OSError('no NVIDIA driver')),patch('importlib.util.find_spec',side_effect=AssertionError):
                info=acc.capabilities()
                self.assertEqual(info['backend'],'cpu');self.assertIn('no NVIDIA driver',info['reason'])
                np.testing.assert_allclose(acc.convolve(np.ones((3,4)),np.ones((3,3)),backend='cuda'),fftconvolve(np.ones((3,4)),np.ones((3,3)),'same'))
        finally:acc._probe.cache_clear()

@unittest.skipUnless((sys.platform.startswith('linux') and shutil.which('g++')) or (os.name=='nt' and shutil.which('cl')),'Test driver requires g++ on Linux or MSVC on Windows')
class DriverABITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory();root=Path(cls.tmp.name);src=str(Path(__file__).with_name('cuda_test_driver.cpp').resolve())
        if os.name=='nt':
            dll=root/'test-driver.dll'
            subprocess.run(['cl','/nologo','/EHsc','/O2','/LD',src,'/link','/OUT:'+str(dll)],cwd=root,check=True)
            cls.api=API(ctypes.WinDLL(str(dll)))
        else:
            dll=root/'test-driver.so'
            subprocess.run(['g++','-std=c++11','-O2','-shared','-fPIC',src,'-o',str(dll)],check=True)
            cls.api=API(ctypes.CDLL(str(dll)))

    @classmethod
    def tearDownClass(cls):
        if os.name=='nt':
            import _ctypes
            _ctypes.FreeLibrary(cls.api.lib._handle)
        cls.tmp.cleanup()

    def test_skip_broken_device_and_compare_native_calls(self):
        old=acc._runtime;acc._probe.cache_clear()
        try:
            with patch('die_shift_studio.cuda_driver.API',return_value=self.api):
                cap=acc.capabilities();rt=acc._runtime
            self.assertEqual(cap['provider'],'NVIDIA Driver');self.assertEqual(cap['selected']['index'],1)
            self.assertEqual(set(cap['operations']),{'rigid','poses','blend','convolution'})
            rng=np.random.default_rng(32)
            for shape,other in [((1,1),(1,1)),((1,23),(1,4)),((11,1),(4,1)),((31,15),(4,8)),((3,5),(16,17))]:
                a=rng.normal(size=shape);b=rng.normal(size=other)
                for mode in ('full','same','valid'):
                    np.testing.assert_allclose(acc.convolve(a,b,mode,'cuda'),fftconvolve(a,b,mode),atol=1e-12)
            v=rng.normal(size=(65536,2));owners=rng.integers(-1,2,len(v));c=np.array([[4.,6.],[-7.,9.]]);s=np.array([[35.,-35.,.034],[-12.,0.,0.]])
            np.testing.assert_allclose(acc.rigid_points(v,owners,c,s,'cuda'),acc.rigid_points(v,owners,c,s,'cpu'),rtol=1e-12,atol=1e-12)
            np.testing.assert_allclose(acc.rigid_pose_batch(v,c[0],s,'cuda'),acc.rigid_pose_batch(v,c[0],s,'cpu'),rtol=1e-12,atol=1e-12)
            with concurrent.futures.ThreadPoolExecutor(3) as pool:
                results=list(pool.map(lambda _:rt.convolution(a,b,'same'),range(6)))
            for q in results:np.testing.assert_allclose(q,fftconvolve(a,b,'same'),atol=1e-12)
            self.assertEqual(self.api.lib.testAllocations(),0)
            rt.close();self.assertEqual(self.api.lib.testReferences(),0)
        finally:acc._runtime=old;acc._probe.cache_clear()

    def test_failed_fft_does_not_disable_geometry(self):
        rt=Runtime(self.api,self.api.devices()[1])
        try:
            with patch.object(rt,'convolution',side_effect=RuntimeError('FFT failed')):
                ops,errors=acc._selftest(rt)
            self.assertEqual(set(ops),{'rigid','poses','blend'});self.assertIn('FFT failed',errors['convolution'])
            with patch.object(rt,'functions',{'rigid':ctypes.c_void_p(99)}),self.assertRaises(RuntimeError):
                rt.rigid(np.ones((3,2)),[0]*3,[[0,0]],np.zeros((1,3)))
            self.assertEqual(self.api.lib.testAllocations(),0)
        finally:rt.close()
