"""Angle-log overlay: changes only when the ANGLE changes (size + camera height), not on every cut or movement.
Shows the angle ID, whether it is new or a return, the angle type, lens/light/movement, a generic "replicate it" tip,
and a live grid of every angle type with how many times each has been used so far, so students can see the scene's
angle diversity and the gaps. Same Full Frame look as the other templates."""
from __future__ import annotations

from PIL import Image, ImageDraw, ImageFilter

import design_fullframe as F
import design_overlay as O
from design_fullframe import W, H, S, DIM, INK, ave, din, label, mono, _xy, spaced, tc
from jw_angles import SIZE_NAME

SIZES = ["XCU", "CU", "MCU", "MS", "MWS", "WS", "OTS"]
HEIGHTS = ["HIGH", "EYE", "LOW"]


def wrap(d, text, font, max_w):
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if d.textlength(t, font=font) / S <= max_w:
            cur = t
        else:
            lines.append(cur); cur = w
    return lines + [cur] if cur else lines


def render(b: dict, blocks: list[dict], meta: dict, path: str, max_frames: int) -> None:
    a = F.ACCENT["dialogue" if b["f_in"] >= 4254 else "action"]
    img = O._shade()
    d = ImageDraw.Draw(img)
    x0 = 80
    lab = b["label"]
    label(d, (x0, 58), "ANGLE LOG")
    d.text(_xy(x0, 78), meta["production"], font=ave(24), fill=INK + (255,))
    label(d, (W - 80, 58), f"ANGLE {b['n']} / {len(blocks)}   ·   {b['shots'][0][-3:]}" + (f"–{b['shots'][-1][-3:]}" if len(b["shots"]) > 1 else ""), (255, 255, 255), 14, "ra")

    glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(glow).text(_xy(x0 - 4, 118), b["id"], font=din(200), fill=tuple(a) + (160,))
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(20 * S)))
    d = ImageDraw.Draw(img)
    d.text(_xy(x0 - 4, 118), b["id"], font=din(200), fill=INK + (255,))
    idw = d.textlength(b["id"], font=din(200)) / S
    if b["before"] == 0:
        txt, col = "NEW ANGLE", (80, 255, 160)
    else:
        n = b["before"] + 1
        txt, col = f"BACK TO THIS ANGLE  ·  {n}{'ND' if n == 2 else 'RD' if n == 3 else 'TH'} TIME", (255, 200, 90)
    f = din(30)
    w = d.textlength(txt, font=f) / S + 28
    d.rounded_rectangle(_xy(x0 + idw + 24, 160, x0 + idw + 24 + w, 202), 5 * S, fill=col + (255,))
    d.text(_xy(x0 + idw + 38, 166), txt, font=f, fill=(10, 12, 14, 255))

    d.rectangle(_xy(x0, 350, x0 + 5, 396), fill=tuple(a) + (255,))
    d.text(_xy(x0 + 20, 346), b["type_name"], font=fit_font(d, b["type_name"], 560), fill=tuple(a) + (255,))

    y = 420
    for k, v in (("LENS FEEL", lab.get("lens")), ("LIGHTING", lab.get("light")), ("MOVEMENT (FIRST SHOT)", lab.get("movement"))):
        label(d, (x0, y), k, DIM, 13)
        txt = v if v else "UNVERIFIED"
        d.text(_xy(x0, y + 18), txt, font=O.fit(d, txt, ave, 23, 560, "medium"), fill=(INK if v else (255, 190, 90)) + (255,))
        d.line(_xy(x0, y + 52, x0 + 540, y + 52), fill=(255, 255, 255, 40), width=S)
        y += 64
    label(d, (x0, y + 4), "REPLICATE IT", a, 13)
    f = ave(20, "regular")
    for i, line in enumerate(wrap(d, b["tip"], f, 540)[:4]):
        d.text(_xy(x0, y + 26 + i * 27), line, font=f, fill=(235, 235, 235, 255))

    # angle-type grid: times each type has been used so far in the scene
    gx, gy, cw, ch = 1470, 676, 112, 30
    d.rounded_rectangle(_xy(gx - 70, gy - 52, gx + 3 * cw + 24, gy + 7 * ch + 54), 8 * S, fill=(0, 0, 0, 140))
    label(d, (gx - 54, gy - 38), "ANGLE TYPES USED SO FAR", DIM, 12)
    for j, h in enumerate(HEIGHTS):
        label(d, (gx + j * cw + cw / 2 - 8, gy - 14), h, DIM, 11, "ma")
    cum = {}
    for x in blocks[: b["n"]]:
        cum[x["key"]] = cum.get(x["key"], 0) + 1
    total = {}
    for x in blocks: total[x["key"]] = total.get(x["key"], 0) + 1
    for i, sz in enumerate(SIZES):
        yy = gy + 6 + i * ch
        label(d, (gx - 54, yy + 8), sz, DIM, 12)
        for j, h in enumerate(HEIGHTS):
            key, xx = f"{sz}-{h}", gx + j * cw
            n = cum.get(key, 0)
            if key == b["key"]:
                d.rounded_rectangle(_xy(xx, yy, xx + cw - 8, yy + ch - 5), 4 * S, fill=tuple(a) + (255,))
                d.text(_xy(xx + (cw - 8) / 2, yy + (ch - 5) / 2 + 1), str(n), font=din(24), fill=(10, 12, 14, 255), anchor="mm")
            elif n:
                d.rounded_rectangle(_xy(xx, yy, xx + cw - 8, yy + ch - 5), 4 * S, fill=(255, 255, 255, 60))
                d.text(_xy(xx + (cw - 8) / 2, yy + (ch - 5) / 2 + 1), str(n), font=din(22), fill=(255, 255, 255, 230), anchor="mm")
            else:
                d.rounded_rectangle(_xy(xx, yy, xx + cw - 8, yy + ch - 5), 4 * S, outline=(255, 255, 255, 40), width=S)
                d.text(_xy(xx + (cw - 8) / 2, yy + (ch - 5) / 2), "—" if total.get(key, 0) == 0 else "·", font=din(18), fill=(255, 255, 255, 70), anchor="mm")
    label(d, (gx - 54, gy + 7 * ch + 22), f"SEEN {b['unique_so_far']} / {len({x['key'] for x in blocks})}   ·   — NOT USED IN SCENE", DIM, 11)

    by = 905
    d.rectangle(_xy(x0, by, x0 + 5, by + 112), fill=tuple(a) + (255,))
    label(d, (x0 + 22, by), f"SHOT{'S' if len(b['shots']) > 1 else ''} {b['shots'][0][-3:]}" + (f"–{b['shots'][-1][-3:]}" if len(b["shots"]) > 1 else "") + f"   ·   IN {tc(b['f_in'])}  OUT {tc(b['f_out'])}", a, 14)
    txt = lab.get("action") or "action UNVERIFIED"
    d.text(_xy(x0 + 22, by + 22), txt, font=O.fit(d, txt, ave, 42, 1200, "demi"), fill=INK + (255,))
    label(d, (x0 + 22, by + 88), f"{b['frames']} FRAMES  ·  {b['frames'] * 1001 / 24000:.2f} S ON THIS ANGLE", INK, 15)
    bx0, bx1, byy = x0 + 600, 1200, by + 98
    d.line(_xy(bx0, byy, bx1, byy), fill=(255, 255, 255, 70), width=2 * S)
    d.line(_xy(bx0, byy, bx0 + (bx1 - bx0) * min(1.0, b["frames"] / max_frames), byy), fill=tuple(a) + (255,), width=4 * S)
    label(d, (1840, 1028), "ANGLE FROM START + END FRAMES  ·  BEST READ  ·  SOUND NOT VERIFIED", DIM, 11, "ra")
    img.resize((W, H), Image.LANCZOS).save(path)


def fit_font(d, text, max_w):
    size = 34
    while size > 16 and d.textlength(text, font=ave(size, "demi")) / S > max_w:
        size -= 2
    return ave(size, "demi")
