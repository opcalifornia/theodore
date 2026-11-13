import json, wave, numpy as np, re
M="/private/tmp/claude-501/-Users-admin-theodore/412eb248-a16d-5d91-aec8-d9cc64479fe2/scratchpad/mil"
S=json.load(open(M+"/sent.json"))
KEEP=[(0,14),("15p",),(33,51),(59,69),(72,80),(82,89),(108,112),(113,121),(125,149),(153,154),(156,166),(167,172),(176,182),(183,204),(242,253),(254,265),(268,273)]
# build ranges of (first_sentence, last_sentence, optional word cut)
rng=[]
for k in KEEP:
    if k==("15p",): rng[-1]=(rng[-1][0],15,"public"); continue
    rng.append((k[0],k[1],None))
# merge touching ranges
mer=[]
for a,b,w in rng:
    if mer and a==mer[-1][1]+1 and mer[-1][2] is None: mer[-1]=(mer[-1][0],b,w)
    else: mer.append((a,b,w))
wv=wave.open(M+"/mil.wav"); sr=wv.getframerate(); x=np.frombuffer(wv.readframes(wv.getnframes()),np.int16).astype(np.float64)
hop=int(sr*0.02); n=len(x)//hop; rms=np.sqrt((x[:n*hop].reshape(n,hop)**2).mean(1))
thr=max(np.percentile(rms,6)*3.0,np.percentile(rms,50)*0.12); strong=rms>thr*3; weak=rms>thr*1.3
def dip_after(t,maxd):
    i=max(0,int(t/0.02))
    for k in range(i,min(n-3,i+int(maxd/0.02))):
        if not weak[k:k+3].any(): return k*0.02
    return None
def rise_before(t,lim):
    c=[k*0.02 for k in range(max(1,int((t-lim)/0.02)),min(n,int((t+0.1)/0.02))) if strong[k] and not strong[k-1]]
    return min(c,key=lambda v:abs(v-t)) if c else None
pieces=[]
for a,b,w in mer:
    fw=S[a][0]; s0=fw[1]; rr=rise_before(s0,0.3); start=max(0,(min(s0,rr) if rr else s0)-0.06)
    if w:
        lw=[q for q in S[b] if re.sub(r"[^a-z]","",q[0].lower())==w][0]; e0=lw[2]
    else: e0=S[b][-1][2]
    d=dip_after(e0-0.1,0.7); end=(d+0.05) if d else e0+0.2
    # never run into the next sentence's first word
    nxt=S[b+1][0][1] if b+1<len(S) else None
    if nxt and not w and end>nxt-0.05: end=nxt-0.05
    pieces.append((start,end,a,b))
tot=sum(e-s for s,e,_,_ in pieces)
print("%d pieces, total %.1f s = %d:%02d"%(len(pieces),tot,int(tot//60),int(tot%60)))
for s,e,a,b in pieces: print("  %6.1f-%6.1f (%5.1fs)  sentences %d-%d : %s"%(s,e,e-s,a,b," ".join(q[0] for q in S[a])[:60]))
assert tot<=1490, "over the 25 minute limit"
json.dump([(s,e) for s,e,_,_ in pieces],open(M+"/mil_plan.json","w"))
