import json, re, wave, numpy as np, mlx_whisper
M="/private/tmp/claude-501/-Users-admin-theodore/412eb248-a16d-5d91-aec8-d9cc64479fe2/scratchpad/mil"
wv=wave.open(M+"/mil.wav"); sr=wv.getframerate(); X=np.frombuffer(wv.readframes(wv.getnframes()),np.int16)
def norm(s): return re.sub(r"[^a-z]","",s.lower())
# (label, approximate source seconds, tokens to cover)
T=[("Tana (child) 1:28",88.0,["tana"]),("Tana (child) 11:07",667.2,["tana"]),("Captain Allie Fleming",548.5+360-0.0,["allie","fleming"]),
   ("Mrs. Saha 22:24",1344.0,["mrs","saha"]),("SSG Joshua Monroe",1572.9,["joshua","monroe"]),("Ms. Saha 26:32",1592.3,["ms","sahad"]),("Miss Saha 34:06",2045.8,["miss","saha"])]
out=[]
for lab,t,tok in T:
    a=max(0,t-6); b=t+9; seg=X[int(a*sr):int(b*sr)]
    fn=M+"/_w.wav"; w=wave.open(fn,"wb"); w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr); w.writeframes(seg.tobytes()); w.close()
    r=mlx_whisper.transcribe(fn,path_or_hf_repo="mlx-community/whisper-large-v3-turbo",language="en",word_timestamps=True,condition_on_previous_text=False)
    W=[(q["word"].strip(),norm(q["word"]),q["start"]+a,q["end"]+a) for s in r["segments"] for q in s.get("words",[])]
    hit=[]
    for want in tok:
        c=[q for q in W if (q[1]==want or q[1].startswith(want[:4])) and abs(q[2]-t)<5]
        if c: hit.append(min(c,key=lambda q:abs(q[2]-t)))
    print(lab,"| heard:"," ".join("%s@%.2f"%(q[0],q[2]) for q in W if t-3<q[2]<t+4),"| matched:",[(q[0],round(q[2],2),round(q[3],2)) for q in hit])
    out.append(dict(label=lab,approx=t,hits=[(q[0],q[2],q[3]) for q in hit]))
json.dump(out,open(M+"/refined.json","w"))
