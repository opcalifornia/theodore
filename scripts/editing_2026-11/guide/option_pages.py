"""Content for the one-page-per-option guide (A-H). Run by split_options.py."""
OPTS = [
dict(L="A", c="violet", name="LIVE TITLE OVERLAY", tag="NOT BUILT", tc="dim",
 what="Replaces the 47 baked overlay images with one editable Fusion title in Resolve. The title shows the angle ID, whether it is a new angle or a return, the angle type, lens, lighting, movement, a replicate-it tip and the angle grid, all as live text that slides in on each new angle over a frosted-glass blur.",
 can=["Show angle ID, NEW or BACK-TO-THIS-ANGLE, type, lens, light and movement as text fields",
      "Fill the angle grid (sizes by heights) with a count of how often each angle was used",
      "Animate in only when the angle changes, never on every cut or camera move",
      "Wear any of the three skins: Neon, Hollywood or Full Frame",
      "Be restyled once in the Inspector so every angle updates together"],
 useful=["Fixing a typo takes seconds instead of re-making 47 images",
         "The look stops feeling stale and can match each class or brand",
         "Students read the same data in a cleaner, moving layout"],
 ex="Change the wording of one replicate-it tip, or the font of the whole overlay, and all 47 angle changes update at once.",
 needs="Nothing new. Pick a skin: Neon, Hollywood or Full Frame.", dots=3,
 status="Today the overlay is baked images; the live version is a design only.",
 watch="The Fusion template plus a script to fill it per angle is the tricky part. Theodore tests on three angles before doing all of them.",
 say="Build A in the Hollywood skin."),
dict(L="B", c="gold", name="COVERAGE TRACKER", tag="PARTLY BUILT", tc="gold",
 what="Gives every angle slot keywords (like WIDE, LOW) and a status colour, then keeps a live list of the angles still to shoot. As takes are assigned, the angle grid fills in, so anyone can see what coverage exists and what is missing.",
 can=["Tag each slot with size, height and movement keywords that Resolve can search",
      "Colour a slot for planned, shot, selected, synced or graded",
      "List the angles not yet covered, with the gaps shown in the angle grid",
      "Keep all of it inside the project, so it travels with the timeline",
      "Show a scene's angle diversity at a glance"],
 useful=["On shoot day the crew sees what is still missing before they wrap",
         "Students learn to plan variety, not just repeat one angle",
         "A producer can read progress without opening a single clip"],
 ex="Mid-shoot you ask for the list and see OTS and low-angle wide are still empty, so you shoot them before moving on.",
 needs="The shot list, and which angle types matter to you.", dots=2,
 status="Metadata and status colours already work in Resolve.",
 watch="The scripting API Theodore checked cannot create Smart Bins, so the saved-search bin is a one-time click for you.",
 say="Build B for the John Wick angles."),
dict(L="C", c="gold", name="COMPARE MODE", tag="NOT BUILT", tc="dim",
 what="Puts a student's take beside the reference for each angle. Resolve's Color page can wipe or split-screen a still against a clip, and the waveform and parade scopes show whether the lighting matches. Theodore sets up the stills and the layout, so the check takes seconds.",
 can=["Load the reference still for each slot, ready next to the take",
      "Set up a wipe or split so framing and height can be compared",
      "Use the scopes to compare how bright and how colourful the take is",
      "Give a four-line checklist per angle: framing, height, lens, light",
      "Keep the reference stills private and out of any student document"],
 useful=["Turns \"does it look right?\" into a quick, visible check",
         "Gives students specific feedback on what to change for the next take",
         "Builds the habit of matching an angle on purpose"],
 ex="A student's take is too high and too bright; the wipe shows the gap and the scopes show how far off the lighting is.",
 needs="Reference stills (kept private) and the student takes.", dots=2,
 status="Not built. The layout and scopes are standard Resolve features.",
 watch="Wiping is a Resolve screen feature. Theodore sets up the stills and layout but cannot drive the wipe from a script. Film frames never go into a student guide.",
 say="Build C, here are my reference stills."),
dict(L="D", c="orange", name="CUT & CAMERA CHECK", tag="NOT BUILT", tc="dim",
 what="A second opinion on the cut list and on camera movement. Theodore cross-checks its own cuts against Resolve's built-in Scene Cut Detection, and uses Fusion's camera tracker to measure pans, tilts and pushes instead of guessing the movement row.",
 can=["Compare detected cuts against Theodore's list and flag every disagreement",
      "Catch false cuts: door swings, flashes, whip pans, light flicker",
      "Measure camera movement from the picture, not from a guess",
      "Shrink the review to a short list of spots for you to judge",
      "Re-run after you fix an angle, to confirm the list still holds"],
 useful=["Fewer wrong angle changes, so the overlay and guide can be trusted",
         "Objective movement labels students can reproduce",
         "Saves you scrubbing the whole scene to find mistakes"],
 ex="Resolve sees a cut where a door swings; Theodore flags it, you confirm it is one shot, and the angle count is corrected.",
 needs="Only the footage already in Resolve.", dots=3,
 status="Not built, and untested on very dark, flickering footage.",
 watch="Every detector gets fooled in the dark, so you stay the final judge. Three false cuts were already fixed by hand.",
 say="Run D on the scene."),
dict(L="E", c="orange", name="SOUND ROW", tag="NOT BUILT", tc="dim",
 what="Uses Resolve's own transcription and audio classification to fill the SOUND field for every shot: dialogue, gunfire, music, room tone. The sound row becomes real data with timecodes instead of \"unverified\".",
 can=["Transcribe speech with timecodes",
      "Label dialogue, effects, music and room tone per shot",
      "Fill the SOUND field in the overlay, the sheet and the guide",
      "Flag shots whose sound it cannot identify instead of guessing",
      "Give students sound cues they can recreate with simple gear"],
 useful=["Sound is half the scene, and now it is studied like the picture",
         "Searchable transcript to find a line or a cue fast",
         "Honest labels: measured where possible, UNVERIFIED where not"],
 ex="Shot 12 is tagged dialogue under low music; the guide tells students to record the line close and add a quiet bed.",
 needs="A legitimate copy that has audio. The screen recording is silent.", dots=2,
 status="Not built. The calls exist in this Resolve's scripting API but have not been tried.",
 watch="Audio from a film copy stays private study material, never in a student guide.",
 say="Run E, here is the audio copy."),
dict(L="F", c="blue", name="SHEET + GUIDE EXPORT", tag="DATA READY", tc="gold",
 what="Turns the angle log into two documents: a shot list sheet with one row per shot (ID, in, out, size, angle, movement, lens, light, action, sound) and a student guide with timecodes, written descriptions and low-budget how-to tips. Written descriptions only, never film frames.",
 can=["Export a sortable spreadsheet of every shot and angle",
      "Build a designed PDF guide like this one, organised by angle",
      "Add generic, low-budget tips for each angle type",
      "Mark anything unverified clearly so students are not misled",
      "Regenerate both documents any time the angle log changes"],
 useful=["Handouts and a shot list production can use on set",
         "Students study the scene from words and timecodes, not copied frames",
         "One source of truth: fix the log once, both documents update"],
 ex="Hand students the PDF to study angle by angle, and give the crew the sheet sorted by size to plan the shoot.",
 needs="Your pass through the angle markers first, so it is built on a clean list.", dots=2,
 status="The data is ready; the two documents are not built yet.",
 watch="The tips are generic how-tos, not claims about how the film was made.",
 say="Build F from the angle log."),
dict(L="G", c="blue", name="SHOT-LIST BUILDER", tag="BUILT", tc="green",
 what="Give Theodore a shot list or a script and it creates the whole pre-production skeleton: bins (A-Cam, B-Cam, Audio, Graphics) for every lesson, one timeline per lesson, a colour-coded marker per shot at its planned length, a slate for every shot, and the ID list production slates with.",
 can=["Give every shot an ID like M01-L01-S01 (module, lesson, shot)",
      "Colour markers: blue interview, green B-roll, yellow transition, purple title",
      "Build a slate per shot in Neon, Hollywood or Full Frame",
      "Drop reference stills straight into each slate",
      "Rebuild everything from the CSV after you change the shot list"],
 useful=["A project is ready in minutes instead of hours of setup",
         "Consistent slating means takes can be matched later",
         "Students see the full plan as a coloured, labelled timeline"],
 ex="Edit one row of shot_list.csv, re-run, and the timeline, markers, slate and ID list all regenerate.",
 needs="The shot list: lesson, shot type, description, planned length.", dots=1,
 status="Built and working on a 21-shot sample.",
 watch="The sample is invented. Director, DP and editor read \"TBD\" until you supply real names.",
 say="Build G from this shot list."),
dict(L="H", c="green", name="SWAP-IN LOOP", tag="DRY-RUN ONLY", tc="orange",
 what="The on-set DIT role. Theodore reads the slate on each take with Resolve's Slate ID, matches it to its placeholder, drops it in at the planned length with the lavs synced and linked, and keeps unused takes in the bin. The timeline builds itself as you shoot.",
 can=["Match each take to its shot by slate, shot ID and roll order",
      "Sync lav audio to camera, checking the start and end for drift",
      "Keep each transmitter's own offset, and link audio under the video",
      "Show planned length against actual length on every shot",
      "Flag unclear slates for you instead of guessing"],
 useful=["A first assembly exists by the time the shoot wraps",
         "You see at once which shots are covered and which are not",
         "Placeholders from Option G or J turn into real footage in place"],
 ex="You slate S04, shoot three takes, and S04 fills with take 3 at the planned length while takes 1 and 2 wait in the bin.",
 needs="A few real slated takes and the lav files to test on safely.", dots=4,
 status="Only the read-only matcher exists (dry run).",
 watch="Whether Resolve's slate reader fills Scene and Take from a script is untested. Viewing all takes as one clip is under test (see Option J).",
 say="Set up H, here are my slated takes."),
]

