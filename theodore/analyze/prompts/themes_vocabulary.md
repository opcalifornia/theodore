# Theodore — Theme Vocabulary Pass

You are building a controlled vocabulary of themes for a documentary
interview, based on its full set of Q/A segments (question + answer
summaries only, not the full transcript).

Produce a SMALL, non-redundant set of themes — typically 5-15 for a single
interview. Two segments about closely related material should share a theme
rather than getting near-duplicate themes; this vocabulary will later be
used to cluster segments *across multiple interviews* in the same project,
so themes should be phrased at a level general enough to apply beyond this
one subject's specific wording, while still being meaningful (prefer
"career turning points" over both "the day everything changed" and "quitting
my job").

## Output format

Respond with ONLY a single JSON object — no prose, no markdown code fences:

```json
{
  "themes": [
    {"id": "t01", "label": "Short theme name", "description": "One sentence describing what belongs under this theme"}
  ]
}
```
