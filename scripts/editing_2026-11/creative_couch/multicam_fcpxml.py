import json, xml.etree.ElementTree as ET
from fractions import Fraction as F
from pathlib import Path
from theodore.edit.panel import Source, PanelPlan, to_fcpxml
S = "/private/tmp/claude-501/-Users-admin-theodore/aa91f910-6ebd-5bc7-80ad-a021f2ecd6b8/scratchpad/mc"
D = "/Volumes/Extreme Pro/Creative Couch/Episode 2/"
R = F(24000, 1001)
red_info = {"fps": R, "width": 8192, "height": 4320, "has_video": True, "has_audio": True, "channels": 4, "sample_rate": 48000, "duration": float(46860 / R)}
red = Source(name="RED - LaShonda", path=D + "Camera B(Red w Cooke s4i Mini 100mm)/R057_C042_0101CF.RDC/R057_C042_0101CF_001.R3D",
             kind="camera", info=red_info, start=10.0, tc=F(130611) / R, person="LaShonda", role="close")
# The Alexa sensor ran at 23.976 (Camera FPS) though the files say 24: play it in real time.
ai = lambda n: {"fps": R, "width": 3414, "height": 2198, "has_video": True, "has_audio": False, "channels": 0, "sample_rate": 48000, "duration": n / R}
A = D + "Camera A(Arri Alexa w cooke s4i mini 100mm)/"
alexa = Source(name="Alexa - Alisona", path=A + "A036C001_261112_R6L9.mxf", kind="camera", info=ai(42180), person="Alisona", role="close",
               parts=[{"path": A + "A036C001_261112_R6L9.mxf", "start": F(335, 24), "duration": 42180 / R, "tc": (10600 * 24 + 3) / R, "info": ai(42180), "name": "A036C001_261112_R6L9.mxf"},
                      {"path": A + "A037C001_261112_R6L9.mxf", "start": F(44587, 24), "duration": 2412 / R, "tc": (1686 * 24 + 21) / R, "info": ai(2412), "name": "A037C001_261112_R6L9.mxf"}])
segs = json.load(open(f"{S}/segs.json"))
plan = PanelPlan(cameras=[red, alexa], lavs=[], fps=F(24), length=1960.0, origin=10.0, size=(1920, 1080), attach_lavs=False)
plan.cuts = [(F(a, 24), F(b, 24), cam, why) for a, b, cam, why in segs]
x = to_fcpxml(plan, "Creative Couch Ep2")
assert "timeMap" not in x
# Lavs inside the multicam as audio angles, on the same clock (origin = RED start = synced frame 86640).
root = ET.fromstring(x.split("\n", 2)[2])
res = root.find("resources"); mc = root.find(".//multicam")
ts = lambda v: f"{F(v).numerator}/{F(v).denominator}s"
lavs = [("LaShonda lav", D + "Lav Mic #1/DJI_07_20261112_111627.WAV", 88604661, F(240 + 9805, 24), D + "Lav Mic #1/DJI_07_20261112_114713.WAV", 25917339, F(34498 - 240, 24)),
        ("Alisona lav", D + "Lav Mic #2/DJI_06_20261112_111627.WAV", 88604661, F(240 + 9793, 24), D + "Lav Mic #2/DJI_06_20261112_114715.WAV", 25893579, F(34509 - 240, 24))]
n = 100
for k, (name, f1, s1, in1, f2, s2, off2) in enumerate(lavs, 3):
    angle = ET.SubElement(mc, "mc-angle", {"name": name, "angleID": f"angle{k}"})
    for path, samples, start, offset, dur in ((f1, s1, in1, F(0), off2), (f2, s2, F(0), off2, F(int(F(s2, 48000) * 24), 24))):
        n += 1
        a = ET.SubElement(res, "asset", {"id": f"a{n}", "name": Path(path).name, "start": "0s", "duration": ts(F(samples, 48000)),
                                         "hasAudio": "1", "audioSources": "1", "audioChannels": "1", "audioRate": "48000"})
        ET.SubElement(a, "media-rep", {"kind": "original-media", "src": Path(path).as_uri()})
        ET.SubElement(angle, "asset-clip", {"ref": f"a{n}", "offset": ts(offset), "name": name, "start": ts(start), "duration": ts(dur), "tcFormat": "NDF"})
out = '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n' + ET.tostring(root, encoding="unicode")
Path(f"{S}/cc_ep2_multicam_v3.fcpxml").write_text(out); print(len(out), out.count("<mc-angle"), out.count("<mc-clip"))
