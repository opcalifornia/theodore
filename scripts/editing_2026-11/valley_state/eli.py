import json, re, wave, numpy as np
from theodore.resolve.connection import connect
from theodore.resolve.timeline_ops import duplicate_for_edit
V="/private/tmp/claude-501/-Users-admin-theodore/412eb248-a16d-5d91-aec8-d9cc64479fe2/scratchpad/vsp"
FPS=24000/1001
def norm(s): return re.sub(r"[^a-z0-9']","",s.lower())
r=json.load(open(V+"/tx/A7Siii0470.json"))
W=[(norm(w["word"]),w["start"],w["end"]) for s in r["segments"] for w in s.get("words",[]) if norm(w["word"])]
def find(seq,after=0):
    for i in range(after,len(W)-len(seq)+1):
        if [W[i+k][0] for k in range(len(seq))]==seq: return i
    raise SystemExit("anchor not found: %s"%seq)
spec=[(["my","name","is","elijah","johnson"],["elijah","johnson"]),
      (["i","am","actually","30","this","year"],["this","year"]),
      (["currently","we","are","at","valley","state"],["merced","and","madera"]),
      (["been","incarcerated","for","eight"],["almost","nine"]),
      (["i","came","to","prison","because"],["three","precious","people"])]
pieces=[]; cur=0
for a,b in spec:
    i=find(a,cur); j=find(b,i)+len(b)-1; cur=j+1
    nxt=W[j+1][1] if j+1<len(W) else W[j][2]+1
    s=max(W[i][1]-0.15,(W[i-1][2]+0.02) if i>0 else 0); e=min(W[j][2]+0.30,nxt-0.04)
    pieces.append((s,e," ".join(w[0] for w in W[i:j+1]))); print("piece %.2f-%.2f (%.1fs): %s"%(s,e,e-s,pieces[-1][2]))
# refine the camera offset with phase correlation around the word-based estimate
def load(n):
    w=wave.open(f"{V}/a16/{n}.wav"); x=np.frombuffer(w.readframes(w.getnframes()),np.int16).astype(np.float64); return x-x.mean()
A,C=load("A7Siii0470"),load("C0487"); sr=16000
seg=A[int(8*sr):int(60*sr)]; est=9.98; best=None
for d in np.arange(est-0.5,est+0.5,1/sr*4):
    o=int((8+d)*sr); c=C[o:o+len(seg)]
    if len(c)<len(seg): continue
    v=float(np.dot(seg,c)/(np.linalg.norm(seg)*np.linalg.norm(c)+1e-9))
    if best is None or v>best[0]: best=(v,d)
print("camera offset: refined %.3f s (corr %.2f), words say %.2f"%(best[1],best[0],est))
D=best[1] if best[0]>0.08 and abs(best[1]-est)<0.35 else est
h=connect(); p=h.project
tls={p.GetTimelineByIndex(i).GetName():p.GetTimelineByIndex(i) for i in range(1,p.GetTimelineCount()+1)}
p.SetCurrentTimeline(tls["Valley State Prison Mini Doc"]); h=connect()
tl=duplicate_for_edit(h,"Valley State Prison Mini Doc - Eli Test","eli")
allit=[it for k in ("video","audio") for i in range(1,tl.GetTrackCount(k)+1) for it in (tl.GetItemListInTrack(k,i) or [])]
print("clearing",len(allit),"items", tl.DeleteClips(allit,False))
mp=p.GetMediaPool()
def walk(f,acc):
    for c in f.GetClipList() or []: acc[c.GetName()]=c
    for s in f.GetSubFolderList() or []: walk(s,acc)
    return acc
pool=walk(mp.GetRootFolder(),{}); a=pool["A7Siii0470.MP4"]; c=pool["C0487.MP4"]
base=int(tl.GetStartFrame()); rec=base
for n,(s,e,txt) in enumerate(pieces):
    L=int(round((e-s)*FPS)); sa=int(round(s*FPS)); sc=int(round((s+D)*FPS))
    mp.AppendToTimeline([{"mediaPoolItem":a,"startFrame":sa,"endFrame":sa+L-1,"recordFrame":rec,"trackIndex":1,"mediaType":2}])
    if n%2==0: v={"mediaPoolItem":a,"startFrame":sa,"endFrame":sa+L-1,"recordFrame":rec,"trackIndex":1,"mediaType":1}
    else:      v={"mediaPoolItem":c,"startFrame":sc,"endFrame":sc+L-1,"recordFrame":rec,"trackIndex":2,"mediaType":1}
    mp.AppendToTimeline([v]); print("placed piece",n+1,"at +%.1fs len %.1fs on V%d"%((rec-base)/FPS,L/FPS,1 if n%2==0 else 2),flush=True); rec+=L
tl.SetTrackName("video",1,"Elijah (A7S3)"); tl.SetTrackName("video",2,"Elijah (C-cam cover)")
h.project_manager.SaveProject()
print("DONE",tl.GetName(),{f"{k}{i}":len(tl.GetItemListInTrack(k,i) or []) for k in("video","audio") for i in range(1,tl.GetTrackCount(k)+1)}, "length %.1fs"%((rec-base)/FPS))
