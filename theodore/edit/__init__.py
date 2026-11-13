"""The editorial engine: decisions an editor makes, executed in Resolve.

  cleanup    fillers, stumbles and dead air out of a talking timeline
  interview  Claude as story editor: transcripts in, a structured cut out
  broll      Claude looks at the B-roll, then lays it over the right lines

Every builder works on a NEW timeline; the editor's timeline is never edited.
"""
import os

#: Story judgement is where quality matters most -- the strongest model.
EDITOR_MODEL = os.environ.get("THEODORE_EDITOR_MODEL", "claude-opus-5-5")
#: Looking at B-roll frames: fast and good at vision.
VISION_MODEL = os.environ.get("THEODORE_VISION_MODEL", "claude-sonnet-5-5")
