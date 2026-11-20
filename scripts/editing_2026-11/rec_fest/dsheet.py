from PIL import Image, ImageDraw, ImageFont
import glob, os, sys
font=ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Bold.ttf",18)
for c in sys.argv[1:]:
    fs=sorted(glob.glob(f"dense/{c}_*.jpg")); W,H=256,135; cols=6; rows=(len(fs)+cols-1)//cols
    sh=Image.new("RGB",(cols*W,rows*H),"black"); d=ImageDraw.Draw(sh)
    for i,f in enumerate(fs):
        im=Image.open(f).convert("RGB"); im.thumbnail((W-2,H-2)); x,y=(i%cols)*W,(i//cols)*H; sh.paste(im,(x+1,y+1))
        fr=int(os.path.basename(f).split("_")[1].split(".")[0]); d.rectangle([x+1,y+1,x+70,y+22],fill="black"); d.text((x+3,y+2),str(fr),fill="yellow",font=font)
    sh.save(f"dense_{c}.jpg",quality=82); print(c,len(fs),"frames")
