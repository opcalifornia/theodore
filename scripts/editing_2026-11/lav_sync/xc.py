import numpy as np, subprocess, sys
def load(p):
    b = subprocess.run(["ffmpeg","-v","error","-i",p,"-f","f32le","-ac","1","-"],capture_output=True).stdout
    return np.frombuffer(b, np.float32).astype(np.float64)
SR=8000
djis={k:load(f"DJI_{k}.wav") for k in ("16_20261103_222255","17_20261103_224128")}
wins={"c005a":400,"c006a":1700,"c006b":24400,"c007a":950,"c001a":0,"c003a":0}
for w,f0 in wins.items():
    r=load(w+".wav"); r=r-r.mean()
    for k,d in djis.items():
        n=1<<int(np.ceil(np.log2(len(d)+len(r))))
        c=np.fft.irfft(np.fft.rfft(d,n)*np.conj(np.fft.rfft(r,n)),n)[:len(d)-len(r)]
        # normalise by local energy of d
        cs=np.concatenate([[0],np.cumsum(d*d)]); e=np.sqrt(cs[len(r):len(d)+1-0][:len(c)]-cs[:len(c)])+1e-9
        nc=c/(e*np.linalg.norm(r))
        i=int(np.argmax(nc)); t=i/SR
        srt=np.sort(nc)[-50:]
        print(f"{w} vs DJI_{k[:2]}: peak {nc[i]:.3f} at {t:.4f}s  offset(dji - red) {t - f0/ (24000/1001):.4f}s  2nd-ish {np.median(nc[np.abs(np.arange(len(nc))-i)>SR]):.3f}/{np.max(nc[np.abs(np.arange(len(nc))-i)>SR]):.3f}")
