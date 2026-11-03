// Host execution of the same CUDA C kernels for ABI, arithmetic and cleanup tests.
// This library lives only in the test workspace; it does not emulate GPU performance.
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <atomic>
#include <vector>
#define __device__
#define __global__
struct Dim { unsigned x; };
thread_local Dim blockIdx, blockDim, threadIdx;
#include "../die_shift_studio/cuda_kernels.cu"
static std::atomic<int> allocations{0},refs{0};
static thread_local std::vector<void*> contexts;
#define FN extern "C" int
FN testAllocations() {return allocations;}
FN testReferences() {return refs;}
FN cuInit(unsigned) {return 0;}
FN cuDriverGetVersion(int *v) {*v=12000;return 0;}
FN cuDeviceGetCount(int *v) {*v=2;return 0;}
FN cuDeviceGet(int *v,int i) {*v=i;return 0;}
FN cuDeviceGetName(char *s,int n,int i) {std::strncpy(s,i?"test device 1":"incompatible device 0",n);return 0;}
FN cuDeviceTotalMem_v2(size_t *v,int i) {*v=(i?24ull:32ull)<<30;return 0;}
FN cuDeviceGetAttribute(int *v,int attr,int) {*v=attr==75?8:6;return 0;}
FN cuDevicePrimaryCtxRetain(void **v,int i) {*v=(void*)(intptr_t)(i+1);++refs;return 0;}
FN cuDevicePrimaryCtxRelease_v2(int) {--refs;return 0;}
FN cuCtxPushCurrent_v2(void *v) {contexts.push_back(v);return 0;}
FN cuCtxPopCurrent_v2(void **v) {if(contexts.empty())return 201;*v=contexts.back();contexts.pop_back();return 0;}
FN cuModuleLoadData(void **m,const void *p) {
    if(contexts.empty()||contexts.back()!=(void*)2 || !std::strstr((const char*)p,".entry fft_crop("))return 209;
    *m=(void*)20;return 0;
}
FN cuModuleUnload(void*) {return 0;}
static const char *names[]={"rigid","poses","blend","fft_load","fft_step","fft_product","fft_crop"};
FN cuModuleGetFunction(void **f,void*,const char *n) {
    for(int i=0;i<7;i++)if(!std::strcmp(n,names[i])){*f=(void*)(intptr_t)(i+1);return 0;} return 500;
}
FN cuMemGetInfo_v2(size_t *f,size_t *t) {*f=*t=4ull<<30;return 0;}
FN cuMemAlloc_v2(uint64_t *p,size_t n) {
    if(contexts.empty())return 201;
    *p=(uint64_t)(uintptr_t)std::malloc(n);if(!*p)return 2;++allocations;return 0;
}
FN cuMemFree_v2(uint64_t p) {std::free((void*)(uintptr_t)p);--allocations;return 0;}
FN cuMemcpyHtoD_v2(uint64_t p,const void *v,size_t n) {std::memcpy((void*)(uintptr_t)p,v,n);return 0;}
FN cuMemcpyDtoH_v2(void *v,uint64_t p,size_t n) {std::memcpy(v,(void*)(uintptr_t)p,n);return 0;}
FN cuGetErrorName(int e,const char **v) {*v=e==209?"CUDA_ERROR_NO_BINARY_FOR_GPU":"CUDA_TEST_ERROR";return 0;}
#define ARG(i,T) (*static_cast<T*>(args[i]))
FN cuLaunchKernel(void *fn,unsigned gx,unsigned,unsigned,unsigned bx,unsigned,unsigned,unsigned,
                  void*,void **args,void**) {
    if(contexts.empty())return 201;
    for(unsigned i=0;i<gx*bx;i++){
        blockIdx.x=i/bx;blockDim.x=bx;threadIdx.x=i%bx;
        switch((intptr_t)fn){
        case 1: rigid(ARG(0,int),ARG(1,double*),ARG(2,int*),ARG(3,double*),ARG(4,double*));break;
        case 2: poses(ARG(0,int),ARG(1,int),ARG(2,double*),ARG(3,double*),ARG(4,double*));break;
        case 3: blend(ARG(0,int),ARG(1,double*),ARG(2,double*),ARG(3,double*),ARG(4,int),ARG(5,double),ARG(6,double),ARG(7,double*));break;
        case 4: fft_load(ARG(0,int),ARG(1,int),ARG(2,int),ARG(3,int),ARG(4,double*),ARG(5,Complex*));break;
        case 5: fft_step(ARG(0,int),ARG(1,int),ARG(2,int),ARG(3,int),ARG(4,int),ARG(5,Complex*),ARG(6,Complex*));break;
        case 6: fft_product(ARG(0,int),ARG(1,int),ARG(2,Complex*),ARG(3,Complex*),ARG(4,Complex*));break;
        case 7: fft_crop(ARG(0,int),ARG(1,int),ARG(2,int),ARG(3,int),ARG(4,int),ARG(5,int),ARG(6,Complex*),ARG(7,double*));break;
        default:return 500;
        }
    }return 0;
}
