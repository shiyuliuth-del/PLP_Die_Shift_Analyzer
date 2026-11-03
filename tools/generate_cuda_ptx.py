"""Maintainer tool only. Rebuild the text kernels with NVIDIA NVRTC (no GPU required)."""
import argparse,ctypes as C,hashlib
from pathlib import Path

def compile_ptx(lib,source):
    handle=C.c_void_p()
    def call(name,*args):
        status=getattr(lib,name)(*args)
        if status:raise RuntimeError(f'{name} failed ({status})')
    call('nvrtcCreateProgram',C.byref(handle),source,b'cuda_kernels.cu',0,None,None)
    try:
        options=(C.c_char_p*3)(b'--gpu-architecture=compute_52',b'--fmad=false',b'--std=c++11')
        status=lib.nvrtcCompileProgram(handle,len(options),options)
        if status:
            n=C.c_size_t();call('nvrtcGetProgramLogSize',handle,C.byref(n))
            log=C.create_string_buffer(n.value);call('nvrtcGetProgramLog',handle,log)
            raise RuntimeError(log.value.decode())
        n=C.c_size_t();call('nvrtcGetPTXSize',handle,C.byref(n));text=C.create_string_buffer(n.value)
        call('nvrtcGetPTX',handle,text)
        ptx=b'\n'.join(line.rstrip() for line in text.value.splitlines()).rstrip()+b'\n'
        return b'// cuda_kernels.cu SHA256 '+hashlib.sha256(source).hexdigest().encode()+b'\n'+ptx
    finally:call('nvrtcDestroyProgram',C.byref(handle))

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('nvrtc',help='Path to the NVRTC compiler library')
    args=parser.parse_args();root=Path(__file__).resolve().parents[1]/'die_shift_studio'
    source=(root/'cuda_kernels.cu').read_bytes().replace(b'\r\n',b'\n')
    (root/'cuda_kernels.ptx').write_bytes(compile_ptx(C.CDLL(args.nvrtc),source))