CSS = """
  .op{--c:var(--cyan)}
  .op .h2{font-size:15px;color:var(--dim);margin:14px 0 8px;letter-spacing:.18em}
  .op .bd{font-size:27px;line-height:1.42;color:#d6dde8}
  .op ul.tight li{font-size:25px;line-height:1.32;margin-bottom:10px}.op ul.tight li::before{background:var(--c);top:13px}
  .op .st{border:1px solid var(--line);border-radius:14px;padding:14px 20px;background:rgba(255,255,255,.035)}
  .op .st p{font-size:23px;line-height:1.38;color:#d6dde8;margin-top:6px}
"""

def page(o, n):
    c = "var(--%s)" % o["c"]; tcol = "var(--%s)" % o["tc"]
    can = "".join("<li>%s</li>" % x for x in o["can"])
    use = "".join("<li>%s</li>" % x for x in o["useful"])
    dots = "".join('<i class="on"></i>' if i < o["dots"] else "<i></i>" for i in range(4))
    return f'''<!-- OPTION {o["L"]} -->
<section class="page op" style="--c:{c}">
  <div class="corner c1"></div><div class="corner c2"></div><div class="corner c3"></div><div class="corner c4"></div>
  <div class="row" style="gap:26px"><span class="badge din" style="background:{c};width:104px;height:104px;font-size:80px;border-radius:20px">{o["L"]}</span>
    <div><div class="kick mono" style="color:{c};margin:0 0 10px">Option {o["L"]}</div><h1 class="t din" style="font-size:88px;margin:0;line-height:1;color:{c}">{o["name"].replace("&","&amp;")}</h1></div>
    <span class="tag mono" style="color:{tcol};margin-left:auto;align-self:flex-start;margin-top:16px">{o["tag"]}</span></div>
  <div style="height:3px;background:{c};opacity:.8;border-radius:2px;margin:16px 0 4px"></div>
  <div class="grid2" style="gap:44px;margin-top:6px">
    <div>
      <div class="h2 mono">What it does</div><p class="bd">{o["what"]}</p>
      <div class="h2 mono">What it can do</div><ul class="tight">{can}</ul>
    </div>
    <div>
      <div class="h2 mono">Why it is useful</div><ul class="tight">{use}</ul>
      <div class="h2 mono">In practice</div><div class="st" style="border-color:{c}"><p style="margin:0;font-size:25px">{o["ex"]}</p></div>
    </div>
  </div>
  <div class="grid3" style="gap:20px;margin-top:20px">
    <div class="st"><div class="mono" style="font-size:13px;color:{c}">Theodore needs</div><p>{o["needs"]}</p></div>
    <div class="st"><div class="mono" style="font-size:13px;color:{c}">Effort and status</div><p><span class="dots">{dots}</span><br>{o["status"]}</p></div>
    <div class="st"><div class="mono" style="font-size:13px;color:{c}">Watch out</div><p>{o["watch"]}</p></div>
  </div>
  <div class="say" style="position:absolute;left:96px;right:96px;bottom:74px;padding:12px 24px"><span class="mono">Say it</span><b class="din" style="font-size:34px;color:{c}">"{o["say"]}"</b></div>
  <div class="foot"><span class="mono">Theodore · Field Guide · Option {o["L"]}</span><span class="mono">{n:02d}</span></div>
</section>

'''
