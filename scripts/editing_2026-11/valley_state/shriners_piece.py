"""Shriners Empowerment Piece: Joe Wilson (Shriners), Norman Williams, Jeffrey Burke, Daniel Henson,
Elijah Johnson and Sean Diaz Garcia Jr.,
word-snapped lines from shriners_plan.json, laid end to end with zero gaps.
Main angle on V1/A1, synced second angle on V2/A2 (A2 off); the visible angle
alternates per line. Builds a 16:9 master, then a 9:16 copy with Smart Reframe."""
import json, os
from theodore.resolve.connection import connect
from theodore.resolve.timeline_ops import duplicate_for_edit, _placed_item, make_aspect_version
HERE=os.path.dirname(os.path.abspath(__file__))
FPS=24000/1001
plan=json.load(open(HERE+"/shriners_plan.json"))
# main file -> (pool name, second-angle pool name, second angle starts this many s after main)
SRC={"C0523ii":("C0523 ii.MP4","C0523.MP4",7.92),"C0525II":("C0525 II.MP4","C0525.MP4",14.18),
     "A7Siii0491":("A7Siii0491.MP4","C0518.MP4",-4.78),"C0519ii":("C0519 ii.MP4","C0519.MP4",10.73),
     "A7Siii0508":("A7Siii0508.MP4","C0545.MP4",4.12),"A7Siii0506":("A7Siii0506.MP4","A7Siii0506.MP4",0.0)}
WHO={"C0523ii":"Joe Wilson, Shriners Children's","C0525II":"Jeffrey Burke, organizer",
     "A7Siii0491":"Norman Williams, started the walkathon","C0519ii":"Daniel Henson",
     "A7Siii0508":"Elijah Johnson","A7Siii0506":"Sean Diaz Garcia Jr."}
h=connect(); p=h.project
def wk(f,acc):
    for c in f.GetClipList() or []: acc[c.GetName()]=c
    for s in f.GetSubFolderList() or []: wk(s,acc)
    return acc
def fps(mpi):
    try: return float(mpi.GetClipProperty("FPS"))
    except Exception: return FPS
tls={p.GetTimelineByIndex(i).GetName():p.GetTimelineByIndex(i) for i in range(1,p.GetTimelineCount()+1)}
old=[tls[n] for n in ("Shriners Empowerment Piece","Shriners Empowerment Piece 16x9") if n in tls]
if old: p.GetMediaPool().DeleteTimelines(old)
p.SetCurrentTimeline(tls["Valley State Prison Mini Doc"]); h=connect()
tl=duplicate_for_edit(h,"Shriners Empowerment Piece 16x9","shriners")
mp=h.project.GetMediaPool(); pool=wk(mp.GetRootFolder(),{})
tl.DeleteClips([it for k in ("video","audio") for i in range(1,tl.GetTrackCount(k)+1) for it in (tl.GetItemListInTrack(k,i) or [])],False)
for m in list((tl.GetMarkers() or {}).keys()): tl.DeleteMarkerAtFrame(m)
def place(mpi,src_s,L,rec,tr,mt):
    r=fps(mpi)/FPS; sf=int(round(src_s*fps(mpi))); span=int(round(L*r))
    for shift in (0,1,-1):
        for extra in (1,0,2,-1):
            got=_placed_item(mp.AppendToTimeline([{"mediaPoolItem":mpi,"startFrame":sf+shift,"endFrame":sf+shift+span-1+extra,"recordFrame":rec,"trackIndex":tr,"mediaType":mt}]))
            if got is None: continue
            if int(got.GetStart())==rec and int(got.GetEnd())==rec+L: return got
            tl.DeleteClips([got],False)
    return None
base=int(tl.GetStartFrame()); rec=base; bad=[]; log=[]; cam=0
for n,o in enumerate(plan):
    main,second,off=SRC[o["f"]]; A,C=pool[main],pool[second]
    L=int(round((o["e"]-o["s"])*FPS))
    for mpi,s,tr,mt in ((A,o["s"],1,1),(C,o["s"]-off,2,1),(A,o["s"],1,2),(C,o["s"]-off,2,2)):
        if place(mpi,s,L,rec,tr,mt) is None: bad.append((n,mpi.GetName(),mt))
    v2=[it for it in tl.GetItemListInTrack("video",2) or [] if int(it.GetStart())==rec]
    if cam%2==0 or main==second:
        for it in v2: it.SetClipEnabled(False)
    tl.AddMarker(rec-base,"Blue","%02d %s"%(n+1,o["beat"]),"%s | %s %.2f-%.2f s\n%s"%(WHO[o["f"]],main,o["s"],o["e"],o["text"]),1)
    log.append((rec-base,L,n,o,"main" if cam%2==0 else "second")); cam+=1; rec+=L
tl.SetTrackName("video",1,"Interview main angle"); tl.SetTrackName("video",2,"Interview second angle")
tl.SetTrackName("audio",1,"Dialogue (main mic)"); tl.SetTrackName("audio",2,"Second angle audio"); tl.SetTrackEnable("audio",2,False)
gaps=0
for k in ("video","audio"):
    for i in (1,2):
        its=tl.GetItemListInTrack(k,i) or []; gaps+=sum(1 for a,c in zip(its,its[1:]) if int(c.GetStart())!=int(a.GetEnd()))
        if its and int(its[0].GetStart())!=base: gaps+=1
h.project_manager.SaveProject()
def tc(f): s=f/FPS; return "%02d:%05.2f"%(s//60,s%60)
with open(HERE+"/shriners_edit_list.txt","w") as fh:
    for at,L,n,o,c in log: fh.write("%s-%s  %s %s %.2f-%.2f (%s angle)  \"%s\"\n"%(tc(at),tc(at+L),WHO[o["f"]],SRC[o["f"]][0],o["s"],o["e"],c,o["text"]))
print("16:9 DONE",tl.GetName(),{f"{k}{i}":len(tl.GetItemListInTrack(k,i) or []) for k in("video","audio") for i in (1,2)},"gaps:",gaps,"failed:",bad,"length",tc(rec-base))
h=connect(); p.SetCurrentTimeline(tl); h=connect()
print(make_aspect_version(h,"9:16",name="Shriners Empowerment Piece"))
h.project_manager.SaveProject()
