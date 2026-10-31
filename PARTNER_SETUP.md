# Theodore: Claude editing inside DaVinci Resolve

You tell Claude what you want ("cut this panel into a 60 and a 9:16 with captions"), and it does it
in your Resolve project: sync, multicam, story cuts, clean-up, B-roll, titles, captions, social
versions, transitions, and camera-aware grading to a reference image. Everything runs on **your
Claude plan** through Claude Code. There's no API key and no other subscription.

## What you need
- A Mac with **DaVinci Resolve Studio** (19 or newer; built and tested on 21)
- A Claude plan (Pro or Max). Claude Code gets installed for you if it's missing.
- [Homebrew](https://brew.sh), so the installer can add `ffmpeg` if needed

## Install (once, about 5 minutes)
```bash
git clone https://github.com/opcalifornia/theodore.git ~/theodore
cd ~/theodore
./install.sh
```
If Claude Code was just installed, run `claude` once and sign in with your claude.ai account.

Then do two things in Resolve, also once:
1. **DaVinci Resolve → Preferences → System → General → "External scripting using": Local**, then restart Resolve.
2. **Workspace → Workflow Integrations → Theodore** opens the chat panel.

Optional, and faster: run **Workspace → Scripts → Theodore → Theodore Bridge** once each time you open Resolve.

## Use it
Type what you want in the Theodore panel. Some examples:
- "Organize the project."
- "Build the multicam from the Cameras and Lavs bins. Cam B is on Daniel."
- "Cut the interview in the Interviews bin into a :60 and a :30 for Instagram. Lower thirds with names, captions."
- "Make a vertical version that follows whoever is talking."
- "Grade the timeline to match this still." (Drop a frame from any film onto the panel.)
- "Edit it like this." (Drop in a finished video. Claude studies how it's cut, then copies the style.)
- "Move everything 60 seconds to the right."

Claude also has the same tools in Claude Code (`claude` in Terminal) and in the Claude desktop
app (restart it after installing).

## How it keeps your work safe
- Every edit goes onto a **new, duplicated timeline**. Your original is never touched unless you
  say "in place".
- It only asks first before deleting something or starting a render.
- Source media and projects are never deleted.
- Projects are left organized: footage in bins by camera, named tracks, captions on the subtitle
  track, multicams kept switchable, and grades as one LUT on node 1 that you can adjust. You can
  take the wheel at any point.

## Updating
```bash
cd ~/theodore && git pull && ./install.sh
```
Then close and reopen the Theodore panel in Resolve.
