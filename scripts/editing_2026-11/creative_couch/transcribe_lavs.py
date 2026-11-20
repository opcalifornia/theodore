import sys, json, mlx_whisper
r = mlx_whisper.transcribe(sys.argv[1], path_or_hf_repo="mlx-community/whisper-large-v3-turbo", word_timestamps=True,
                           initial_prompt="Um, uh, so, you know, like. Creative Couch interview.")
json.dump(r["segments"], open(sys.argv[2], "w"))
print(len(r["segments"]))
