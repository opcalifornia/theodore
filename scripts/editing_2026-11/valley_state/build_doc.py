import datetime as dt, sys, json
from theodore.resolve.connection import connect
V="/private/tmp/claude-501/-Users-admin-theodore/412eb248-a16d-5d91-aec8-d9cc64479fe2/scratchpad/vsp"
FPS=24000/1001
h=connect(require_project=False, require_timeline=False)
if h.project is None or h.project.GetName()!="Valley State Prison 2025":
    assert h.project_manager.LoadProject("Valley State Prison 2025"), "load failed"
    h=connect(require_timeline=False)
p=h.project; mp=p.GetMediaPool(); assert p.GetName()=="Valley State Prison 2025", p.GetName()
def walk(f,acc):
    for c in f.GetClipList() or []: acc.append(c)
    for s in f.GetSubFolderList() or []: walk(s,acc)
    return acc
clips={}
for c in walk(mp.GetRootFolder(),[]):
    pr=c.GetClipProperty()
    if pr.get("Type","").lower().startswith("timeline") or not pr.get("File Path"): continue
    clips[c.GetName()]=(c,pr)
print("clips",len(clips),flush=True)
PAIRS={"C0519 ii.MP4":[("C0519.MP4",10.7357)],"C0520 ii.MP4":[("C0520.MP4",-5.448)],"C0521 ii.MP4":[("C0521.MP4",2.9014)],
       "C0522 ii.MP4":[("C0522.MP4",4.1303)],"C0523 ii.MP4":[("C0523.MP4",7.9207)],"C0525 II.MP4":[("C0525.MP4",14.1809),("C0525 I .MP4",185.66)],
       "A7Siii0491.MP4":[("C0518.MP4",-4.779)],"A7Siii0508.MP4":[("C0545.MP4",4.1283)],"A7Siii0470.MP4":[("C0487.MP4",-9.96)]}
SECOND={s for v in PAIRS.values() for s,_ in v}
SHIFT={"C0486.MP4":-(58*60),"C0544.MP4":-(57*60+58)}
rows=[]
for n,(c,pr) in clips.items():
    if n in SECOND: continue
    t=dt.datetime.strptime(pr["Date Created"],"%a %b %d %Y %H:%M:%S")+dt.timedelta(seconds=SHIFT.get(n,0))
    rows.append((t,n))
rows.sort(); order=[n for _,n in rows]
print("sequence",len(order),"secondary",len(SECOND),flush=True)
name="Valley State Prison Mini Doc"
tl=mp.CreateEmptyTimeline(name); assert tl, "timeline create failed"
p.SetCurrentTimeline(tl)
while tl.GetTrackCount("video")<2: tl.AddTrack("video")
while tl.GetTrackCount("audio")<2: tl.AddTrack("audio","stereo")
tl.SetTrackName("video",1,"Day in order"); tl.SetTrackName("video",2,"Other angles")
tl.SetTrackName("audio",1,"Day audio"); tl.SetTrackName("audio",2,"Other angles audio")
for i in range(0,len(order),30):
    got=mp.AppendToTimeline([clips[n][0] for n in order[i:i+30]]); print("appended",i,len(got or []),flush=True)
T0=int(tl.GetStartFrame()); pos={}
for it in tl.GetItemListInTrack("video",1) or []: pos[it.GetName()]=(int(it.GetStart()),int(it.GetEnd()))
print("V1 items",len(pos),"missing",[n for n in order if n not in pos][:10],flush=True)
def nframes(pr):
    f=pr.get("Frames"); 
    if f and str(f).isdigit(): return int(f)
    d=pr["Duration"].replace(";",":").split(":"); return ((int(d[0])*60+int(d[1]))*60+int(d[2]))*24+int(d[3])
for prim,secs in PAIRS.items():
    if prim not in pos: print("skip",prim); continue
    ps,pe=pos[prim]; Dp=pe-ps
    for sn,d in secs:
        mpi,pr=clips[sn]; Ls=nframes(pr); df=int(round(d*FPS))
        if df>=0: rec=ps+df; st=0; ln=min(Ls,Dp-df)
        else: rec=ps; st=-df; ln=min(Ls-st,Dp)
        if ln<=0: print("no overlap",sn); continue
        for mt,tr in ((1,2),(2,2)):
            r=mp.AppendToTimeline([{"mediaPoolItem":mpi,"startFrame":st,"endFrame":st+ln-1,"recordFrame":rec,"trackIndex":tr,"mediaType":mt}])
            print("angle",sn,"->",prim,"media",mt,"placed",len(r or []),flush=True)
tl.SetTrackEnable("audio",2,False)
h.project_manager.SaveProject()
tot=max(e for s,e in pos.values())-T0
print("DONE V1 length",round(tot/FPS/60,1),"min",{k:len(tl.GetItemListInTrack(t,i) or []) for k,(t,i) in {"V1":("video",1),"V2":("video",2),"A1":("audio",1),"A2":("audio",2)}.items()},flush=True)
