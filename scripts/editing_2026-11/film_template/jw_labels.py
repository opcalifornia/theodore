"""Shot labels for the John Wick home-invasion study, written from the START and END stills of each shot (plus 1-second
samples inside the long ones). Movement is inferred from how the start and end frames differ, so it is a best read, never
more. A field left as None is shown on screen as UNVERIFIED. Sound is never labelled: no audio was available.
Keys are the shot numbers of the first review pass (old numbering); labels_for() maps the final shot list onto them."""
from __future__ import annotations

# size, angle, movement, lens feel, lighting, action
L = {
 1: ("Medium-wide group", "Eye level", "Static or slow drift", "Normal-long, compressed", "Very low-key teal; figures in silhouette", "Hooded figures close in on a figure at the stairs"),
 2: ("Extreme close-up (face)", "Eye level", "Slow reveal / move-in", "Long, shallow focus", "Low-key; one soft side source finds the face", "A face emerges from darkness and turns into the light"),
 3: ("Wide", "Eye level", None, "Normal", "Low-key; muzzle flash lights a gunman", "Gunman on the landing fires as two figures pass in the foreground"),
 4: ("Medium-wide", "Eye level", "Handheld shift", "Normal", "Low-key with flash", "Figures wrestle at the landing"),
 5: ("Medium", "Eye level", "Handheld, follows the action", "Normal", "Low-key; orange muzzle flash", "Close-quarters shoot-out; John fires, a body lies at frame edge"),
 6: ("Medium, then wide on the stairs", "Eye level to low", "Whip or tilt from John to the staircase", "Normal-wide", "Low-key", "John fires, the camera swings to the staircase"),
 7: ("Wide", "Low", "Camera drifts across the stairwell", "Wide", "Low-key; hard practical lamp", "Figure moves through the stairwell (detail UNVERIFIED)"),
 8: ("Wide to medium close", "Low", "Move from the lamp toward a foreground figure", "Wide", "Low-key; blue-white lamp", "A figure watches from the foreground"),
 9: ("Medium", "Eye level, slightly low", "Push-in", "Normal", "Low-key; gun flash", "John advances and fires"),
 10: ("Medium close-up", "Eye level", "Slight push", "Longer, shallow", "Low-key cool", "John aims his pistol"),
 11: ("Wide", None, None, "Wide; foreground blurred", "Low-key; flashes of light", "A figure creeps through a dark room"),
 12: ("Wide", None, None, "Wide; foreground blurred", "Low-key; dark room", "Dark room, a figure and clothing on the floor (detail UNVERIFIED)"),
 13: ("Medium close-up then wide", "Eye level", "Camera reframes from John to the target", "Normal", "Low-key cool", "John aims, then the camera finds an armed figure by the wall"),
 14: ("Wide", "Eye level", "Follows John through the hallway to the stairs", "Normal", "Low-key cool", "Long take: John aims down the hall, passes the stairs, a figure's shadow on the wall (may hold more than one beat)"),
 15: ("Wide", "Low", "Handheld follow up the stairs", "Wide", "Low-key; hard stripes of light", "A man runs up the staircase in a chase or fight"),
 16: ("Wide to medium", "Eye level", "Handheld", "Normal", "Low-key teal; window light", "A figure falls; John appears at the stairs (detail UNVERIFIED)"),
 17: ("Medium close-up", "Low (looking up)", "Tilt up to John", "Wide", "Low-key; warm gun flash", "John fires upward from the stairwell"),
 18: ("Medium close-up", "Low", "Near static", "Longer, shallow", "Low-key cool", "John aims and waits"),
 19: ("Medium", "Slightly low", "Locked off; the actor moves", "Normal", "Low-key; hard striped side light", "John crouches, then looks up and listens"),
 20: ("Medium-wide", "Eye level", None, "Normal", "Practical flashlight beams in the dark", "An intruder sweeps the room with weapon lights"),
 21: ("Medium-wide", "Eye level", "Slight drift", "Normal", "Practical weapon lights", "The same sweep continues"),
 22: ("Medium-wide to wide", "Eye level", "Drifts or pans with the intruder", "Normal", "Low-key; practical lamp", "An armed intruder moves through the room"),
 23: ("Medium close-up", "Eye level", "Static", "Normal", "Low-key; soft side light", "John waits, looks down"),
 24: ("Close-up", "Eye level", "Handheld", "Longer, shallow", "Low-key", "Close action (detail UNVERIFIED)"),
 25: ("Wide", "Eye level", "Handheld follow", "Normal", "Low-key blue", "Figures run behind the shelving"),
 26: ("Medium-wide", "Eye level", "Static", "Normal", "Low-key; muzzle flash", "Gun exchange behind wooden shelving"),
 27: ("Medium", "Eye level", "Handheld", "Normal", "Low-key", "A figure in a coat moves with a gun"),
 28: ("Medium to wide", "Eye level", "Handheld", "Normal", "Low-key; teal exterior light", "A struggle at the doorway"),
 29: ("Medium close-up", "Eye level", "Handheld", "Normal", "Low-key", "John and an attacker brawl"),
 30: ("Medium", "Eye level", "Static", "Normal", "Low-key", "A man is thrown toward the glass"),
 31: ("Medium close-up", "Eye level", "Handheld", "Normal", "Low-key", "A fight breaks through the window (detail UNVERIFIED)"),
 32: ("Medium close-up", "Eye level", "Push-in", "Normal", "Low-key; large warm muzzle flash", "John fires into the camera side of the frame"),
 33: ("Medium close-up", "Eye level", "Near static", "Normal", "Low-key", "John aims his pistol"),
 34: ("Medium to extreme close-up", "Eye level", "Handheld whip toward the foreground", "Normal", "Low-key", "John turns on an attacker; a face fills the frame"),
 35: ("Wide", "Eye level", "Locked off, then John enters", "Normal", "Cool backlight through the window", "John enters and goes at someone near the window"),
 36: ("Medium to wide", "Eye level", "Handheld", "Normal", "Mixed warm and cool", "A fight at the window"),
 37: ("Medium over-the-shoulder to close-up", "Eye level", "Handheld", "Normal", "Low-key", "A takedown; John's hair and face swing into view"),
 38: ("Wide", "Eye level", "Static", "Normal", "Low-key", "A man on the kitchen counter as John lunges"),
 39: ("Wide", "Eye level", "Static", "Normal", "Backlit trees; low-key room", "Two men fight in front of the window"),
 40: ("Medium", "Eye level", "Handheld", "Normal", "Low-key", "John in the middle of a kitchen brawl"),
 41: ("Medium to low floor view", "Eye level to low", "Camera drops with the fall", "Normal", "Low-key", "Bodies fall to the floor"),
 42: ("Medium", "Eye level", "Handheld", "Normal", "Low-key; window light", "A blurred figure falls across the frame"),
 43: ("Wide", "Eye level", "Handheld", "Normal", "Low-key", "John fights on and over the counter"),
 44: ("Wide", "Eye level", "Static", "Normal", "Low-key", "A man on the counter, John standing"),
 45: ("Close-up", "Eye level", "Handheld", "Normal", "Low-key", "John's face turns; a masked attacker at the right"),
 46: ("Wide", "Eye level", "Static", "Normal", "Low-key", "The kitchen fight continues"),
 47: ("Medium then wall detail", "Eye level", "Pan or tilt after the fall", "Normal", "Low-key", "A hit sends a body down; the camera lands on framed pictures"),
 48: ("Wide, ending on a close-up", "Eye level to low", "Long take; move from the wide hallway to John's face (UNVERIFIED whether one take)", "Wide to long", "Low-key; cool window light", "A figure comes down the hallway; a struggle on the floor; John's face fills the frame"),
 49: ("Wide to medium", "Eye level", "Static or slow", "Normal", "Very low-key; red police light through windows", "John at the door; a silhouette at the red window; a police officer arrives"),
 50: ("Medium", "Eye level", "Static", "Normal", "Low-key; flickering red and blue police lights", "John and the officer talk at the door"),
 51: ("Medium close-up", "Eye level", "Static", "Longer", "Low-key; red and blue flicker on John's face", "John listens, then answers the officer"),
}
# the police-at-the-door section was re-cut by frame (final numbering), so it is labelled by frame range:
DOOR = [
 (4254, 4304, ("Wide to close-up", "Eye level", "Static", "Normal-long", "Low-key; red-blue light at the door", "John comes to the red door; the camera is on his face at the end")),
 (4305, 4354, ("Close-up", "Eye level", "Static", "Long, shallow", "Low-key; cool light, red car light behind", "John, face turned to the officer outside")),
 (4355, 4417, ("Medium over-the-shoulder", "Eye level", "Static", "Long", "Blue-lit officer, red car lights", "The officer, over John's shoulder")),
 (4418, 4498, ("Medium over-the-shoulder", "Eye level", "Static", "Long", "Dark; red light flicker", "John at the window while the officer waits")),
 (4499, 4696, ("Medium over-the-shoulder", "Eye level", "Static", "Long", "Red and blue police lights on the officer", "The officer speaks; John listens")),
 (4697, 4779, ("Close-up", "Eye level", "Static", "Long, shallow", "Low-key; red-lit face", "John's face at the window")),
 (4780, 4843, ("Medium close-up (profile)", "Eye level", "Static", "Normal", "Dark; red car light across the glass", "John in profile at the window")),
]
SPLITS = {  # shots that were split from an older one by the second detection pass
 782: ("Medium", "Low", "Handheld", "Wide", "Low-key; hard light", "A man tumbles against the bookshelf"),
 1764: ("Medium", "Eye level", "Handheld", "Normal", "Low-key; green trees through glass", "A figure hits the glass; it cracks"),
}


def labels_for(shots: list[dict], old: list[dict]) -> dict:
    out = {}
    for s in shots:
        row = None
        for a, b, v in DOOR:
            if a <= s["f_in"] <= b:
                row = v
        if row is None:
            for f, v in SPLITS.items():
                if abs(s["f_in"] - f) <= 3:
                    row = v
        if row is None:
            o = max((x for x in old if x["f_in"] <= s["f_in"] + 3), key=lambda x: x["f_in"])
            row = L.get(o["n"])
        if row is None:
            continue
        size, angle, move, lens, light, action = row
        d = dict(size=size, angle=angle, movement=move, lens=lens, light=light, action=action)
        out[s["id"]] = {k: v for k, v in d.items() if v}
        out[s["id"]]["verified"] = False
    return out
