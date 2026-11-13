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

## Color: white balance in RAW comes FIRST
- Before any node, LUT or CST work on RED (or any RAW) footage, set the clip's Camera Raw tab: **Decode Using = Clip**, then ISO, Color Temp and Tint to balance the picture. That is why the editor shoots RAW. The camera's as-shot values (e.g. ISO 2000, 5600 K, Tint 0) look yellow/green and are only a starting point.
- Only after that: node tree (the editor's: `~/Documents/Theodore/Node Trees/`, apply with `Graph.ApplyGradeFromDRX(path, 0)`; `theodore/color/drx_patch.py` retargets the first CST's camera input). RED setup: Color Space REDWideGamutRGB, Gamma Curve Log3G10, node 1 CST = RWG/Log3G10 -> DWG/Intermediate.
- Reference (CREATIVE FORUM 2026, R058_C039_0101FU_001.R3D, set by the editor 2026-11-13): ISO 2000 -> 500, Color Temp 5600 -> 4080, Tint 0 -> -9.91, Exposure Adjust 0. Scene-specific: use as a starting guess for a similar scene, never copy blindly; check the picture/scopes after.
- The scripting API cannot read or set Camera Raw (Decode Using, gamma, ISO, temp, tint). Drive the UI: `screencapture` to see, CoreGraphics mouse events to click (open a dropdown and pick the item in ONE run; Resolve must be frontmost). Read the values back from a screenshot and tell the editor what you set.
