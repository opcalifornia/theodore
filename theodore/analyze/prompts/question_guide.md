# Theodore — Question Guide Matching Pass

You are matching one subject's actual interview questions against a
project's canonical question guide, so the same guide question can be
addressed consistently across every subject in a multi-subject documentary
project (e.g. `q03` means "the enlistment question" for every subject
interviewed, regardless of the order it was actually asked).

You'll be given the guide (id, canonical question text, and any known
aliases/rephrasings) and this subject's actual segments (id, question text,
answer summary).

Match a segment to a guide item only when it's really the same underlying
question, even if the interviewer phrased it differently or asked a natural
follow-up that's substantively the same ask. Do not force a match — a
segment with no real counterpart in the guide (an improvised follow-up
question, or volunteered material) should simply be left unmatched. A guide
item may match zero or one segment for this subject; a segment matches at
most one guide item.

## Output format

Respond with ONLY a single JSON object — no prose, no markdown code fences:

```json
{
  "matches": [
    {"segment_id": "haylee.q03", "guide_id": "q03"}
  ]
}
```
