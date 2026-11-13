import json
from theodore.resolve.connection import connect
from theodore.resolve.timeline_ops import duplicate_for_edit, _placed_item
V="/private/tmp/claude-501/-Users-admin-theodore/412eb248-a16d-5d91-aec8-d9cc64479fe2/scratchpad/vsp"
FPS=24000/1001
h=connect(); p=h.project
tls={p.GetTimelineByIndex(i).GetName():p.GetTimelineByIndex(i) for i in range(1,p.GetTimelineCount()+1)}
src=tls["Valley State Prison Mini Doc - Eli Test"]
first=[(int(it.GetLeftOffset()),int(it.GetDuration())) for it in src.GetItemListInTrack("audio",1)]
print("first interview pieces from the editor's own cut:",len(first))
long_=json.load(open(V+"/eli_full7_plan.json"))["flat"]
mp=p.GetMediaPool()
def wk(f,acc):
    for c in f.GetClipList() or []: acc[c.GetName()]=c
    for s in f.GetSubFolderList() or []: wk(s,acc)
    return acc
pool=wk(mp.GetRootFolder(),{})
A1_,C1_,A2_,C2_=pool["A7Siii0470.MP4"],pool["C0487.MP4"],pool["A7Siii0508.MP4"],pool["C0545.MP4"]
items=[]
off1=int(round(9.97*FPS))
for k,(sa,L) in enumerate(first): items.append(dict(A=A1_,C=C1_,sa=sa,sc=sa+off1,L=L,cam="A" if k%2==0 else "C"))
for s,e,c in long_:
    L=int(round((e-s)*FPS)); sa=int(round(s*FPS)); items.append(dict(A=A2_,C=C2_,sa=sa,sc=int(round((s-4.12)*FPS)),L=L,cam=c))
p.SetCurrentTimeline(tls["Valley State Prison Mini Doc"]); h=connect()
tl=duplicate_for_edit(h,"Valley State Prison Mini Doc - Eli Full v7","eli full v7")
tl.DeleteClips([it for k in ("video","audio") for i in range(1,tl.GetTrackCount(k)+1) for it in (tl.GetItemListInTrack(k,i) or [])],False)
def place(mpi,sf,L,rec,tr,mt):
    for extra in (1,0,2,-1):
        got=_placed_item(mp.AppendToTimeline([{"mediaPoolItem":mpi,"startFrame":sf,"endFrame":sf+L-1+extra,"recordFrame":rec,"trackIndex":tr,"mediaType":mt}]))
        if got is None: continue
        if int(got.GetStart())==rec and int(got.GetEnd())==rec+L: return got
        tl.DeleteClips([got],False)
    return None
base=int(tl.GetStartFrame()); rec=base; bad=0; cam_at={}
for n,it in enumerate(items):
    for mpi,sf,tr,mt in ((it["A"],it["sa"],1,1),(it["C"],it["sc"],2,1),(it["A"],it["sa"],1,2),(it["C"],it["sc"],2,2)):
        if place(mpi,sf,it["L"],rec,tr,mt) is None: bad+=1
    cam_at[rec]=it["cam"]; rec+=it["L"]
    if n%10==0: print("placed",n+1,"of",len(items),flush=True)
off=0
for it in tl.GetItemListInTrack("video",2) or []:
    if cam_at.get(int(it.GetStart()))=="A": it.SetClipEnabled(False); off+=1
tl.SetTrackName("video",1,"Eli - A7S3 (full)"); tl.SetTrackName("video",2,"Eli - C-cam (full)")
tl.SetTrackName("audio",1,"Eli audio A7S3"); tl.SetTrackName("audio",2,"Eli audio C-cam"); tl.SetTrackEnable("audio",2,False)
gaps=0
for k in ("video","audio"):
    for i in (1,2):
        its=tl.GetItemListInTrack(k,i) or []; gaps+=sum(1 for a,c in zip(its,its[1:]) if int(c.GetStart())!=int(a.GetEnd()))
h.project_manager.SaveProject()
print("DONE",tl.GetName(),{f"{k}{i}":len(tl.GetItemListInTrack(k,i) or []) for k in("video","audio") for i in (1,2)},"gaps/overlaps:",gaps,"failed:",bad,"C-cam off:",off,"length %.1f min"%((rec-base)/FPS/60))
