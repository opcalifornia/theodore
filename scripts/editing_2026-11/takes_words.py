"""Word-level transcripts of lav files (run with ~/.theodore-stt/bin/python): takes_words.py out.json file1.wav file2.wav ..."""
import json, subprocess, sys, tempfile, os
import mlx_whisper
out, files = sys.argv[1], sys.argv[2:]
res = {}
for src in files:
    tmp = tempfile.mktemp(suffix=".wav")
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", src, "-ac", "1", "-ar", "16000", tmp], check=True)
    r = mlx_whisper.transcribe(tmp, path_or_hf_repo="mlx-community/whisper-large-v3-turbo", language="en", condition_on_previous_text=False, word_timestamps=True)
    os.remove(tmp)
    res[os.path.basename(src)] = [dict(w=w["word"].strip(), s=round(w["start"], 2), e=round(w["end"], 2)) for seg in r["segments"] for w in seg.get("words", [])]
json.dump(res, open(out, "w"))
