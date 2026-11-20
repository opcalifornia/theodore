import numpy as np, pickle
from theodore.edit.panel import Source, activity, _runs, HOP, _probe
D="/Volumes/Extreme Pro/Creative Couch/Episode 2/"
def lav(name, f1, f2, s1, s2):
    parts=[]
    for f,s in ((f1,s1),(f2,s2)):
        info=_probe(f); parts.append({"path":f,"start":s,"duration":info["duration"],"tc":0.0,"info":info,"name":f.split("/")[-1]})
    return Source(name=name, path=f1, kind="lav", info=parts[0]["info"], person=name, parts=parts)
L1=lav("Lav1", D+"Lav Mic #1/DJI_07_20261112_111627.WAV", D+"Lav Mic #1/DJI_07_20261112_114713.WAV", -9805/24, (120898-86400)/24)
L2=lav("Lav2", D+"Lav Mic #2/DJI_06_20261112_111627.WAV", D+"Lav Mic #2/DJI_06_20261112_114715.WAV", -9793/24, (120909-86400)/24)
length=(133867-86400)/24
m=activity([L1,L2], length, 0.0)
pickle.dump(m, open("/private/tmp/claude-501/-Users-admin-theodore/aa91f910-6ebd-5bc7-80ad-a021f2ecd6b8/scratchpad/mc/masks.pkl","wb"))
for k,v in m.items():
    r=_runs(v); print(k, "talk sec", round(v.sum()*HOP), "runs", len(r))
    print("  first", [(round(a*HOP,1), round(b*HOP,1)) for a,b in r[:12]])
    print("  last", [(round(a*HOP,1), round(b*HOP,1)) for a,b in r[-10:]])
