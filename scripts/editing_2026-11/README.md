# Editing sessions, Nov 7-8 2026: working scripts

These are the scripts used to build the timelines in the Resolve projects below. They are copies saved so the work is
not lost; they have hard-coded paths to a temporary scratch folder, so edit the paths before re-running.
Run the Resolve scripts with `~/theodore/.venv/bin/python`; run transcription with `~/.theodore-stt/bin/python`
(mlx-whisper, large-v3-turbo; the model is cached in ~/.cache/huggingface).

| Folder | What it did |
|---|---|
| lav_sync/ | MRB @ Callabash: matched RED scratch audio to DJI lav files by cross-correlation (`xc.py`), then laid the lav on A5 under every cut without touching locked tracks (`lay.py`). |
| rec_fest/ | Rec Fest Year 2 60 s recap: dialogue pieces (`pieces.py`), mic transcription (`tx_all.py`), timeline build (`build.py`). The v1 result was judged not good enough; see the memory notes. |
| valley_state/ | Valley State Prison mini doc: `sync.py` finds overlapping angles, `build_doc.py` puts everything on one timeline in day order with other angles on V2, `eli_plan12.py` plans a clean interview cut from audio energy, `eli_full7.py` places it with exact frames and a gap check. |
| military_meeting/ | 36-minute call cut to 24:21 (`cut.py`, `place.py`), bleeping names (`refine.py`, `place_bleep.py`), and the slow-zoom photo overlays (`photos_place.py`). |

Key rules the scripts follow (details in the Claude memory notes): exact frame placement with read-back (endFrame = start+length, retry offsets),
no gaps or overlaps between cuts, never delete an unused angle (disable it with SetClipEnabled(False)), never remove dialogue except
questions, ums and dead pauses, and verify before reporting.
