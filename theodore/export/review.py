"""Self-contained review.html: the primary interface for deciding what makes
the cut before anything touches a Resolve timeline.

Shows the proposed assembly in order -- timecode, speaker, question, full
transcript text with trimmed portions struck through, strength, themes,
running total, and (for narrative mode) the structural rationale up top.
Per-segment checkboxes feed a "copy excluded IDs" control that produces the
exact flag string for `theodore assemble --exclude s004,s011,...`, so the
loop is: open review.html, uncheck the bad ones, copy, rebuild.

No external dependencies, no CDN -- everything is inlined so the file works
opened directly from disk.
"""
from __future__ import annotations

import html
import json
import logging
from pathlib import Path
from typing import Optional

from theodore.assembly.trim import get_segment_words, is_word_kept
from theodore.resolve import markers as resolve_markers
from theodore.resolve import timecode as tc

logger = logging.getLogger("theodore.export.review")


def _seconds_to_hhmmss(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _render_segment_html_text(words: list[dict], trim: Optional[dict]) -> str:
    """Full transcript text for a segment's clean word range, with trimmed
    portions wrapped in <s> so the review page shows exactly what would be
    cut without hiding it."""
    if not words:
        return ""
    cuts = (trim or {}).get("cuts", [])

    def gap_cut(prev_word: dict, next_word: dict):
        """A cut interval (e.g. dead air) that falls entirely in the silent
        gap between two words has no word of its own to strike -- surface it
        as an inline marker instead, or a reviewer would never see it."""
        for c in cuts:
            if c[0] >= prev_word["end"] - 1e-6 and c[1] <= next_word["start"] + 1e-6:
                return c[1] - c[0]
        return None

    parts = []
    for i, w in enumerate(words):
        text = html.escape(w["word"])
        parts.append(text if is_word_kept(w, trim) else f"<s>{text}</s>")
        if i + 1 < len(words):
            duration = gap_cut(w, words[i + 1])
            if duration is not None:
                parts.append(f'<span class="cutmark" title="{duration:.1f}s removed">✂ {duration:.1f}s</span>')
    return " ".join(parts)


def build_rows(
    transcript: dict,
    analysis: dict,
    trims: dict,
    segment_order: Optional[list[str]] = None,
) -> list[dict]:
    """One row per segment, in `segment_order` (defaults to chronological --
    the order segments already appear in analysis["segments"])."""
    segments_by_id = {s["id"]: s for s in analysis["segments"]}
    selects_by_id = {s["segment_id"]: s for s in analysis.get("selects", [])}
    themes_by_id = {t["id"]: t for t in analysis.get("themes", [])}
    assignments = analysis.get("theme_assignments", {})
    fps = transcript["fps"]
    start_frame = tc.timecode_to_frames(transcript["start_timecode"], fps)

    order = segment_order or [s["id"] for s in analysis["segments"]]

    rows = []
    for seg_id in order:
        seg = segments_by_id.get(seg_id)
        if seg is None:
            logger.warning("Segment id %s in assembly order not found in analysis, skipping", seg_id)
            continue
        sel = selects_by_id.get(seg_id)
        trim = trims.get(seg_id)

        resolved = get_segment_words(seg, sel, transcript)
        words = resolved[1] if resolved else []
        html_text = _render_segment_html_text(words, trim)

        if trim:
            in_seconds, out_seconds = trim["trimmed_start"], trim["trimmed_end"]
        elif words:
            in_seconds, out_seconds = words[0]["start"], words[-1]["end"]
        else:
            in_seconds = out_seconds = 0.0

        frame_in = start_frame + tc.seconds_to_frames(in_seconds, fps)
        frame_out = start_frame + tc.seconds_to_frames(out_seconds, fps)

        theme_ids = assignments.get(seg_id, [])
        theme_labels = [themes_by_id[t]["label"] for t in theme_ids if t in themes_by_id]
        strength = sel.get("strength") if sel else None
        is_volunteered = seg.get("question_text") is None

        rows.append({
            "segment_id": seg_id,
            "timecode_in": tc.frames_to_timecode(frame_in, fps),
            "timecode_out": tc.frames_to_timecode(frame_out, fps),
            "runtime_seconds": max(0.0, out_seconds - in_seconds),
            "speaker": transcript["speakers"].get((resolved[0] if resolved else None), "Unknown"),
            "question_text": seg.get("question_text"),
            "question_label": seg.get("question_label") or "Untitled",
            "answer_summary": seg.get("answer_summary", ""),
            "is_volunteered": is_volunteered,
            "strength": strength,
            "color": resolve_markers.choose_color(strength, is_volunteered),
            "theme_labels": theme_labels,
            "issues": (sel or {}).get("issues", []),
            "backchannel_overlaps": (trim or {}).get("backchannel_overlaps", []),
            "html_text": html_text,
        })
    return rows


_COLOR_HEX = {
    "Green": "#2e9e5b", "Blue": "#3b7dd8", "Yellow": "#c9a227", "Cyan": "#2ba8ab",
}


def render_html(
    project_name: str,
    rows: list[dict],
    *,
    mode: str = "chronological",
    rationale: Optional[str] = None,
    excluded: Optional[set] = None,
) -> str:
    excluded = excluded or set()
    total_runtime = sum(r["runtime_seconds"] for r in rows)

    rationale_html = ""
    if rationale:
        rationale_html = f"""
        <div class="rationale">
          <div class="rationale-label">Structure rationale ({html.escape(mode)})</div>
          <div class="rationale-text">{html.escape(rationale)}</div>
        </div>"""

    row_html = []
    for r in rows:
        checked = "" if r["segment_id"] in excluded else "checked"
        color = _COLOR_HEX.get(r["color"], "#3b7dd8")
        badges = []
        if r["is_volunteered"]:
            badges.append('<span class="badge badge-volunteered">volunteered</span>')
        if r["strength"] is not None:
            badges.append(f'<span class="badge">strength {r["strength"]:.2f}</span>')
        for theme in r["theme_labels"]:
            badges.append(f'<span class="badge badge-theme">{html.escape(theme)}</span>')

        issues_html = ""
        if r["issues"]:
            issues_html = f'<div class="issues">Issues: {html.escape("; ".join(r["issues"]))}</div>'

        backchannel_html = ""
        if r["backchannel_overlaps"]:
            names = ", ".join(
                f'{html.escape(b["speaker_name"])} ("{html.escape(b["text"])}")' for b in r["backchannel_overlaps"]
            )
            backchannel_html = f'<div class="backchannel">Backchannel overlap: {names}</div>'

        question_html = (
            f'<div class="question">Q: {html.escape(r["question_text"])}</div>'
            if r["question_text"] else '<div class="question question-none">(volunteered, unprompted)</div>'
        )

        row_html.append(f"""
        <div class="segment" style="border-left-color:{color}" data-seg-id="{html.escape(r['segment_id'])}" data-runtime="{r['runtime_seconds']:.3f}">
          <label class="segment-check">
            <input type="checkbox" class="seg-toggle" {checked} onchange="theodoreReview.update()">
            <span class="tc">{r['timecode_in']}–{r['timecode_out']}</span>
          </label>
          <div class="segment-body">
            <div class="segment-header">
              <span class="label">{html.escape(r['question_label'])}</span>
              <span class="speaker">{html.escape(r['speaker'])}</span>
            </div>
            <div class="badges">{''.join(badges)}</div>
            {question_html}
            <div class="transcript-text">{r['html_text']}</div>
            {issues_html}
            {backchannel_html}
          </div>
        </div>""")

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Theodore Review — {html.escape(project_name)}</title>
<style>
  :root {{
    --bg: #f7f6f3; --panel: #ffffff; --text: #1c1b19; --muted: #6b6862;
    --border: #e4e1da; --accent: #3b7dd8; --struck: #b6b2a8;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --bg: #171614; --panel: #211f1c; --text: #ece9e2; --muted: #9a968c;
      --border: #37342e; --accent: #6fa3e8; --struck: #6b675f;
    }}
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; background: var(--bg); color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
    line-height: 1.5;
  }}
  header {{
    position: sticky; top: 0; z-index: 10; background: var(--panel);
    border-bottom: 1px solid var(--border); padding: 16px 24px;
    display: flex; flex-wrap: wrap; align-items: center; gap: 16px;
  }}
  header h1 {{ font-size: 18px; margin: 0; }}
  header .meta {{ color: var(--muted); font-size: 13px; }}
  header .spacer {{ flex: 1; }}
  #runtime {{ font-variant-numeric: tabular-nums; font-size: 14px; }}
  button {{
    background: var(--accent); color: white; border: none; border-radius: 6px;
    padding: 8px 14px; font-size: 13px; cursor: pointer;
  }}
  button:active {{ opacity: 0.8; }}
  #copy-output {{
    width: 100%; margin-top: 8px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 12px; padding: 6px 8px; border-radius: 4px; border: 1px solid var(--border);
    background: var(--bg); color: var(--text);
  }}
  main {{ max-width: 860px; margin: 0 auto; padding: 24px; }}
  .rationale {{
    background: var(--panel); border: 1px solid var(--border); border-radius: 8px;
    padding: 14px 16px; margin-bottom: 20px;
  }}
  .rationale-label {{ font-size: 12px; text-transform: uppercase; letter-spacing: 0.04em; color: var(--muted); margin-bottom: 6px; }}
  .segment {{
    display: flex; gap: 14px; background: var(--panel); border: 1px solid var(--border);
    border-left: 4px solid var(--accent); border-radius: 8px; padding: 14px 16px; margin-bottom: 12px;
  }}
  .segment-check {{ display: flex; flex-direction: column; align-items: center; gap: 4px; cursor: pointer; }}
  .segment-check input {{ width: 16px; height: 16px; }}
  .tc {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 11px; color: var(--muted); white-space: nowrap; }}
  .segment-body {{ flex: 1; min-width: 0; }}
  .segment-header {{ display: flex; justify-content: space-between; align-items: baseline; gap: 8px; }}
  .segment-header .label {{ font-weight: 600; }}
  .segment-header .speaker {{ color: var(--muted); font-size: 13px; }}
  .badges {{ margin: 6px 0; display: flex; flex-wrap: wrap; gap: 6px; }}
  .badge {{
    font-size: 11px; padding: 2px 8px; border-radius: 99px; background: var(--bg);
    border: 1px solid var(--border); color: var(--muted);
  }}
  .badge-volunteered {{ color: #2ba8ab; border-color: #2ba8ab; }}
  .badge-theme {{ color: var(--accent); border-color: var(--accent); }}
  .question {{ font-size: 13px; color: var(--muted); margin: 6px 0 2px; }}
  .question-none {{ font-style: italic; }}
  .transcript-text {{ margin-top: 6px; }}
  .transcript-text s {{ color: var(--struck); text-decoration: line-through; }}
  .cutmark {{
    display: inline-block; font-size: 10px; color: #b8862f; background: var(--bg);
    border: 1px solid var(--border); border-radius: 4px; padding: 0 4px; margin: 0 2px;
    vertical-align: middle;
  }}
  .issues, .backchannel {{ margin-top: 8px; font-size: 12px; color: #b8862f; }}
  .excluded {{ opacity: 0.45; }}
</style>
</head>
<body>
<header>
  <h1>{html.escape(project_name)}</h1>
  <span class="meta">mode: {html.escape(mode)} · {len(rows)} segments</span>
  <div class="spacer"></div>
  <div id="runtime">-- / -- selected</div>
  <button onclick="theodoreReview.copyExcluded()">Copy excluded IDs</button>
</header>
<main>
  {rationale_html}
  <div id="segments">
    {''.join(row_html)}
  </div>
  <input id="copy-output" type="text" readonly value="" onclick="this.select()">
</main>
<script>
  const theodoreReview = {{
    totalRuntime: {total_runtime:.3f},
    totalCount: {len(rows)},
    update() {{
      const segments = document.querySelectorAll('.segment');
      let selectedRuntime = 0, selectedCount = 0;
      segments.forEach(seg => {{
        const box = seg.querySelector('.seg-toggle');
        seg.classList.toggle('excluded', !box.checked);
        if (box.checked) {{
          selectedRuntime += parseFloat(seg.dataset.runtime);
          selectedCount += 1;
        }}
      }});
      const fmt = s => {{
        s = Math.round(s);
        const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
        const pad = n => String(n).padStart(2, '0');
        return h ? `${{h}}:${{pad(m)}}:${{pad(sec)}}` : `${{m}}:${{pad(sec)}}`;
      }};
      document.getElementById('runtime').textContent =
        `${{fmt(selectedRuntime)}} / ${{fmt(this.totalRuntime)}} runtime · ${{selectedCount}}/${{this.totalCount}} selected`;
    }},
    excludedIds() {{
      return Array.from(document.querySelectorAll('.segment'))
        .filter(seg => !seg.querySelector('.seg-toggle').checked)
        .map(seg => seg.dataset.segId);
    }},
    copyExcluded() {{
      const ids = this.excludedIds();
      const flag = ids.length ? `--exclude ${{ids.join(',')}}` : '(none excluded)';
      const output = document.getElementById('copy-output');
      output.value = flag;
      output.select();
      try {{
        navigator.clipboard.writeText(flag);
      }} catch (e) {{
        try {{ document.execCommand('copy'); }} catch (e2) {{ /* manual copy from the field is the fallback */ }}
      }}
    }},
  }};
  theodoreReview.update();
</script>
</body>
</html>"""


def write_review_html(
    project_name: str,
    transcript: dict,
    analysis: dict,
    trims: dict,
    out_path: Path,
    *,
    segment_order: Optional[list[str]] = None,
    mode: str = "chronological",
    rationale: Optional[str] = None,
    excluded: Optional[set] = None,
) -> Path:
    rows = build_rows(transcript, analysis, trims, segment_order=segment_order)
    html_doc = render_html(project_name, rows, mode=mode, rationale=rationale, excluded=excluded)
    out_path.write_text(html_doc)
    logger.info("Wrote %s", out_path)
    return out_path
