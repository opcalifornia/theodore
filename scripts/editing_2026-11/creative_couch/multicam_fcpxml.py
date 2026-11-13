import json, sys
from fractions import Fraction as F
from pathlib import Path
from theodore.edit.panel import Source, PanelPlan, to_fcpxml
S = "/private/tmp/claude-501/-Users-admin-theodore/aa91f910-6ebd-5bc7-80ad-a021f2ecd6b8/scratchpad/mc"
D = "/Volumes/Extreme Pro/Creative Couch/Episode 2/"
RFPS = F(24000, 1001)
red_info = {"fps": RFPS, "width": 8192, "height": 4320, "has_video": True, "has_audio": True, "channels": 4,
            "sample_rate": 48000, "duration": float(46860 / RFPS)}
red = Source(name="RED - LaShonda", path=D + "Camera B(Red w Cooke s4i Mini 100mm)/R057_C042_0101CF.RDC/R057_C042_0101CF_001.R3D",
             kind="camera", info=red_info, start=10.0, tc=F(130611) / RFPS, person="LaShonda", role="close")
ai = lambda n: {"fps": F(24), "width": 3414, "height": 2198, "has_video": True, "has_audio": False, "channels": 0,
                "sample_rate": 48000, "duration": n / 24}
A = D + "Camera A(Arri Alexa w cooke s4i mini 100mm)/"
alexa = Source(name="Alexa - Alisona", path=A + "A036C001_261112_R6L9.mxf", kind="camera", info=ai(42180), person="Alisona", role="close",
               parts=[{"path": A + "A036C001_261112_R6L9.mxf", "start": F(335, 24), "duration": F(42180, 24), "tc": F(10600 * 24 + 3, 24), "info": ai(42180), "name": "A036C001_261112_R6L9.mxf"},
                      {"path": A + "A037C001_261112_R6L9.mxf", "start": F(44587, 24), "duration": F(2412, 24), "tc": F(1686 * 24 + 21, 24), "info": ai(2412), "name": "A037C001_261112_R6L9.mxf"}])
segs = json.load(open(f"{S}/segs.json"))
plan = PanelPlan(cameras=[red, alexa], lavs=[], fps=F(24), length=1960.0, origin=10.0, size=(1920, 1080), attach_lavs=False)
plan.cuts = [(F(a, 24), F(b, 24), cam, why) for a, b, cam, why in segs]
x = to_fcpxml(plan, "Creative Couch Ep2")
Path(f"{S}/cc_ep2_multicam.fcpxml").write_text(x)
print(len(x), x.count("<mc-clip"))
