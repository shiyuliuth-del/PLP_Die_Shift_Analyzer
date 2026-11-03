// Double-precision CUDA source for the bundled text PTX. No CUDA libraries are linked.
// One table row per DIE/pose: pivot x/y, dx/dy, cos(theta), sin(theta), theta.
static __device__ void transform(double x, double y, const double *t, double &ox, double &oy) {
    if (t[6] == 0.) { ox=x+t[2]; oy=y+t[3]; return; }
    double rx=x-t[0], ry=y-t[1];
    ox=(t[4]*rx-t[5]*ry)+t[0]+t[2];
    oy=(t[5]*rx+t[4]*ry)+t[1]+t[3];
}
extern "C" __global__ void rigid(int n,const double *p,const int *owner,const double *table,double *out) {
    int i=blockIdx.x*blockDim.x+threadIdx.x;
    if (i>=n) return;
    double x=p[2*i],y=p[2*i+1];
    if (owner[i]>=0) transform(x,y,table+7*owner[i],x,y);
    out[2*i]=x; out[2*i+1]=y;
}
extern "C" __global__ void poses(int n,int vertices,const double *p,const double *table,double *out) {
    int i=blockIdx.x*blockDim.x+threadIdx.x;
    if (i>=n) return;
    int j=2*(i%vertices);
    transform(p[j],p[j+1],table+7*(i/vertices),out[2*i],out[2*i+1]);
}
extern "C" __global__ void blend(int n,const double *p,const double *weights,const double *t,
                                 int arc,double gamma,double cot,double *out) {
    int i=blockIdx.x*blockDim.x+threadIdx.x;
    if (i>=n) return;
    double x=p[2*i],y=p[2*i+1],ox,oy,w=weights[i];
    transform(x,y,t,ox,oy);
    double dx=ox-x,dy=oy-y;
    if (!arc) {out[2*i]=x+w*dx; out[2*i+1]=y+w*dy; return;}
    double c=cos(gamma*w),s=sin(gamma*w);
    double a=.5*(1.-c+cot*s),b=.5*(cot*(1.-c)-s);
    out[2*i]=(x+a*dx)-b*dy; out[2*i+1]=(y+a*dy)+b*dx;
}

// Separable radix-2 FFT. Both axes are bit reversed before the first stage.
// Each launch owns disjoint butterfly pairs; the in-order stream separates stages.
static __device__ int reverse_index(int x,int n) {
    int r=0;
    for (;n>1;n>>=1,x>>=1) r=(r<<1)|(x&1);
    return r;
}
struct Complex {double r,i;};
extern "C" __global__ void fft_load(int nx,int ny,int w,int h,const double *src,Complex *out) {
    int i=blockIdx.x*blockDim.x+threadIdx.x;
    if (i>=nx*ny) return;
    int x=reverse_index(i%nx,nx),y=reverse_index(i/nx,ny);
    out[i].r=(x<w && y<h)?src[y*w+x]:0.; out[i].i=0.;
}
extern "C" __global__ void fft_step(int nx,int ny,int span,int vertical,int inverse,
                                     const Complex *twiddles,Complex *data) {
    int i=blockIdx.x*blockDim.x+threadIdx.x;
    if (i>=nx*ny/2) return;
    int half=span/2, axis=vertical?ny:nx, k=i%(axis/2),j=k%half;
    int v=(k/half)*span+j, step=vertical?nx:1;
    int first=vertical?v*nx+i/(ny/2):(i/(nx/2))*nx+v;
    Complex a=data[first],b=data[first+half*step],w=twiddles[j*((nx>ny?nx:ny)/span)];
    double wi=inverse?-w.i:w.i;
    double r=b.r*w.r-b.i*wi,s=b.r*wi+b.i*w.r;
    data[first].r=a.r+r; data[first].i=a.i+s;
    data[first+half*step].r=a.r-r; data[first+half*step].i=a.i-s;
}
extern "C" __global__ void fft_product(int nx,int ny,const Complex *a,const Complex *b,Complex *out) {
    int i=blockIdx.x*blockDim.x+threadIdx.x;
    if (i>=nx*ny) return;
    int j=reverse_index(i/nx,ny)*nx+reverse_index(i%nx,nx);
    out[j].r=a[i].r*b[i].r-a[i].i*b[i].i; out[j].i=a[i].i*b[i].r+a[i].r*b[i].i;
}
extern "C" __global__ void fft_crop(int nx,int ny,int w,int h,int x0,int y0,const Complex *data,double *out) {
    int i=blockIdx.x*blockDim.x+threadIdx.x;
    if (i<w*h) out[i]=data[(i/w+y0)*nx+i%w+x0].r/(double(nx)*ny);
}
