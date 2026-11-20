import numpy as np, wave, itertools, json
SR=2000
def load(n):
    w=wave.open(f"a16/{n}.wav"); x=np.frombuffer(w.readframes(w.getnframes()),np.int16).astype(np.float32)
    x=x[:len(x)//8*8].reshape(-1,8).mean(1)  # 16k->2k
    x-=x.mean(); return x/(x.std()+1e-9)
names=[l.strip() for l in open("ints.txt")]
A={n:load(n) for n in names}
res=[]
for a,b in itertools.combinations(names,2):
    x,y=A[a],A[b]
    if min(len(x),len(y))<SR*5: continue
    n=1<<int(np.ceil(np.log2(len(x)+len(y))))
    c=np.fft.irfft(np.fft.rfft(x,n)*np.conj(np.fft.rfft(y,n)),n)
    c=np.concatenate([c[-(len(y)-1):],c[:len(x)]])  # lags -(len(y)-1)..len(x)-1
    lags=np.arange(-(len(y)-1),len(x))
    ov=np.minimum(len(x),lags+len(y))-np.maximum(0,lags); ok=ov>SR*5
    sc=np.where(ok,c/np.maximum(ov,1),0)
    i=int(np.argmax(sc)); best=sc[i]
    med=np.median(np.abs(sc[ok]))
    if best>0.15 and best>8*med:
        res.append((a,b,lags[i]/SR,float(best),float(ov[i]/SR)))
        print(f"{a:16s} {b:16s} b starts at a+{lags[i]/SR:8.2f}s score {best:.2f} overlap {ov[i]/SR:.0f}s")
json.dump(res,open("sync.json","w"))
