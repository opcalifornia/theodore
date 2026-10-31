"""Human-readable interview log (Markdown) + machine-readable selects.csv.

This lives in export/, not analyze/, specifically so it can depend on
resolve/timecode.py for timecode display without violating the rule that
analyze/ itself must stay platform-agnostic.
"""
from __future__ import annotations

import csv
import logging
from pathlib import Path

from theodore.resolve import timecode as tc

logger = logging.getLogger("theodore.export.notes")


def build_rows(transcript: dict, analysis: dict) -> list[dict]:
    utterances_by_id = {u["id"]: u for u in transcript["utterances"]}
    selects_by_id = {s["segment_id"]: s for s in analysis.get("selects", [])}
    themes_by_id = {t["id"]: t for t in analysis.get("themes", [])}
    assignments = analysis.get("theme_assignments", {})
    fps = transcript["fps"]
    start_tc = transcript["start_timecode"]

    start_frame = tc.timecode_to_frames(start_tc, fps)

    rows = []
    for seg in analysis["segments"]:
        answer_u = utterances_by_id.get(seg["answer_start_utterance"])
        end_u = utterances_by_id.get(seg["answer_end_utterance"])
        if answer_u is None or end_u is None:
            continue
        sel = selects_by_id.get(seg["id"], {})
        theme_ids = assignments.get(seg["id"], [])
        theme_labels = [themes_by_id[t]["label"] for t in theme_ids if t in themes_by_id]

        # frames_in/out are absolute (source start timecode + elapsed), so
        # they stay consistent with timecode_in/out rather than reading as
        # an offset from zero.
        frames_in = start_frame + tc.seconds_to_frames(answer_u["start"], fps)
        frames_out = start_frame + tc.seconds_to_frames(end_u["end"], fps)

        rows.append({
            "segment": seg,
            "select": sel,
            "speaker": transcript["speakers"].get(answer_u["speaker"], answer_u["speaker"]),
            "timecode_in": tc.frames_to_timecode(frames_in, fps),
            "timecode_out": tc.frames_to_timecode(frames_out, fps),
            "frames_in": frames_in,
            "frames_out": frames_out,
            "theme_labels": theme_labels,
        })
    rows.sort(key=lambda r: r["frames_in"])
    return rows


def write_markdown(transcript: dict, analysis: dict, out_path: Path) -> Path:
    rows = build_rows(transcript, analysis)

    lines = [f"# Interview Notes — {transcript.get('source_file', 'unknown')}", ""]
    strengths = [r["select"].get("strength") for r in rows if r["select"].get("strength") is not None]
    if strengths:
        lines.append(f"*{len(rows)} segments, average strength {sum(strengths) / len(strengths):.2f}*")
    else:
        lines.append(f"*{len(rows)} segments*")
    lines.append("")

    for r in rows:
        seg, sel = r["segment"], r["select"]
        volunteered = seg.get("question_text") is None
        header = seg.get("question_label") or "Untitled"
        lines.append(f"## {r['timecode_in']}–{r['timecode_out']} — {header}")

        tag_bits = [r["speaker"]]
        if volunteered:
            tag_bits.append("volunteered")
        if sel.get("strength") is not None:
            tag_bits.append(f"strength {sel['strength']:.2f}")
        if r["theme_labels"]:
            tag_bits.append(", ".join(r["theme_labels"]))
        lines.append(f"*{' · '.join(tag_bits)}*")
        lines.append("")

        if seg.get("question_text"):
            lines.append(f"**Q:** {seg['question_text']}")
        if seg.get("answer_summary"):
            lines.append(f"**A:** {seg['answer_summary']}")
        if sel.get("best_line"):
            lines.append("")
            lines.append(f"> {sel['best_line']}")
        if sel.get("issues"):
            lines.append("")
            lines.append(f"_Issues: {'; '.join(sel['issues'])}_")
        lines.append("")
        lines.append("---")
        lines.append("")

    out_path.write_text("\n".join(lines))
    logger.info("Wrote %s", out_path)
    return out_path


def write_csv(transcript: dict, analysis: dict, out_path: Path) -> Path:
    rows = build_rows(transcript, analysis)
    rows.sort(key=lambda r: (r["select"].get("strength") is None, -(r["select"].get("strength") or 0)))

    with open(out_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "timecode_in", "timecode_out", "frames_in", "frames_out",
            "speaker", "question_label", "strength", "themes", "best_line",
        ])
        for r in rows:
            seg, sel = r["segment"], r["select"]
            writer.writerow([
                r["timecode_in"], r["timecode_out"], r["frames_in"], r["frames_out"],
                r["speaker"], seg.get("question_label", ""),
                sel.get("strength", ""), "|".join(r["theme_labels"]), sel.get("best_line", ""),
            ])
    logger.info("Wrote %s", out_path)
    return out_path
