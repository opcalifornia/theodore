from theodore.edit import compare


def test_measure_reads_cutaways_face_holds_and_visible_cuts():
    base = [(0, 10), (10, 20), (20, 30)]            # three selects, cuts at 10 and 20
    over = [(4, 7), (9, 12), (25, 26)]              # cutaway over the 10s cut, one at the end
    m = compare.measure(base, over, 30.0)
    assert m["cutaways"] == 3 and m["first_cutaway_at"] == 4
    assert m["broll_pct"] == round(100 * 7 / 30, 1)
    assert m["cutaway_seconds"]["max"] == 3 and m["cutaway_seconds"]["min"] == 1
    # cut at 10 is hidden under the 9-12 cutaway; visible: cut at 20 + 6 cutaway edges
    assert m["cuts_per_minute"] == round(7 / (30 / 60), 1)
    assert m["longest_uninterrupted_face_seconds"] == 13.0   # 12 -> 25


def test_compare_flags_what_is_off_with_a_fix():
    ours = {"average_shot_length": 8.0, "cuts_per_minute": 7.0, "broll_pct": 10.0, "cutaways": 2,
            "cutaway_seconds": {"median": 3.0}, "first_cutaway_at": 12.0,
            "longest_uninterrupted_face_seconds": 20.0}
    ref = {"pacing": {"average_shot_length": 4.0, "cuts_per_minute": 15},
           "layers": {"summary": {"time_pct": {"broll_over_speech": 30, "broll_clean": 5},
                                  "cutaway_seconds": {"median": 3.2}, "first_cutaway_at": 6.0,
                                  "longest_uninterrupted_face_seconds": 8.0}}}
    out = compare.compare(ours, ref)
    bad = {r["measure"] for r in out["rows"] if not r["matches"]}
    assert bad == {"average shot length (s)", "cuts per minute", "B-roll share of runtime (%)",
                   "first cutaway at (s)", "longest stretch on the face (s)"}
    assert out["verdict"] == "5 measure(s) off" and any("place more cutaways" in f for f in out["fixes"])
    assert compare.compare(ours | {"average_shot_length": 4.1}, {"pacing": {"average_shot_length": 4.0}})["verdict"] == "close match"
