import json, os, sys, mlx_whisper
V=os.path.dirname(os.path.abspath(__file__))
for n in [l.strip() for l in open(sys.argv[1])]:
    o=f"{V}/tx/{n}.json"
    if os.path.exists(o): continue
    r=mlx_whisper.transcribe(f"{V}/a16/{n}.wav", path_or_hf_repo="mlx-community/whisper-large-v3-turbo", language="en", word_timestamps=True, condition_on_previous_text=False)
    json.dump(r,open(o,"w")); print(n, flush=True)
