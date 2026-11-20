"""Run with ~/.theodore-stt/bin/python (mlx-whisper lives there): python stt_worker.py in.wav out.json"""
import json, subprocess, sys, tempfile, os
import mlx_whisper
src, out = sys.argv[1], sys.argv[2]
tmp = tempfile.mktemp(suffix=".wav")
subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", src, "-ac", "1", "-ar", "16000", tmp], check=True)
res = mlx_whisper.transcribe(tmp, path_or_hf_repo="mlx-community/whisper-large-v3-turbo", language="en", condition_on_previous_text=False)
os.remove(tmp)
json.dump([dict(s=round(x["start"], 2), e=round(x["end"], 2), t=x["text"].strip()) for x in res["segments"]], open(out, "w"))
