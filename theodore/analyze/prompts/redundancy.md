# Theodore — Redundancy Detection Pass

You are reviewing a set of interview segments (question + answer summaries)
that were pooled together because they share a theme, to find where the
subject told essentially the same story or gave essentially the same
answer more than once. This happens constantly in real interviews: the
subject circles back to a good story unprompted, or gets asked a similar
question two different ways and gives the same answer both times. Finding
these by re-watching every take is slow — that is what this pass is for.

Flag TRUE redundancy only: segments so alike in substance that an editor
would only ever use one of them, not segments that merely share a topic.

Do not flag:
- Segments that share a subject but add different information (one covers
  the timeline, another covers how it felt — keep both).
- A brief callback or reference to something covered earlier that isn't
  itself a full retelling.
- Segments that are similar only because the theme grouping is broad —
  read the actual question and answer, not just the shared theme.

## Output format

Respond with ONLY a single JSON object — no prose, no markdown code fences:

```json
{
  "groups": [
    {
      "segment_ids": ["haylee.q03", "haylee.q11"],
      "reason": "One line on what makes these the same underlying answer"
    }
  ]
}
```

Only include entries where at least two segment_ids are genuinely
redundant with each other. A group may have more than two members if
several segments all cover the same ground. If nothing in this set is
redundant, return `{"groups": []}`.
