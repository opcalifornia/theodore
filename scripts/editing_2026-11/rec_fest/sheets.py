from PIL import Image, ImageDraw, ImageFont
import glob, os, collections, json
font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 20)
meta={}
for l in open("clips.txt"):
    f,tc,n,fl=l.strip().split("|"); c=os.path.basename(f)[5:9]; meta[c]=(int(n),fl)
fps={l.split()[0][5:9]:float(l.split()[1]) for l in open("fps.txt")}
by=collections.OrderedDict()
for f in sorted(glob.glob("shotlog/*.jpg")):
    b=os.path.basename(f); c=b[:4]; p=int(b.split("_")[1].split(".")[0]); by.setdefault(c,[]).append((p,f))
W,H=240,126; clips=sorted(by); size=12
for s in range(0,len(clips),size):
    ch=clips[s:s+size]; sheet=Image.new("RGB",(8*W,len(ch)*H),"black"); d=ImageDraw.Draw(sheet)
    for r,c in enumerate(ch):
        n,fl=meta[c]; real=n/fps[c.join(["R033_C","_x"])[0:0]+"R033_C%s_"%c if False else c] if False else None
        for k,(p,f) in enumerate(sorted(by[c])):
            im=Image.open(f).convert("RGB"); im.thumbnail((W-2,H-2)); sheet.paste(im,(k*W+1,r*H+1))
            d.rectangle([k*W+1,r*H+1,k*W+95,r*H+25],fill="black"); d.text((k*W+4,r*H+2),f"{c} {p}%",fill="yellow",font=font)
    sheet.save(f"shotlog_sheet{s//size+1:02d}.jpg",quality=85)
print(len(clips),"clips,",(len(clips)+size-1)//size,"sheets")
