# Theodore — Segment ID Matching Pass

You are comparing two lists of interview Q/A segments for the SAME subject
from two different analysis runs: "existing segments" (already have a
permanent id from a prior run) and "new segments" (just produced by a fresh
segmenter run, not yet assigned an id).

Your job: decide which new segments are the SAME question/answer moment as
an existing segment (the segmenter re-detected it, possibly with slightly
different utterance boundaries or a reworded summary), versus genuinely new
material that wasn't there before.

A match should have the same underlying question and the same substance of
the answer, even if wording or exact boundaries drifted slightly between
runs. Do not match segments that cover different topics just because they're
near each other in the transcript. Every existing segment matches at most
one new segment, and vice versa.

## Output format

Respond with ONLY a single JSON object — no prose, no markdown code fences:

```json
{
  "matches": [
    {"new_index": 2, "existing_id": "haylee.q03"}
  ]
}
```

Only include genuine matches. Leave any new segment with no real match out
of the list entirely — it will correctly be treated as new material.
