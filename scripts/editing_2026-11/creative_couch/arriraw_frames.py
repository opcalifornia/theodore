import numpy as np
W,H=3424,2202; STRIDE=11313152; FIRST=451072+24; LEN=11309544
def frame(f, i, swap=True, hdr=0):
    f.seek(FIRST+i*STRIDE); b=np.frombuffer(f.read(LEN),np.uint8)
    if swap: b=b.reshape(-1,4)[:,::-1].reshape(-1)
    b=b[hdr:hdr+W*H*3//2]; t=b.reshape(-1,3).astype(np.uint16)
    p0=(t[:,0]<<4)|(t[:,1]>>4); p1=((t[:,1]&15)<<8)|t[:,2]
    img=np.empty(W*H,np.uint16); img[0::2]=p0; img[1::2]=p1
    return img.reshape(H,W)
def save(im, path):
    g=im[0::2,0::2][::4,::4].astype(np.float32)
    g=np.clip((g-np.percentile(g,1))/(np.percentile(g,99)-np.percentile(g,1)+1e-9),0,1)**0.5
    a=(g*255).astype(np.uint8); open(path,"wb").write(b"P5 %d %d 255\n"%(a.shape[1],a.shape[0])+a.tobytes())
if __name__=="__main__":
    import sys
    f=open(sys.argv[1],"rb")
    for sw in (True,False):
        for hdr in (0,72):
            save(frame(f,int(sys.argv[2]),sw,hdr), f"{sys.argv[3]}_{int(sw)}_{hdr}.pgm")
