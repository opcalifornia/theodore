import json
from theodore.resolve.connection import connect
from theodore.resolve.timeline_ops import _placed_item
M="/private/tmp/claude-501/-Users-admin-theodore/412eb248-a16d-5d91-aec8-d9cc64479fe2/scratchpad/mil"
pieces=json.load(open(M+"/mil_plan.json"))
h=connect(require_timeline=False); p=h.project; mp=p.GetMediaPool()
assert p.GetName()=="Military Meeting GoFundMe"
clip=[c for c in mp.GetRootFolder().GetClipList() if c.GetName()=="military.mp4"][0]
pr=clip.GetClipProperty(); print({k:pr.get(k) for k in ("FPS","Frames","Duration","Resolution","Start TC")})
cf=float(pr.get("FPS") or 46.0); TF=30.0
nm="Military Meeting - 24 min cut"
tl=None
for i in range(1,p.GetTimelineCount()+1):
    if p.GetTimelineByIndex(i).GetName()==nm: tl=p.GetTimelineByIndex(i)
if tl is None: tl=mp.CreateEmptyTimeline(nm)
p.SetCurrentTimeline(tl)
def clear_from(frame):
    for k in ("video","audio"):
        for i in range(1,tl.GetTrackCount(k)+1):
            dead=[it for it in (tl.GetItemListInTrack(k,i) or []) if int(it.GetStart())>=frame]
            if dead: tl.DeleteClips(dead,False)
clear_from(0)
base=int(tl.GetStartFrame()); rec=base; bad=0; rows=[]
for s,e in pieces:
    L=int(round((e-s)*TF)); sf=int(round(s*cf)); ok=None
    for extra in (0,1,-1,2,-2):
        ef=sf+int(round(L/TF*cf))-1+extra
        got=_placed_item(mp.AppendToTimeline([{"mediaPoolItem":clip,"startFrame":sf,"endFrame":ef,"recordFrame":rec}]))
        if got is None: continue
        if int(got.GetStart())==rec and int(got.GetEnd())==rec+L: ok=got; break
        clear_from(rec)
    if ok is None: bad+=1; print("  could not place exactly",s,e); continue
    rows.append((s,e,ok.GetSourceStartTime() if hasattr(ok,"GetSourceStartTime") else None)); rec+=L
print("placed",len(rows),"of",len(pieces),"bad",bad)
gaps=0
for k in ("video","audio"):
    for i in range(1,tl.GetTrackCount(k)+1):
        its=tl.GetItemListInTrack(k,i) or []; gaps+=sum(1 for a,c in zip(its,its[1:]) if int(c.GetStart())!=int(a.GetEnd()))
        print(k,i,len(its),"items")
print("gaps/overlaps:",gaps,"| timeline length %.2f s = %d:%02d"%((rec-base)/TF,int((rec-base)/TF//60),int((rec-base)/TF%60)))
vi=tl.GetItemListInTrack("video",1)
for it,(s,e,_) in list(zip(vi,rows))[:12]:
    print("  piece source %.1f-%.1f -> clip start-time read back %.2f"%(s,e,it.GetSourceStartTime()))
h.project_manager.SaveProject()
