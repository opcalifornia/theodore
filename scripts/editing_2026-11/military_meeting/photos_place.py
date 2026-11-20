from theodore.resolve.connection import connect
from theodore.resolve.timeline_ops import _placed_item
M="/private/tmp/claude-501/-Users-admin-theodore/412eb248-a16d-5d91-aec8-d9cc64479fe2/scratchpad/mil"
h=connect(require_timeline=False); p=h.project; mp=p.GetMediaPool(); assert p.GetName()=="Military Meeting GoFundMe"
tl=None
for i in range(1,p.GetTimelineCount()+1):
    if p.GetTimelineByIndex(i).GetName()=="Military Meeting - 24 min cut - BLEEPED": tl=p.GetTimelineByIndex(i)
p.SetCurrentTimeline(tl); base=int(tl.GetStartFrame())
root=mp.GetRootFolder(); sub=[f for f in root.GetSubFolderList() if f.GetName()=="Photos"]; fold=sub[0] if sub else mp.AddSubFolder(root,"Photos")
mp.SetCurrentFolder(fold)
if not fold.GetClipList(): mp.ImportMedia([M+"/photos/father_zoom.mov",M+"/photos/hospital_zoom.mov"])
mp.SetCurrentFolder(root)
c={x.GetName():x for x in fold.GetClipList()}
while tl.GetTrackCount("video")<2: tl.AddTrack("video")
tl.SetTrackName("video",2,"Photos")
for it in tl.GetItemListInTrack("video",2) or []: tl.DeleteClips([it],False)
def place(mpi,L,rec):
    for extra in (0,1,-1,2):
        got=_placed_item(mp.AppendToTimeline([{"mediaPoolItem":mpi,"startFrame":0,"endFrame":L-1+extra,"recordFrame":rec,"trackIndex":2,"mediaType":1}]))
        if got is None: continue
        if int(got.GetStart())==rec and int(got.GetEnd())==rec+L: return got
        tl.DeleteClips([got],False)
for name,start_s,dur in (("father_zoom.mov",63.0,20.5),("hospital_zoom.mov",85.9,20.0)):
    L=int(round(dur*30)); rec=base+int(round(start_s*30)); g=place(c[name],L,rec)
    print(name,"on V2 at %.1f s for %.1f s:"%(start_s,dur),"placed exactly" if g else "FAILED")
h.project_manager.SaveProject()
print("V1 items",len(tl.GetItemListInTrack("video",1)),"| V2 items",len(tl.GetItemListInTrack("video",2)),"| timeline %.2f s"%((tl.GetEndFrame()-base)/30))
