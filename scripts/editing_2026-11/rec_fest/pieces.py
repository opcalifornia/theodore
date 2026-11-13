import json, glob, subprocess, sys
R="/Volumes/Pool 1/Rec Fest Year 2"; O=R+"/Theodore Recap Audio"
def words(k):
    r=json.load(open(glob.glob(f"DJI_{k}_*.json")[0])); return [w for s in r["segments"] for w in s.get("words",[])]
P=[("P01_Damien_my_people","40",10.0,12.5,"My","people."),
   ("P02_Damien_second_annual","40",14.0,16.7,"We","Wreckfest."),
   ("P06_DrReynolds_India_Basin","39",46.9,59.2,"Wreckfest","around."),
   ("P07_MC_we_kayak","39",397.4,404.0,"We","outside."),
   ("P08_MC_we_outside_baby","39",407.6,409.8,"We","baby."),
   ("P03_Damien_kids_outside","40",28.2,33.6,"Getting","man."),
   ("P04_Damien_beautiful_world","40",35.0,40.2,"It's","people."),
   ("P09_MRB_intro","43",173.2,180.1,"We're","Lena"),
   ("P10_MRB_waterfront","43",182.0,186.7,"We","Park"),
   ("P11_MRB_shine","43",129.9,138.6,"If","can."),
   ("P05_Damien_lets_go","40",41.1,41.9,"Let's","go.")]
out=[]
for name,k,a,b,sw,ew in P:
    ws=words(k); idx=[i for i,w in enumerate(ws) if w["start"]>=a-0.2 and w["end"]<=b+0.3]
    i0=next(i for i in idx if ws[i]["word"].strip().startswith(sw)); i1=[i for i in idx if ws[i]["word"].strip().rstrip(",")==ew.rstrip(",") or ws[i]["word"].strip()==ew][-1]
    st=ws[i0]["start"]; en=ws[i1]["end"]
    prev=ws[i0-1]["end"] if i0>0 else 0; nxt=ws[i1+1]["start"] if i1+1<len(ws) else en+1
    s=max(st-0.15, prev+0.03); e=min(en+0.35, nxt-0.04)
    src=glob.glob(f"{R}/DJI_{k}_*.WAV")[0]; dst=f"{O}/{name}.wav"
    subprocess.run(["ffmpeg","-v","error","-y","-ss",f"{s:.3f}","-t",f"{e-s:.3f}","-i",src,
        "-af",f"highpass=f=80,loudnorm=I=-16:TP=-1.5:LRA=11,afade=t=in:d=0.03,afade=t=out:st={max(0,e-s-0.06):.3f}:d=0.06",
        "-ar","48000","-ac","1","-c:a","pcm_s24le",dst],check=True)
    text="".join(w["word"] for w in ws[i0:i1+1]).strip()
    out.append(dict(name=name,file=dst,dur=e-s,text=text)); print(f"{name:32s} {e-s:5.2f}s  {text}")
json.dump(out,open("pieces.json","w"),indent=1)
