import json, re, wave, difflib, statistics
import numpy as np, mlx_whisper
V="/private/tmp/claude-501/-Users-admin-theodore/412eb248-a16d-5d91-aec8-d9cc64479fe2/scratchpad/vsp"
def norm(s): return re.sub(r"[^a-z0-9']","",s.lower())
FILL=re.compile(r"^(u+h*m+|u+h+|e+r+m*|ah+|hm+|mm+|uhm+)$")
wv=wave.open(f"{V}/a16/A7Siii0508.wav"); sr=wv.getframerate(); X=np.frombuffer(wv.readframes(wv.getnframes()),np.int16)
x=X.astype(np.float64); hop=int(sr*0.02); n=len(x)//hop; rms=np.sqrt((x[:n*hop].reshape(n,hop)**2).mean(1))
thr=max(np.percentile(rms,6)*3.0,np.percentile(rms,50)*0.12); sp=rms>thr; strong=rms>thr*3; weak=rms>thr*1.3
def runs(mask,val):
    out=[];i=0
    while i<len(mask):
        if mask[i]==val:
            j=i
            while j<len(mask) and mask[j]==val: j+=1
            out.append((i*0.02,j*0.02)); i=j
        else: i+=1
    return out
sil=[(a,b) for a,b in runs(sp,False) if b-a>=0.12]
def dip_after(t,maxd):
    i=max(0,int(t/0.02))
    for k in range(i,min(n-3,i+int(maxd/0.02))):
        if not weak[k:k+3].any(): return k*0.02
    return None
def rise_near(t,lim):
    c=[k*0.02 for k in range(1,n) if strong[k] and not strong[k-1] and abs(k*0.02-t)<=lim]
    return min(c,key=lambda v:abs(v-t)) if c else None
r=json.load(open(f"{V}/tx/A7Siii0508_um.json"))
rb=json.load(open(f"{V}/tx/A7Siii0508.json"))
WB=[(w["word"].strip(),norm(w["word"]),w["start"],w["end"]) for ss in rb["segments"] for w in ss.get("words",[]) if norm(w["word"]) and not FILL.match(norm(w["word"]))]
W=[(w["word"].strip(),norm(w["word"]),w["start"],w["end"]) for s in r["segments"] for w in s.get("words",[]) if norm(w["word"]) and not FILL.match(norm(w["word"]))]
BL=[("my name's elijah johnson","death by incarceration"),
    ("me being a part of that triple murder","but not the murders"),
    ("so currently myself","and they will"),
    ("i would tell them do whatever you can","give back like that too"),
    ("everybody has potential","greatness in everything"),
    ("it's a great question because","call this place home"),
    ("don't give up because each and every day","do better each and every day")]
def find(phrase,after):
    seq=[norm(t) for t in phrase.split()]
    for i in range(after,len(W)-len(seq)+1):
        if all(W[i+k][1]==seq[k] or difflib.SequenceMatcher(None,W[i+k][1],seq[k]).ratio()>=0.8 for k in range(len(seq))): return i
    return None
PROMPT="Um, so, uh, you know, I was, um, uh, like a foster kid. Umm, yeah. Uh-huh. Hmm, well, uh, so..."
def chunks(a,b):
    out=[];s=a
    while b-s>26:
        c=[(x+y)/2 for x,y in sil if s+12<(x+y)/2<s+26]; cut=max(c) if c else s+25; out.append((s,cut)); s=cut
    out.append((s,b)); return out
def transcribe(a,b):
    words=[]
    for s,e in chunks(a,b):
        seg=X[int(s*sr):int(e*sr)]; fn=f"{V}/_chunk.wav"; w=wave.open(fn,"wb"); w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr); w.writeframes(seg.tobytes()); w.close()
        rr=mlx_whisper.transcribe(fn,path_or_hf_repo="mlx-community/whisper-large-v3-turbo",language="en",word_timestamps=True,condition_on_previous_text=False,initial_prompt=PROMPT)
        words+=[(q["word"].strip(),norm(q["word"]),q["start"]+s,q["end"]+s) for ss in rr["segments"] for q in ss.get("words",[]) if norm(q["word"])]
    return words
def merge(cuts):
    cuts=sorted(cuts); out=[]
    for a,b,k in cuts:
        if out and a<=out[-1][1]+0.05: out[-1]=(out[-1][0],max(out[-1][1],b),out[-1][2]+"+"+k)
        else: out.append((a,b,k))
    return out
