import json, glob, os, sys
from theodore.resolve.connection import connect
from theodore.resolve.timeline_ops import _placed_item
S=os.path.dirname(os.path.abspath(__file__)); O="/Volumes/Pool 1/Rec Fest Year 2/Theodore Recap Audio"
FPS=24000/1001
pieces={p["name"]:p for p in json.load(open(S+"/pieces.json"))}
clips={l.split("|")[0].split("/")[-1][5:9]:int(l.split("|")[2]) for l in open(S+"/clips.txt")}
order=["P01_Damien_my_people","P02_Damien_second_annual","|","P06_DrReynolds_India_Basin","|","P07_MC_we_kayak","P08_MC_we_outside_baby","|",
       "P03_Damien_kids_outside","P04_Damien_beautiful_world","|","P09_MRB_intro","P10_MRB_waterfront","|","P11_MRB_shine","P05_Damien_lets_go"]
sections=[[("C043",46),("C048",46),("C110",82)],
          [("C001",64),("C066",46),("C037",46),("C036",82),("C031",46),("C075",64)],
          [("C078",46),("C026",64),("C023",64),("C020",46),("C005",46)],
          [("C022",46),("C021",64),("C042",82),("C060",82),("C076",46),("C080",28)],
          [("C091",64),("C115",28),("C090",46),("C089",64),("C117",46),("C094",64)],
          [("C087",64),("C106",46),("C058",64),("C114",64),("C121",64),("C097",64)]]
h=connect(require_timeline=False); p=h.project; mp=p.GetMediaPool(); root=mp.GetRootFolder()
assert p.GetName()=="Rec Fest Year 2 Recap"
def find(folder,pred,acc):
    for c in folder.GetClipList() or []:
        if pred(c): acc.append(c)
    for f in folder.GetSubFolderList() or []: find(f,pred,acc)
    return acc
red={}
for c in find(root,lambda c:c.GetName().endswith(".R3D"),[]): red.setdefault(c.GetName()[5:9],c)
sub=[f for f in root.GetSubFolderList() if f.GetName()=="Recap Audio"]
fold=sub[0] if sub else mp.AddSubFolder(root,"Recap Audio")
if not fold.GetClipList():
    mp.SetCurrentFolder(fold); mp.ImportMedia(sorted(glob.glob(O+"/*.wav")))
aud={c.GetName()[:-4]:c for c in fold.GetClipList()}
print("audio clips",len(aud),"red",len(red))
name="Rec Fest Recap 60"
tl=mp.CreateEmptyTimeline(name); p.SetCurrentTimeline(tl)
while tl.GetTrackCount("audio")<2: tl.AddTrack("audio","stereo")
tl.SetTrackName("video",1,"B-Roll"); tl.SetTrackName("audio",1,"Dialogue"); tl.SetTrackName("audio",2,"Music")
T0=int(tl.GetStartFrame())
def place(info,want):
    for extra in (0,1,-1,2):
        i=dict(info,endFrame=info["startFrame"]+want-1+extra)
        got=_placed_item(mp.AppendToTimeline([i]))
        if got is None: continue
        if int(got.GetStart())==info["recordFrame"] and int(got.GetEnd())==info["recordFrame"]+want: return got
        tl.DeleteClips([got],False)
    print("  FAILED",info.get("recordFrame"),want); return None
# dialogue
cur=T0+24; sec_bounds=[T0]; log=[]
for n in order:
    if n=="|": cur+=8; sec_bounds.append(cur-6); continue
    c=aud[n]; L=int(round(pieces[n]["dur"]*FPS))
    place({"mediaPoolItem":c,"startFrame":0,"recordFrame":cur,"trackIndex":1,"mediaType":2},L)
    log.append((n,cur-T0,L)); cur+=L+6
end=cur+30; sec_bounds.append(end)
# b-roll
for (a,b),shots in zip(zip(sec_bounds,sec_bounds[1:]),sections):
    k=len(shots); edges=[a+round((b-a)*i/k) for i in range(k+1)]
    for (c,pct),s,e in zip(shots,edges,edges[1:]):
        d=e-s; n=clips[c]; mid=n*pct//100; st=max(0,min(n-d-1,mid-d//2))
        place({"mediaPoolItem":red[c],"startFrame":st,"recordFrame":s,"trackIndex":1,"mediaType":1},d)
# music
mus=aud["Music_bed_drums"]
place({"mediaPoolItem":mus,"startFrame":0,"recordFrame":T0,"trackIndex":2,"mediaType":2},end-T0)
print("timeline",name,"frames",end-T0,"=",round((end-T0)/FPS,1),"s")
print({k:len(tl.GetItemListInTrack(t,i) or []) for k,(t,i) in {"V1":("video",1),"A1":("audio",1),"A2":("audio",2)}.items()})
json.dump(dict(log=log,sec=sec_bounds,T0=T0),open(S+"/built.json","w"))
p.GetProjectManager().SaveProject() if hasattr(p,"GetProjectManager") else h.project_manager.SaveProject()
