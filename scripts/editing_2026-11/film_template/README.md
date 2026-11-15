# Film template (pre-production skeleton)

Resolve project **Theodore Film Template**, timeline **M01-L01 Film Template** (Timelines bin), 23.976, 1080p,
program slate 00:59:50:00, picture 01:00:00:00. Media lives in `~/Documents/Theodore/Templates/Film Template Media`.

- `design.py`: HUD slate designs (camera slate per shot, lower third, program slate). Pillow only.
- `build_template.py`: the shot plan (SPINE / OVER / META), IDs, scene/roll/take, placeholder media, FCPXML.
- `build_project.py`: first-time project + bins (run once).
- `build_v2.py`: imports the video spine + dissolves from FCPXML, then lays all audio by API on exact
  tracks (Resolve ignores FCPXML audio lanes), links lav + camera audio to video, fades, markers.
  Retires the previous timeline to Timelines/Working as `Theodore checkpoint - ...` (never deletes).

Reference frames: drop images in `references/<shot ID>/`, then
`python build_template.py && python build_v2.py --refresh` (slates rewritten in place + ReplaceClip relink,
references imported to the lesson's References bin; timeline untouched).
Overwriting media in place without the relink shows Media Offline in Resolve.
