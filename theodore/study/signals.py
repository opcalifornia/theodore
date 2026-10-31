"""Per-frame video signals and per-hop audio signals, decoded with ffmpeg.

Everything downstream works from these arrays, never from the media itself,
so a two-hour film costs one decode pass and a few hundred MB at most: each
frame is reduced to a 32x18 grey thumbnail, a 48-bin colour histogram and its
mean luma the moment it is read.
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np

#: Camera files (ARRI ProRes 4444 especially) are often tagged with "reserved"
#: colour metadata that ffmpeg's converter refuses. Declare plain BT.709 YUV
#: so the recorded values (log, if log) come through untouched -- the camera
#: transform is applied later, deliberately.
TAG = "setparams=colorspace=bt709:color_primaries=bt709:color_trc=bt709"

THUMB_W, THUMB_H = 32, 18
DECODE_W, DECODE_H = 64, 36
HIST_BINS = 16  # per channel

AUDIO_SR = 22050
HOP = 512  # ~23 ms at 22.05 kHz
N_FFT = 2048


class SignalError(RuntimeError):
    pass


@dataclass
class VideoInfo:
    path: str
    duration: float
    fps: float
    fps_str: str
    width: int
    height: int
    has_audio: bool


def probe(path: Path) -> VideoInfo:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", str(path)],
            capture_output=True, text=True, timeout=120, check=True,
        ).stdout
    except FileNotFoundError as exc:
        raise SignalError("ffprobe not found -- install ffmpeg.") from exc
    except subprocess.CalledProcessError as exc:
        raise SignalError(f"ffprobe could not read {path}: {exc.stderr.strip()[-500:]}") from exc
    data = json.loads(out)
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"
                  and s.get("disposition", {}).get("attached_pic") != 1), None)
    if video is None:
        raise SignalError(f"{path} has no video stream.")
    num, _, den = (video.get("avg_frame_rate") or video.get("r_frame_rate") or "0/1").partition("/")
    fps = float(num) / float(den or 1) if float(den or 1) else 0.0
    if fps <= 0:
        num, _, den = (video.get("r_frame_rate") or "24/1").partition("/")
        fps = float(num) / float(den or 1)
    duration = float(data.get("format", {}).get("duration") or video.get("duration") or 0)
    return VideoInfo(
        path=str(path), duration=duration, fps=fps,
        fps_str=video.get("r_frame_rate") or f"{fps:g}",
        width=int(video.get("width") or 0), height=int(video.get("height") or 0),
        has_audio=any(s.get("codec_type") == "audio" for s in data.get("streams", [])),
    )


@dataclass
class VideoSignals:
    luma: np.ndarray       # (n,) mean luma 0..1
    hist: np.ndarray       # (n, 48) normalised colour histogram
    thumbs: np.ndarray     # (n, 18, 32) uint8 grey
    d_hist: np.ndarray     # (n,) L1/2 histogram distance to previous frame, 0..1
    d_pix: np.ndarray      # (n,) mean abs grey difference to previous frame, 0..1

    @property
    def n(self) -> int:
        return len(self.luma)


def _frame_features(rgb: np.ndarray):
    """rgb: (k, DECODE_H, DECODE_W, 3) uint8 -> luma, hist, thumbs."""
    k = rgb.shape[0]
    grey = (0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2])
    luma = grey.reshape(k, -1).mean(axis=1) / 255.0
    thumbs = grey.reshape(k, THUMB_H, 2, THUMB_W, 2).mean(axis=(2, 4)).astype(np.uint8)
    bins = (rgb.astype(np.uint16) * HIST_BINS // 256).reshape(k, -1, 3)
    hist = np.zeros((k, 3 * HIST_BINS), dtype=np.float32)
    for c in range(3):
        for i in range(k):
            hist[i, c * HIST_BINS:(c + 1) * HIST_BINS] = np.bincount(
                bins[i, :, c], minlength=HIST_BINS)
    hist /= hist.sum(axis=1, keepdims=True) / 3.0 + 1e-9
    return luma.astype(np.float32), hist / 3.0, thumbs


def active_area(path: Path, duration: float, width: int, height: int) -> Optional[dict]:
    """The picture inside any letterbox/pillarbox bars, sampled at several
    points (a title card can be black at the edges without bars). None when
    the picture fills the frame. Bars never change, so leaving them in dilutes
    every cut -- vertical video pillarboxed in 16:9 hid most of its cuts."""
    boxes = []
    for frac in (0.15, 0.4, 0.65, 0.9):
        res = subprocess.run(
            ["ffmpeg", "-hide_banner", "-ss", f"{max(duration * frac, 0):.2f}", "-i", str(path),
             "-map", "0:v:0", "-t", "1.5", "-vf", "cropdetect=limit=24:round=2:reset=0",
             "-f", "null", "-"], capture_output=True, text=True, timeout=300)
        found = re.findall(r"crop=(\d+):(\d+):(\d+):(\d+)", res.stderr)
        if found:
            boxes.append(tuple(int(v) for v in found[-1]))
    if not boxes or not width or not height:
        return None
    # The union of what was seen: never crop away picture that appears somewhere.
    x0 = min(b[2] for b in boxes)
    y0 = min(b[3] for b in boxes)
    x1 = max(b[2] + b[0] for b in boxes)
    y1 = max(b[3] + b[1] for b in boxes)
    w, h = x1 - x0, y1 - y0
    if w <= 0 or h <= 0 or w * h >= 0.92 * width * height:
        return None
    return {"w": w, "h": h, "x": x0, "y": y0}


def video_signals(path: Path, chunk_frames: int = 512, crop: Optional[dict] = None) -> VideoSignals:
    vf = f"{TAG},scale={DECODE_W}:{DECODE_H}:flags=area,format=rgb24"
    if crop:
        vf = f"crop={crop['w']}:{crop['h']}:{crop['x']}:{crop['y']}," + vf
    cmd = ["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:v:0",
           "-vf", vf, "-f", "rawvideo", "-"]
    frame_bytes = DECODE_W * DECODE_H * 3
    lumas, hists, thumbs = [], [], []
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        while True:
            buf = proc.stdout.read(frame_bytes * chunk_frames)
            if not buf:
                break
            k = len(buf) // frame_bytes
            if k == 0:
                break
            rgb = np.frombuffer(buf[:k * frame_bytes], dtype=np.uint8).reshape(k, DECODE_H, DECODE_W, 3)
            l, h, t = _frame_features(rgb)
            lumas.append(l)
            hists.append(h)
            thumbs.append(t)
    finally:
        proc.stdout.close()
        err = proc.stderr.read().decode(errors="replace")
        proc.wait()
    if proc.returncode != 0 and not lumas:
        raise SignalError(f"ffmpeg could not decode {path}: {err.strip()[-500:]}")
    if not lumas:
        raise SignalError(f"No frames decoded from {path}.")
    luma = np.concatenate(lumas)
    hist = np.concatenate(hists)
    th = np.concatenate(thumbs)
    d_hist = np.zeros(len(luma), dtype=np.float32)
    d_pix = np.zeros(len(luma), dtype=np.float32)
    d_hist[1:] = 0.5 * np.abs(hist[1:] - hist[:-1]).sum(axis=1)
    d_pix[1:] = np.abs(th[1:].astype(np.int16) - th[:-1].astype(np.int16)).mean(axis=(1, 2)) / 255.0
    return VideoSignals(luma=luma, hist=hist, thumbs=th, d_hist=d_hist, d_pix=d_pix)


# ---------------------------------------------------------------------------
# audio
# ---------------------------------------------------------------------------

def audio_samples(path: Path) -> np.ndarray:
    cmd = ["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a:0", "-ac", "1",
           "-ar", str(AUDIO_SR), "-f", "f32le", "-"]
    res = subprocess.run(cmd, capture_output=True, timeout=1800)
    if res.returncode != 0:
        raise SignalError(f"ffmpeg could not decode audio: {res.stderr.decode(errors='replace')[-500:]}")
    return np.frombuffer(res.stdout, dtype=np.float32)


def onset_envelope(y: np.ndarray) -> np.ndarray:
    """Log-magnitude spectral flux, one value per HOP samples."""
    if len(y) < N_FFT:
        return np.zeros(1, dtype=np.float32)
    n_frames = 1 + (len(y) - N_FFT) // HOP
    window = np.hanning(N_FFT).astype(np.float32)
    env = np.zeros(n_frames, dtype=np.float32)
    prev = None
    for start in range(0, n_frames, 2048):
        idx = np.arange(start, min(start + 2048, n_frames))
        frames = np.stack([y[i * HOP:i * HOP + N_FFT] for i in idx]) * window
        mag = np.log1p(100.0 * np.abs(np.fft.rfft(frames, axis=1)))
        if prev is not None:
            mag_prev = np.vstack([prev[None, :], mag[:-1]])
        else:
            mag_prev = np.vstack([mag[:1], mag[:-1]])
        env[idx] = np.maximum(mag - mag_prev, 0).mean(axis=1)
        prev = mag[-1]
    env -= np.convolve(env, np.ones(16) / 16, mode="same")  # remove slow trend
    return np.maximum(env, 0)


def rms_envelope(y: np.ndarray) -> np.ndarray:
    n = len(y) // HOP
    if n == 0:
        return np.zeros(1, dtype=np.float32)
    return np.sqrt((y[:n * HOP].reshape(n, HOP) ** 2).mean(axis=1))


def integrated_loudness(path: Path) -> dict:
    """EBU R128 integrated loudness and true peak, as delivery specs quote them."""
    res = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-map", "0:a:0",
         "-af", "ebur128=peak=true", "-f", "null", "-"],
        capture_output=True, text=True, timeout=1800,
    )
    summary = res.stderr[res.stderr.rfind("Summary:"):] if "Summary:" in res.stderr else ""
    out = {}
    for key, label in (("integrated_lufs", "I:"), ("loudness_range_lu", "LRA:"), ("true_peak_dbtp", "Peak:")):
        pos = summary.find(label)
        if pos >= 0:
            try:
                out[key] = float(summary[pos + len(label):].split()[0])
            except (ValueError, IndexError):
                pass
    return out
