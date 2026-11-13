"""Study a finished video and turn it into an edit blueprint.

Given any reference video -- a commercial, a music video, an interview piece --
this package works out how it was cut: every shot and how each one ends (hard
cut, dissolve, fade, dip, flash), the pacing, the music's tempo and whether
cuts land on the beat, what is said, and (with Claude looking at a frame of
every shot) shot sizes, A-roll vs B-roll, and every overlay, title and graphic.
The result is a blueprint the agent can replicate on new footage.

Like theodore.analyze, nothing here imports theodore.resolve: it reads media
files and writes JSON, so it is testable without Resolve and NLE-agnostic.
"""
