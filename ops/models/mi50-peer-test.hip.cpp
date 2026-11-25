// Direct GPU kernel accesses peer memory; no hipMemcpyPeer host fallback.
#include <hip/hip_runtime.h>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>
#define CHECK(x) do { auto e=(x); if(e!=hipSuccess){std::fprintf(stderr,"%s: %s\n",#x,hipGetErrorString(e));std::exit(2);} } while(0)
constexpr size_t N=16*1024*1024;
constexpr size_t bytes=N*sizeof(uint32_t);
__global__ void copy_words(uint32_t *out,const uint32_t *in){
    for(size_t i=blockIdx.x*blockDim.x+threadIdx.x;i<N;i+=size_t(gridDim.x)*blockDim.x)out[i]=in[i];
}
int device(const char *bdf){
    int n=0; CHECK(hipGetDeviceCount(&n));
    for(int i=0;i<n;++i){
        char id[32]={}; CHECK(hipDeviceGetPCIBusId(id,sizeof(id),i));
        if(std::strcmp(id,bdf)!=0)continue;
        hipDeviceProp_t p{}; CHECK(hipGetDeviceProperties(&p,i));
        if(std::strncmp(p.gcnArchName,"gfx906",6)!=0)std::exit(2);
        return i;
    }
    std::fprintf(stderr,"HIP did not enumerate %s\n",bdf);std::exit(2);
}
void test(int src,int dst,bool read,uint32_t seed){
    std::vector<uint32_t> want(N),got(N);
    for(auto &v:want){seed^=seed<<13;seed^=seed>>17;seed^=seed<<5;v=seed;}
    uint32_t *a=nullptr,*b=nullptr;
    CHECK(hipSetDevice(src));CHECK(hipMalloc(&a,bytes));
    CHECK(hipMemcpy(a,want.data(),bytes,hipMemcpyHostToDevice));
    CHECK(hipSetDevice(dst));CHECK(hipMalloc(&b,bytes));CHECK(hipMemset(b,0,bytes));CHECK(hipDeviceSynchronize());
    CHECK(hipSetDevice(read?dst:src));
    auto launch=[&]{hipLaunchKernelGGL(copy_words,dim3(1024),dim3(256),0,0,b,a);CHECK(hipGetLastError());};
    launch();CHECK(hipDeviceSynchronize());
    hipEvent_t start,end;CHECK(hipEventCreate(&start));CHECK(hipEventCreate(&end));
    CHECK(hipEventRecord(start,0));
    for(int i=0;i<8;++i)launch();
    CHECK(hipEventRecord(end,0));CHECK(hipEventSynchronize(end));CHECK(hipDeviceSynchronize());
    float ms=0;CHECK(hipEventElapsedTime(&ms,start,end));
    CHECK(hipEventDestroy(start));CHECK(hipEventDestroy(end));
    CHECK(hipSetDevice(dst));CHECK(hipMemcpy(got.data(),b,bytes,hipMemcpyDeviceToHost));
    size_t bad=0;for(size_t i=0;i<N;++i)bad+=got[i]!=want[i];
    std::printf("%d -> %d peer-%s: %.3f GB/s; %zu incorrect words\n",src,dst,read?"read":"write",ms>0?bytes*8.0/(ms*1e6):0,bad);
    CHECK(hipFree(b));CHECK(hipSetDevice(src));CHECK(hipFree(a));
    if(bad||ms<=0)std::exit(3);
}
int main(int argc,char **argv){
    if(argc!=3)return 2;
    int a=device(argv[1]),b=device(argv[2]);if(a==b)return 2;
    int ab=0,ba=0;CHECK(hipDeviceCanAccessPeer(&ab,a,b));CHECK(hipDeviceCanAccessPeer(&ba,b,a));
    std::printf("Peer access: %d / %d\n",ab,ba);if(!ab||!ba)return 4;
    CHECK(hipSetDevice(a));CHECK(hipDeviceEnablePeerAccess(b,0));
    CHECK(hipSetDevice(b));CHECK(hipDeviceEnablePeerAccess(a,0));
    test(a,b,true,0x12345678u);test(a,b,false,0xabcde123u);
    test(b,a,true,0x98765432u);test(b,a,false,0x123abcdeu);
    std::puts("PASS: direct peer read/write, both directions, data checked.");
}
