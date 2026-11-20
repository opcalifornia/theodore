# Theodore — Theme Tagging Pass

You are given a fixed controlled vocabulary of themes for this interview,
and a set of Q/A segments. Assign each segment 1-3 theme IDs from the
vocabulary below — do not invent new themes here, only choose from the list
given.

## Output format

Respond with ONLY a single JSON object — no prose, no markdown code fences:

```json
{
  "assignments": [
    {"segment_id": "s001", "theme_ids": ["t01", "t04"]}
  ]
}
```
