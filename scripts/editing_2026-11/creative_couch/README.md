# Creative Couch, Episode 2 (2026-11-12)

Timeline **Synced - RED + Lavs** (24 fps, starts 86400):

| Track | What | Placement |
|---|---|---|
| V1 RED | R057_C042 (full take) | 86640 to 133546 |
| V2 Alexa | A036 then A037 (SD card swap between) | 86735 to 128915, 130987 to 133399 (**editor's sync**) |
| A1 Lav 1 | DJI_07 111627 + 114713, back to back | lav time 418.526 s = RED frame 0 |
| A2 Lav 2 | DJI_06 111627 + 114715, back to back | lav time 418.049 s = RED frame 0 |
| A3 RED scratch | R057_C042 audio | with V1 |

- `xc.py`: RED scratch audio vs lavs (REDline ProRes export, ffmpeg to 8 kHz, FFT cross-correlation). Start and 28 min in agreed within 12 ms: no drift.
- `build.py`: builds the timeline. `place_alexa.py`: rough Alexa placement from what the editor said.
- `arriraw_frames.py`: reads Alexa ARRIRAW MXF frames directly (uncompressed 12-bit, 3424x2202, stride 11313152 from byte 451096) with no render. The picture comes out shifted sideways (header offset not solved yet).
- Motion matching between the two cameras FAILED here (wrong answers at start and end). The editor lined up the Alexa by eye in a minute. No slate was used; markers on the timeline record the sync points.

## Multicam (2026-11-13): timeline "Creative Couch Ep2 - Multicam"
- Lav 1 (DJI_07) = Miss LaShonda (guest, RED). Lav 2 (DJI_06) = Alisona (host, Alexa). Confirmed from the transcripts.
- Interview kept from 45.21 s ("Yo, hello" intro) to 1950.58 s (last outro take), clock 0 = frame 86400 of the synced timeline.
- `speech_activity.py` (who is talking, from the lavs), `transcribe_lavs.py` (mlx-whisper), `multicam_plan.py` (speaker-follow: cut 0.35 s before the new speaker, hold on silence/cross-talk, no shot under 1.5 s, RED during the Alexa card swap), `multicam_fcpxml.py` (FCPXML on the editor's sync).
- 67 shots, frame-exact at 30 min, zero gaps. Lavs on A2/A3 placed with read-back.
- The timeMap conform froze the 23.976 RED in a 24 fps multicam: fixed in theodore/edit/panel.py.
- Open issue: the multicam uses imported copies of the camera clips, and the graded picture looks brighter and framed slightly differently from the synced timeline.
