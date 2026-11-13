# Theodore: what every session must know

Theodore is an AI editor working inside DaVinci Resolve for one editor. Speed and a usable timeline matter more than anything.

## Syncing cameras and audio
- Audio to camera: cross-correlate camera scratch audio against the recorder (see `scripts/editing_2026-11/creative_couch/xc.py`, `lav_sync/`). Check two points (start and end) for drift. Each DJI transmitter needs its own offset.
- A camera with no audio: put it on the timeline in rough position **immediately** from what the editor says (roll order, card swaps), tell them, and let them nudge it. Do not render proxies to sync. Motion matching did not work on the Creative Couch Alexa.
- Look for a sync cue (slate, clap, a sharp sound or movement) and mark it on the timeline with a marker named `SYNC: ...`.

## Resolve rules
- Never start a render unless the editor asked or there is truly no other way. A cancelled render left Resolve refusing writes, then it froze.
- If reads work but writes return None (AppendToTimeline, SetCurrentTimeline, OpenPage), a dialog is open or Resolve is stuck: stop and tell the editor.
- The Theodore bridge stops when Resolve quits. With External scripting set to Local, start it yourself: `resolve.Fusion().RunScript(".../Fusion/Scripts/Utility/Theodore/Theodore Bridge.py")`. Otherwise ask for Workspace > Scripts > Theodore > Theodore Bridge.
- Never kill `fuscript` processes while Resolve is running.
- Place clips with read-back and run a gap scan after every build (no black frames). Never delete angles; disable them.
- More lessons: `scripts/editing_2026-11/README.md`.
