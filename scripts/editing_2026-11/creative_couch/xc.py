import numpy as np, subprocess
def load(p):
    b=subprocess.run(["ffmpeg","-v","error","-i",p,"-f","f32le","-ac","1","-"],capture_output=True).stdout
    return np.frombuffer(b,np.float32).astype(np.float64)
SR=8000; RFPS=24000/1001
for k in ("d07","d06"):
    d=load(k+".wav")
    for w,f0 in (("red_a",0),("red_b",40000)):
        r=load(w+".wav"); r=r-r.mean()
        n=1<<int(np.ceil(np.log2(len(d)+len(r))))
        c=np.fft.irfft(np.fft.rfft(d,n)*np.conj(np.fft.rfft(r,n)),n)[:len(d)-len(r)]
        cs=np.concatenate([[0],np.cumsum(d*d)]); e=np.sqrt(cs[len(r):len(r)+len(c)]-cs[:len(c)])+1e-9
        nc=c/(e*np.linalg.norm(r)); i=int(np.argmax(nc))
        far=np.abs(np.arange(len(nc))-i)>SR
        print(f"{w} vs {k}: peak {nc[i]:.3f} 2nd {nc[far].max():.3f}  lav time at RED start = {i/SR - f0/RFPS:.4f}s")
