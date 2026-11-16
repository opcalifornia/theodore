"""The Mr.H Method: turn raw event audio into a recap built from value lines only.

corpus    transcribe every lav recording once (cached) into timed sentences
tagger    separate intro/resume/logistics from value statements and score them
selection the picked-lines file (spine order), the Gate 1 report, validation
check     Gate 2: transcribe the built result and check it against the spine
build     place the picked lines on a NEW Resolve timeline (butt joins, music plan)
"""
