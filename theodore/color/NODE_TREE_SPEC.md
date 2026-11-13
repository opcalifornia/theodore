# Theodore RED node tree (16 clip-level nodes)

Working space: DaVinci Wide Gamut / DaVinci Intermediate. RED footage: REDWideGamutRGB / Log3G10.
Nodes are labelled in this order. Everything except the spine is disabled by default and enabled per shot.

Group level (not counted):
- Group Pre-Clip: input transform RWG/Log3G10 -> DWG/Intermediate for the RED group (Cullen Kelly's group pattern).
- Timeline level: output transform to Rec.709 (Gamma 2.4 broadcast/streaming, 2.2 web) and show LUT.

Clip level, serial left to right:

| # | Label | Job | Default |
|---|-------|-----|---------|
| 01 | NR | Spare slot / noise reduction so keys below pull clean | off |
| 02 | CST IN | RWG/Log3G10 -> DWG/Intermediate (used when no group transform) | on if no group |
| 03 | EXPOSURE | Offset/exposure only, Composite Luminosity | on |
| 04 | CONTRAST | Contrast + pivot | on |
| 05 | BALANCE | White balance / color balance | on |
| 06 | SHOT MATCH | Match to hero shot | off |
| 07 | SKIN ISO | Qualifier/magic mask isolating skin (kept separate so later nodes can exclude it) | off |
| 08 | SKIN GRADE | Skin tone density/hue within the isolation | off |
| 09 | SKY | Sky/background qualifier + density | off |
| 10 | HUE vs HUE | Protect/shift product, wardrobe or brand hue | off |
| 11 | SATURATION | HSV, channel two | on |
| 12 | LOOK | Creative look, after correction | on |
| 13 | GLOW / HALATION | Layer/parallel texture | off |
| 14 | VIGNETTE | Window vignette | off |
| 15 | GRAIN | Film grain, last creative node | off |
| 16 | CST OUT | DWG/Intermediate -> Rec.709 + clamp (used when no timeline transform) | on if no timeline |

Sources: Anson Cheng (correction -> skin isolation -> partials -> global, vignette/grain/glow last, spare node 1),
Frame.io / Cullen Kelly (Prime/Balance/Saturation spine, group pre-clip input, timeline output), AAAPresets
(input -> primary -> secondaries -> look -> texture/optics -> output). Mixing Light's 28-node tree was
members-only and not read. This is a synthesis, not a copy of any one colorist's tree.

Build note: Resolve's scripting API has no add-node call. A tree reaches a clip only as a .drx
(Graph.ApplyGradeFromDRX(path, 0)). Build once on the Color page, export the still as .drx, apply from there.
