import json, glob, os, mlx_whisper
R="/Volumes/Pool 1/Rec Fest Year 2"; S=os.path.dirname(os.path.abspath(__file__))
for f in sorted(glob.glob(R+"/DJI_*.WAV")):
    r = mlx_whisper.transcribe(f, path_or_hf_repo="mlx-community/whisper-large-v3-turbo", language="en", word_timestamps=True)
    json.dump(r, open(f"{S}/{os.path.basename(f)[:-4]}.json","w"))
    print(os.path.basename(f), flush=True)