pieces=[]; cur=0; log=[]; gaps=[]; prev_end_idx=None
for bi,(sp_,ep_) in enumerate(BL):
    i=find(sp_,cur)
    if i is None: print("B%d start not found"%(bi+1)); continue
    j=find(ep_,i)
    if j is None: print("B%d end not found"%(bi+1)); continue
    j+=len(ep_.split())-1; cur=j+1
    if prev_end_idx is not None: gaps.append((bi,' '.join(w[0] for w in W[prev_end_idx+1:i])))
    prev_end_idx=j
    s0=W[i][2]; rr_=rise_near(s0,0.2); start=min(s0,rr_ if rr_ else s0)-0.03
    d=dip_after(W[j][3]-0.15,0.9); end=d if d else W[j][3]+0.2
    if sp_.startswith('i never had a lot growing up'): start=71.11
    CW=transcribe(start-0.2,end+0.2); clean=[w for w in CW if not FILL.match(w[1])]
    cuts=[]
    for f in CW:
        isf=bool(FILL.match(f[1]))
        sofill=(f[1]=="so" and any(c[3]<f[2] and c[0][-1:] in ".?!" for c in clean[-0:] if c is not f) and (lambda pw: pw and pw[-1][0][-1:] in ".?!")([c for c in CW if c[3]<=f[2]+0.05 and c is not f]))
        if not (isf or sofill): continue
        if not (start+0.1<f[2]<end-0.1): continue
        prev=[c for c in clean if c[3]<=f[3] and c[2]<f[2]]; nxt=[c for c in clean if c[2]>=f[3]-0.25 and c[2]>f[2]]
        if not prev or not nxt: continue
        pe=prev[-1][3]; ns=nxt[0][2]
        for q in WB:
            if q[1]==prev[-1][1] and abs(q[2]-prev[-1][2])<0.7: pe=max(pe,q[3])
        ca=dip_after(pe-0.15,0.8) or pe+0.15; cb=ns-0.03
        if cb-ca>=0.25: cuts.append((ca,cb,"um" if isf else "so"))
    for a,b in sil:
        if not (start+0.4<a and b<end-0.4): continue
        mid=(a+b)/2; prevw=[c for c in clean if c[3]<=a+0.35]; endp=prevw and prevw[-1][0][-1:] in ".?!"
        span=[c for c in clean if c[2]<=mid<=c[3] and (c[3]-c[2])>0.9]
        if b-a>=0.9 and (span or not endp): cuts.append((a+0.30,b-0.08,"stall"))
        elif b-a>=0.55 and not endp and not span: cuts.append((a+0.30,b-0.08,"lull"))
        elif endp and b-a>1.3: cuts.append((a+0.4,b-0.4,"long pause"))
    safe=[]; U=WB+[w_ for w_ in clean]
    def dip_before(lo,hi):
        for k in range(int(hi/0.02),int(lo/0.02)-1,-1):
            if k+3<=n and not weak[k:k+3].any(): return k*0.02
        return None
    for ca,cb,k in cuts:
        inside=[q[2] for q in U if ca<q[2]<cb+0.02 and (q[3]-q[2])<=0.9]
        if inside:
            lo=min(inside)-0.06
            if cb>lo:
                rises=[k*0.02 for k in range(max(1,int(lo/0.02)),int((cb+0.02)/0.02)+1) if k<n and strong[k] and not strong[k-1]]
                cb=max(lo,min(rises)-0.05) if rises else lo
        for q in U:
            if (q[3]-q[2])<=0.9 and q[2]<ca<q[3]-0.03: ca=max(ca,q[3]+0.03)
        safe.append((ca,cb,k))
    cuts=[c for c in safe if c[1]-c[0]>=0.2]
    if start<69.86<end and end>71.11: cuts.append((69.86,71.11,'mmm'))
    cuts=merge(cuts); segs=[]; p=start
    for a,b,k in cuts: segs.append((p,a)); p=b
    segs.append((p,end)); pieces.append(segs)
    log.append((bi+1,round(start,2),round(end,2),[(round(a,2),round(b,2),k) for a,b,k in cuts]))
    print("B%d %.2f-%.2f  pieces: %s"%(bi+1,start,end,[(round(s,2),round(e,2)) for s,e in segs]),flush=True)
flat=[(s,e) for segs in pieces for s,e in segs if e-s>0.15]; out=[]
for s,e in flat:
    if e-s>24:
        bnd=[(a,b) for a,b in sil if s+7<a<e-7 and b-a>=0.35]
        if bnd:
            a,b=min(bnd,key=lambda ab:abs((ab[0]+ab[1])/2-(s+e)/2)); mid=a+0.2; out+=[(s,mid),(mid,e)]; continue
    out.append((s,e))
out=[(s,(59.27 if abs(s-57.36)<0.02 and abs(e-59.1)<0.06 else e)) for s,e in out]
flat=[(s,e,"C" if k%2==0 else "A") for k,(s,e) in enumerate(out)]
print("WHAT SITS BETWEEN ANSWERS (must be only the interviewer):")
for g in gaps: print("  between answer %d and %d: %s"%(g[0],g[0]+1,g[1][:260]))
print("TOTAL %.1f s, %d pieces"%(sum(e-s for s,e,_ in flat),len(flat)))
json.dump(dict(flat=flat,log=log),open(V+"/eli_full7_plan.json","w"))
