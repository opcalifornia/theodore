import json, glob
from theodore.resolve.connection import connect
from theodore.resolve.timeline_ops import _placed_item
M="/private/tmp/claude-501/-Users-admin-theodore/412eb248-a16d-5d91-aec8-d9cc64479fe2/scratchpad/mil"
pieces=json.load(open(M+"/mil_plan.json")); bp=json.load(open(M+"/bleep_plan.json")); B=bp["bleep"]; MU=bp["mute"]
h=connect(require_timeline=False); p=h.project; mp=p.GetMediaPool(); assert p.GetName()=="Military Meeting GoFundMe"
root=mp.GetRootFolder(); clip=[c for c in root.GetClipList() if c.GetName()=="military.mp4"][0]
sub=[f for f in root.GetSubFolderList() if f.GetName()=="Bleeps"]; fold=sub[0] if sub else mp.AddSubFolder(root,"Bleeps")
if not fold.GetClipList(): mp.SetCurrentFolder(fold); mp.ImportMedia(sorted(glob.glob(M+"/bleeps/bleep_*.wav")))
mp.SetCurrentFolder(root)
tones={c.GetName():c for c in fold.GetClipList()}
nm="Military Meeting - 24 min cut - BLEEPED"; tl=None
for i in range(1,p.GetTimelineCount()+1):
    if p.GetTimelineByIndex(i).GetName()==nm: tl=p.GetTimelineByIndex(i)
if tl is None: tl=mp.CreateEmptyTimeline(nm)
p.SetCurrentTimeline(tl)
while tl.GetTrackCount("audio")<2: tl.AddTrack("audio","stereo")
for k in ("video","audio"):
    for i in range(1,tl.GetTrackCount(k)+1):
        its=tl.GetItemListInTrack(k,i) or []
        if its: tl.DeleteClips(its,False)
tl.SetTrackName("video",1,"Meeting"); tl.SetTrackName("audio",1,"Meeting audio"); tl.SetTrackName("audio",2,"Bleeps")
TF=30.0; CF=46.0; base=int(tl.GetStartFrame())
def place(mpi,sf,L,rec,tr,mt,cf):
    for extra in (0,1,-1,2,-2):
        ef=sf+int(round(L/TF*cf))-1+extra
        got=_placed_item(mp.AppendToTimeline([{"mediaPoolItem":mpi,"startFrame":sf,"endFrame":ef,"recordFrame":rec,"trackIndex":tr,"mediaType":mt}]))
        if got is None: continue
        if int(got.GetStart())==rec and int(got.GetEnd())==rec+L: return got
        tl.DeleteClips([got],False)
    return None
cuts=[(a,b,"bleep",n) for n,a,b in B]+[(a,b,"mute",n) for n,a,b in MU]
rec0=base; bad=0; muted=[]; tone_at=[]
for s,e in pieces:
    Lp=int(round((e-s)*TF)); pts={0,Lp}
    for a,b,k,n in cuts:
        if b>s and a<e:
            pts.add(max(0,int(round((max(a,s)-s)*TF)))); pts.add(min(Lp,int(round((min(b,e)-s)*TF))))
    pts=sorted(pts)
    for x0,x1 in zip(pts,pts[1:]):
        if x1<=x0: continue
        mid=s+(x0+x1)/2/TF; kind="normal"; name=None
        for a,b,k,n in cuts:
            if a<=mid<=b: kind=k; name=n
        rec=rec0+x0; L=x1-x0; sf=int(round((s+x0/TF)*CF))
        v=place(clip,sf,L,rec,1,1,CF); au=place(clip,sf,L,rec,1,2,CF)
        if v is None or au is None: bad+=1; print("  could not place exactly",round(s+x0/TF,2),L); continue
        if kind in ("bleep","mute"): muted.append((au,kind,name,rec,L))
        if kind=="bleep": tone_at.append((name,rec,L))
    rec0+=Lp
print("sub-pieces placed; failures:",bad)
for au,kind,name,rec,L in muted: au.SetClipEnabled(False)
tn=sorted(tones)
for (name,rec,L),tname in zip(tone_at,tn):
    t=place(tones[tname],0,L,rec,2,2,TF)
    print("bleep on A2:",name,"at %.2fs"%((rec-base)/TF),"len %.2fs"%(L/TF),"placed" if t else "FAILED")
gaps=0
for k in ("video","audio"):
    for i in (1,):
        its=tl.GetItemListInTrack(k,i) or []; gaps+=sum(1 for a,c in zip(its,its[1:]) if int(c.GetStart())!=int(a.GetEnd()))
print("V1/A1 gaps or overlaps:",gaps,"| items V1",len(tl.GetItemListInTrack("video",1)),"A1",len(tl.GetItemListInTrack("audio",1)),"A2",len(tl.GetItemListInTrack("audio",2)),"| audio switched off:",len(muted),"| length %.2f s = %d:%02d"%((rec0-base)/TF,int((rec0-base)/TF//60),int((rec0-base)/TF%60)))
a1=tl.GetItemListInTrack("audio",1)
print("read-back check, enabled flags of A1 pieces that must be off:",[(round((x.GetStart()-base)/TF,1),x.GetClipEnabled()) for x in a1 if not x.GetClipEnabled()])
h.project_manager.SaveProject()
