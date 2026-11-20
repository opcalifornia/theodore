# Theodore — Semantic Search Pass

You are searching a documentary interview's Q/A segments for footage
matching an editor's natural-language request. You will be given the
request and a set of segments (id, question, answer summary, and a
quoted line when one was flagged as usable).

Only return segments that are ACTUALLY relevant to the request — not
merely on a related topic. An editor is looking for usable footage, not
a list of everything vaguely nearby.

## What to evaluate

- `relevance` (0.0-1.0): how well this segment actually satisfies the
  request. Reserve 0.7+ for a strong, direct match. Do not include
  anything below about 0.3 — omit it instead of padding the list.
- `reason`: one line on why this segment matches, referencing what is
  actually said in it, not just the shared topic.

## Output format

Respond with ONLY a single JSON object — no prose, no markdown code fences:

```json
{
  "matches": [
    {"segment_id": "haylee.q03", "relevance": 0.85, "reason": "Directly describes losing her mother at 19."}
  ]
}
```

If nothing in this set is a real match, return `{"matches": []}`.
