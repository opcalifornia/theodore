"""One shoot in, every deliverable out.

The editor's money job: a multicam shoot becomes the long-form episode, a
highlight, a commercial spot and a set of vertical social clips -- each a
separate, named timeline in Resolve, built from the same synced multicam
timeline, so any of them can be taken over by hand.

  0. multicam    sync + angle-cut the cameras and lavs (or use a timeline that
                 is already built)
  1. episode     the full conversation, ums and dead air out
  2. highlight   a story cut to length, cutting between the people
  3. spot        a tight brand spot with a hook and a call to action
  4. verticals   N different 9:16 clips, each its own story, captioned

Products are independent: if one fails the rest still get built and the
failure is reported, never a silent gap.
"""
from __future__ import annotations

from typing import Callable, Optional

from theodore import brand as brand_mod
from theodore.edit import cleanup, interview, panel


def _open_timeline(h, name: str):
    project = h.project
    for i in range(1, int(project.GetTimelineCount() or 0) + 1):
        tl = project.GetTimelineByIndex(i)
        if tl and tl.GetName() == name:
            project.SetCurrentTimeline(tl)
            return tl
    raise RuntimeError(f"Timeline {name!r} not found in the project.")


def package_shoot(connect: Callable, *, client: str = "Client", brand: Optional[str] = None, brief: str = "",
                  camera_bin=None, cameras=None, lav_bin=None, lavs=None, people=None, camera_for=None,
                  wide=None, source_timeline: Optional[str] = None, episode: bool = True,
                  highlight_seconds: float = 120.0, spot_seconds: float = 30.0, vertical_clips: int = 3,
                  vertical_seconds: float = 45.0, captions: bool = True, subjects: Optional[dict] = None,
                  progress: Optional[Callable] = None) -> dict:
    from theodore.resolve import organize, timeline_ops, transcribe

    say = progress or (lambda m: None)
    if brand and not brand_mod.prompt_text(brand):
        raise ValueError(f"No saved brand for {brand!r}. Use learn_brand or set_brand_profile first.")
    base_brief = (brief + "\n\n" if brief else "") + brand_mod.prompt_text(brand)
    products: list[dict] = []
    out: dict = {"client": client, "products": products}

    def product(kind: str, name: str, fn):
        say(f"{kind}: {name}")
        try:
            res = fn()
        except Exception as exc:  # noqa: BLE001 -- the other products are still worth building
            products.append({"kind": kind, "name": name, "ok": False, "error": f"{type(exc).__name__}: {exc}"})
            return None
        h2 = connect()
        try:
            organize.label_tracks(h2.timeline)
        except Exception:  # noqa: BLE001
            pass
        products.append({"kind": kind, "name": h2.timeline.GetName(), "ok": True,
                         **{k: res[k] for k in ("seconds", "logline", "intercut", "editor_notes") if res.get(k)}})
        return res

    h = connect()
    if source_timeline:
        _open_timeline(h, source_timeline)
        source = source_timeline
    else:
        say("Building the multicam from the cameras and lavs")
        made = panel.multicam_from_bins(h, camera_bin=camera_bin, cameras=cameras, lav_bin=lav_bin, lavs=lavs,
                                        people=people, camera_for=camera_for, wide=wide,
                                        name=f"{client} - Multicam")
        source = made["timeline"]
        out["multicam"] = {k: made[k] for k in ("timeline", "cameras", "seconds_per_angle", "speaking_seconds",
                                                 "cuts", "notes") if k in made}
    out["source_timeline"] = source

    def story(seconds, extra, name):
        def run():
            _open_timeline(connect(), source)
            return interview.build_story(connect(), use_timeline=True, brief=(base_brief + "\n\n" + extra).strip(),
                                         target_seconds=float(seconds), name=name, subjects=subjects, progress=say)
        return run

    if episode:
        def run_episode():
            _open_timeline(connect(), source)
            return cleanup.clean_up(connect(), new_name=f"{client} - Episode")
        product("episode", f"{client} - Episode", run_episode)
    if highlight_seconds:
        product("highlight", f"{client} - Highlight {int(highlight_seconds)}s", story(
            highlight_seconds, "This is the highlight: the best arc of the conversation, hook first.",
            f"{client} - Highlight {int(highlight_seconds)}s"))
    if spot_seconds:
        product("spot", f"{client} - Spot :{int(spot_seconds)}", story(
            spot_seconds, "This is a commercial spot: a hook in the first seconds, the benefit shown, the brand "
            "named, and a clear call to action to finish.", f"{client} - Spot :{int(spot_seconds)}"))

    used: list[str] = []
    for k in range(1, int(vertical_clips) + 1):
        name = f"{client} - Vertical {k}"
        extra = (f"This is vertical social clip {k} of {int(vertical_clips)}: ONE self-contained moment that stops "
                 "the scroll in the first 2 seconds and makes sense with no context."
                 + (" Do NOT use any of these lines, already used in other clips:\n- " + "\n- ".join(used)
                    if used else ""))

        def run_vertical(k=k, name=name, extra=extra):
            res = story(vertical_seconds, extra, name)()
            h3 = connect()
            timeline_ops.make_aspect_version(h3, "9:16", name=f"{name} (9x16)")
            if captions:
                transcribe.add_captions(connect(), "social")
            used.extend(s["line"] for s in res.get("story", []))
            return res

        product("vertical", name, run_vertical)

    try:
        organize.organize_media(connect())
    except Exception:  # noqa: BLE001
        pass
    out["built"] = sum(1 for p in products if p["ok"])
    out["failed"] = [p for p in products if not p["ok"]]
    return out
