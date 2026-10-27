"""Panda video engine: writes H3 prompts, estimates cost, renders on the owner's Modal app, keeps a gallery.

Two ways in, one queue:
  * the Video tab -> worker.py commands  video.prompt / video.estimate / video.submit / video.cancel /
    video.delete / video.budget
  * the chat agent -> the `pvideo` command (bottom of this file). It drops a request in a spool folder
    that the worker's engine picks up, so chat jobs show up in the Video tab too.

Rendering is the deployed Modal app `panda-h3` (repo: modal/h3_app.py): class H3 on one RTX PRO 6000,
max_containers=1, so every job from every chat runs one after the other on one GPU.

State lives in Storage, never only on this machine (Kaggle wipes it):
  panda-files/<owner>/video/jobs.json   every job: settings, prompt, status, progress, cost
  panda-files/<owner>/video/<id>.mp4    the clip (+ <id>.jpg poster); also a `files` row
  ~/.panda/video/stats.json             what the estimate learned (copied into jobs.json too)
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

HOME = os.path.expanduser("~")
STATE_DIR = os.path.join(HOME, ".panda", "video")
SPOOL = os.path.join(STATE_DIR, "spool")      # pvideo requests waiting for the engine
STATUS = os.path.join(STATE_DIR, "status")    # one json per job, for pvideo --wait
STATS_FILE = os.path.join(STATE_DIR, "stats.json")
SECRETS_ENV = os.path.join(HOME, ".panda", "secrets.env")
OUTPUTS = os.path.join(HOME, "outputs")

APP_NAME, CLS_NAME, PROGRESS_DICT = "panda-h3", "H3", "panda-h3-progress"
VIDEO_APPS = ("panda-h3", "h3-")  # Modal apps that made videos (h3-batch / h3-video were the first tests)
FILES_BUCKET = "panda-files"

# Modal prices (modal.com/pricing): RTX PRO 6000 $3.03/h, CPU $0.0000131 per core-second,
# memory $0.00000222 per GiB-second. The renderer asks for 4 cores and 48 GiB.
GPU_USD_S = 3.03 / 3600
USD_PER_SEC = GPU_USD_S + 4 * 0.0000131 + 48 * 0.00000222   # ~= $0.001 a second, $3.60 an hour
MODAL_CREDIT = 30.0      # Modal Starter: $30 of free compute every month (resets on the 1st, UTC)
DEFAULT_BUDGET = MODAL_CREDIT   # stop line for ALL Modal use this month, read from Modal's own billing
USAGE_TTL = 600          # re-read Modal's billing every 10 minutes (it lags by up to an hour anyway)
FPS = 24

# Sizes H3 was trained around; drafts stay above 0.4 MP so faces don't go soft when upscaled.
SIZES = {"final": {"16:9": (1344, 768), "9:16": (768, 1344), "1:1": (1024, 1024)},
         "draft": {"16:9": (896, 512), "9:16": (512, 896), "1:1": (640, 640)}}
QUALITY = {"draft": {"steps": 8, "loras": [["turbo8", 0.8]]},   # turbo LoRA: ~2.4x faster, close look
           "final": {"steps": 20, "loras": []}}
ORBIT = {"w": 768, "h": 768, "secs": 3, "steps": 28, "loras": [["orbit", 1.0]]}
MODES = ("clip", "frames", "orbit")       # text/start image, start+end frame, 360° orbit of one photo

# The estimate starts from measured numbers and then learns from every finished job.
#   per_step = a * u**b, u = width*height*frames / 1e8. Measured on the RTX PRO 6000 (26-27 Oct):
#   1344x768 6.5 s clip 13 s/step (fisherman, 20 steps, 317 s with the load), 768x1344 5 s turbo
#   draft 74 s for 8 steps (samurai), 768x768 orbit 78 s for 28 steps. a=7.28, b=1.2 fits all three.
DEFAULT_STATS = {"a": 7.28, "b": 1.2, "n": 0,
                 "stage": {"reading prompt": 6.0, "decoding": 14.0, "saving": 3.0, "loading": 20.0},
                 "cold_up": 45.0}

STAGE_SPAN = {  # share of the progress bar each stage owns
    "queued": (0, 2), "starting": (2, 8), "loading": (8, 14), "reading prompt": (14, 18),
    "sampling": (18, 88), "decoding": (88, 96), "saving": (96, 99), "done": (100, 100)}

# ── prompt writing ──────────────────────────────────────────────────────────
STYLES = {
    "cinematic": "cinematic film look, natural colour grade, shallow depth of field, motivated light",
    "aura": "aura edit: slow motion, dramatic low angles, wind and sparks, rim light, glowing eyes, fast push-ins",
    "anime": "2D anime style, clean line art, cel shading, vivid colours, expressive motion",
    "product": "premium product film: clean studio light, glossy reflections, smooth slow orbiting camera",
    "documentary": "handheld documentary look, available light, real textures, observational camera",
    "dreamy": "dreamy pastel palette, soft bloom, floating particles, gentle slow camera",
}

WRITER_RULES = """You write prompts for the MiniMax H3 video model. Reply with the prompt only: no preamble, no code fence.
Use exactly these three lines (each label, a colon, then text):
integrated_multimodal_description: ...
overall_soundscape: ...
non_diegetic_music: ...
Rules:
- English only. Concrete and visual: subject, look, setting, light, lens, motion.
- Split into shots: "[Shot 1] ..." then "[Shot 2] At 00:03.500 ..." with real timestamps inside the clip length. Short clips (<= 5 s) use one or two shots.
- Exactly one camera move per shot, written as move + size + speed, e.g. "the camera pushes in with medium amplitude at fast speed".
- Give actions times and speed words ("at 00:01.200 he snaps his head up at fast speed").
- Never write "no X" lists (there is no negative prompt). Say what you want instead ("plain cream dial, black numerals only").
- Text that must appear on screen goes in "double quotes", one string at a time.
- References are written <Picture 1>, <Video 1>, <Audio 1> when the user gave them.
- overall_soundscape: the real sounds of the scene. non_diegetic_music: describe the music, or "none".
- Keep it under 170 words in total."""


def writer_prompt(idea: str, secs: float, ratio: str, style: str = "", mode: str = "clip",
                  refs: dict | None = None) -> str:
    refs = refs or {}
    extra = []
    if mode == "frames":
        extra.append("The user gave a START image and an END image: describe the motion that carries the start into the end.")
    elif mode == "orbit":
        extra.append("This is a 360-degree orbit of the subject in the photo: the camera circles once around it at "
                     "constant speed and ends where it started; the subject holds still.")
    elif refs.get("first"):
        extra.append("The user gave a start image: spend the words on motion and camera, not on appearance.")
    if style in STYLES:
        extra.append("Style: " + STYLES[style] + ".")
    return (f"{WRITER_RULES}\n\nClip: {secs:g} s, aspect {ratio}. " + " ".join(extra) +
            f"\nIdea from the user (any language, write the prompt in English):\n{idea.strip()[:1500]}")


def template_prompt(idea: str, secs: float, style: str = "", mode: str = "clip") -> str:
    """Used when no writer model is available: still the official 3-field layout."""
    look = STYLES.get(style, STYLES["cinematic"])
    idea = re.sub(r"\s+", " ", idea).strip().rstrip(".")
    if mode == "orbit":
        cam = "the camera orbits 360 degrees around the subject at constant medium speed and returns to the start"
    else:
        cam = "the camera pushes in slowly with small amplitude"
    return (f"integrated_multimodal_description: [Shot 1] {idea}. {look[0].upper() + look[1:]}; {cam}; "
            f"natural, continuous motion through all {secs:g} seconds.\n"
            f"overall_soundscape: the real ambient sound of this scene, clear and close.\n"
            f"non_diegetic_music: none")


def clean_prompt(text: str) -> str:
    text = (text or "").strip()
    text = re.sub(r"^```[a-z]*\s*|\s*```$", "", text).strip()
    keep = [ln for ln in text.splitlines() if ln.strip()]
    i = next((k for k, ln in enumerate(keep) if ln.lower().startswith("integrated_multimodal_description")), 0)
    return "\n".join(keep[i:])[:4000]


def has_fields(prompt: str) -> bool:
    p = prompt.lower()
    return all(f in p for f in ("integrated_multimodal_description", "overall_soundscape", "non_diegetic_music"))


# ── autopilot: the director picks everything from one idea ──────────────────
DIRECTOR_RULES = """You direct one short AI video for the MiniMax H3 model. From the user's idea, choose the settings and write the prompt.
Reply with ONE JSON object and nothing else (no code fence):
{"title": "...", "ratio": "...", "secs": 0, "style": "...", "audio": true, "why": "...", "prompt": "..."}
- title: 2-5 words, in the user's language.
- ratio: "9:16" for people, animals, one subject, social clips; "16:9" for landscapes, chases, things moving sideways, wide scenes; "1:1" for products, objects, loops.
- secs: 4-6 for one action, 7-10 for a tiny story in 2-3 shots. Whole number.
- style: one of STYLE_IDS, or "" when none fits.
- audio: false only when the user wants silence.
- why: one short sentence in the user's language that explains the choices, like a director to a friend.
- prompt: exactly three lines joined by \\n, each a label, a colon, then text:
integrated_multimodal_description: ...
overall_soundscape: ...
non_diegetic_music: ...
Prompt rules:""" + WRITER_RULES.split("Rules:", 1)[1]

RATIO_RX = {"16:9": r"\b(landscape|panorama|wide|road|race|chase|drone|ocean|sea|city|skyline|car|train|horizon)\b",
            "1:1": r"\b(product|logo|bottle|watch|perfume|shoe|loop|icon|cup|phone)\b"}


def director_prompt(idea: str, has_picture: bool) -> str:
    rules = DIRECTOR_RULES.replace("STYLE_IDS", ", ".join(f'"{k}"' for k in STYLES))
    extra = ("\nThe user gave a start picture: the clip begins on it, so spend the words on motion, camera and sound, "
             "not on what it looks like. Pick the ratio closest to a phone photo (9:16) unless the idea says otherwise."
             if has_picture else "")
    return f"{rules}{extra}\n\nIdea from the user (any language; the prompt is always English):\n{idea.strip()[:1500]}"


def parse_direction(text: str) -> dict:
    """The director's JSON, checked field by field. Raises ValueError when nothing usable came back."""
    m = re.search(r"\{[\s\S]*\}", text or "")
    if not m:
        raise ValueError("no JSON")
    d = json.loads(m.group(0))
    out = {"title": re.sub(r"\s+", " ", str(d.get("title") or "")).strip()[:60],
           "ratio": d.get("ratio") if d.get("ratio") in ("9:16", "16:9", "1:1") else "9:16",
           "secs": int(min(10, max(3, round(float(d.get("secs") or 5))))),
           "style": d.get("style") if d.get("style") in STYLES else "",
           "audio": d.get("audio") is not False,
           "why": re.sub(r"\s+", " ", str(d.get("why") or "")).strip()[:240],
           "prompt": clean_prompt(str(d.get("prompt") or "").replace("\\n", "\n"))}
    if not has_fields(out["prompt"]):
        out["prompt"] = ""
    return out


def guess_direction(idea: str, has_picture: bool) -> dict:
    """No director model: still sensible choices from the words of the idea."""
    low = idea.lower()
    ratio = "9:16" if has_picture else next((r for r, rx in RATIO_RX.items() if re.search(rx, low)), "9:16")
    style = next((k for k in STYLES if k in low), "aura" if "aura" in low else "cinematic")
    return {"title": re.sub(r"\s+", " ", idea).strip()[:40], "ratio": ratio, "secs": 5, "style": style,
            "audio": True, "why": "", "prompt": ""}


def check_clip(mp4: str, secs: float) -> dict:
    """Cheap look at a finished clip: is it mostly black, or frozen? (ffmpeg filters, no model)."""
    try:
        r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", mp4, "-vf",
                            "blackdetect=d=0.4:pix_th=0.08,freezedetect=n=0.002:d=1.5", "-an", "-f", "null", "-"],
                           capture_output=True, text=True, timeout=90)
        log = r.stderr
        black = sum(float(x) for x in re.findall(r"black_duration:([\d.]+)", log))
        frozen = sum(float(x) for x in re.findall(r"freeze_duration: ?([\d.]+)", log))
        starts = [float(x) for x in re.findall(r"freeze_start: ?([\d.]+)", log)]
        ends = re.findall(r"freeze_end: ?([\d.]+)", log)
        if len(starts) > len(ends):  # still frozen when the clip ends: ffmpeg never prints that duration
            frozen += max(0.0, float(secs) - starts[-1])
        bad = black > 0.6 * secs or frozen > 0.7 * secs
        return {"black": round(black, 1), "frozen": round(frozen, 1), "ok": not bad}
    except Exception as exc:  # a broken check must never fail the video
        return {"ok": True, "error": str(exc)[:80]}


# ── sizes, frames, estimate ─────────────────────────────────────────────────
def frames_for(secs: float) -> int:
    """H3 wants 4k+1 frames at 24 fps: 3 s -> 73, 5 s -> 121, 10 s -> 241."""
    return 4 * max(4, round(float(secs) * FPS / 4)) + 1


def plan(spec: dict) -> dict:
    """Turn the tab's choices into renderer settings. Raises ValueError on bad input."""
    mode = spec.get("mode") or "clip"
    if mode not in MODES:
        raise ValueError(f"unknown video type {mode}")
    quality = spec.get("quality") or "draft"
    if quality not in QUALITY:
        raise ValueError("quality is draft or final")
    if mode == "orbit":
        w, h, secs, steps, loras = ORBIT["w"], ORBIT["h"], ORBIT["secs"], ORBIT["steps"], ORBIT["loras"]
        quality = "final"
    else:
        ratio = spec.get("ratio") or "9:16"
        if ratio not in SIZES[quality]:
            raise ValueError("ratio is 16:9, 9:16 or 1:1")
        w, h = SIZES[quality][ratio]
        secs = min(10.0, max(2.0, float(spec.get("secs") or 5)))
        steps, loras = QUALITY[quality]["steps"], QUALITY[quality]["loras"]
    if mode == "frames" and not (spec.get("first") and spec.get("last")):
        raise ValueError("start + end needs two pictures")
    if mode == "orbit" and not spec.get("first"):
        raise ValueError("orbit needs one photo")
    return {"mode": mode, "quality": quality, "w": w, "h": h, "secs": secs, "frames": frames_for(secs),
            "steps": steps, "loras": loras, "audio": bool(spec.get("audio", True))}


def load_stats() -> dict:
    try:
        with open(STATS_FILE) as f:
            s = json.load(f)
        return {**DEFAULT_STATS, **s, "stage": {**DEFAULT_STATS["stage"], **(s.get("stage") or {})}}
    except Exception:
        return json.loads(json.dumps(DEFAULT_STATS))


def save_stats(stats: dict):
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = STATS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(stats, f)
    os.replace(tmp, STATS_FILE)


def per_step(p: dict, stats: dict) -> float:
    u = p["w"] * p["h"] * p["frames"] / 1e8
    return stats["a"] * u ** stats["b"]


def estimate(p: dict, stats: dict | None = None, warm: bool = False) -> dict:
    stats = stats or load_stats()
    st = stats["stage"]
    sampling = p["steps"] * per_step(p, stats)
    start = 0.0 if warm else stats["cold_up"] + st["loading"]
    secs = start + st["reading prompt"] + sampling + st["decoding"] + st["saving"]
    usd = secs * USD_PER_SEC
    return {"secs": round(secs), "usd": round(usd, 3), "usd_high": round(usd * 1.35 + 0.01, 3),
            "warm": warm, "parts": {"start": round(start), "sampling": round(sampling),
                                    "other": round(st["reading prompt"] + st["decoding"] + st["saving"])}}


def learn(stats: dict, p: dict, timings: dict, cold: bool) -> dict:
    """Fold one finished job's real timings into the estimate (moving average, recent jobs weigh more)."""
    stages = timings.get("stages") or {}
    w = 0.7 if stats.get("n", 0) == 0 else 0.4
    samp = float(stages.get("sampling") or 0)
    if samp > 1 and p.get("steps"):
        u = p["w"] * p["h"] * p["frames"] / 1e8
        a_obs = samp / p["steps"] / (u ** stats["b"])
        stats["a"] = round((1 - w) * stats["a"] + w * a_obs, 4)
    for k in ("reading prompt", "decoding", "saving"):
        if stages.get(k):
            stats["stage"][k] = round((1 - w) * stats["stage"][k] + w * float(stages[k]), 2)
    if cold:
        if stages.get("loading"):
            stats["stage"]["loading"] = round((1 - w) * stats["stage"]["loading"] + w * float(stages["loading"]), 2)
        if timings.get("container_up"):
            stats["cold_up"] = round((1 - w) * stats["cold_up"] + w * float(timings["container_up"]), 2)
    stats["n"] = int(stats.get("n", 0)) + 1
    return stats


def progress_pct(job: dict, now: float | None = None) -> int:
    """Bar position from the stage the GPU reports, nudged by time inside a stage."""
    stage = job.get("stage") or "queued"
    if job.get("status") == "done":
        return 100
    lo, hi = STAGE_SPAN.get(stage, (0, 2))
    if stage == "sampling" and job.get("steps"):
        return int(lo + (hi - lo) * min(1.0, (job.get("step") or 0) / job["steps"]))
    est = (job.get("est") or {}).get("secs") or 60
    since = (now or time.time()) - float(job.get("stage_at") or job.get("started") or time.time())
    frac = 1 - math.exp(-since / max(5.0, est * 0.15))
    return int(lo + (hi - lo) * 0.9 * frac)


def month_key(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m")


def human_error(exc: BaseException | str) -> str:
    s = str(exc)
    low = s.lower()
    if "out of memory" in low or ("cuda" in low and "memory" in low):
        return "The GPU ran out of memory. Try a shorter clip or a draft."
    if "time cap" in low or "timeout" in low:
        return "It took too long and was stopped, so it can't run up a bill."
    if "token" in low and ("modal" in low or "auth" in low):
        return "Modal sign-in failed: check MODAL_TOKEN_ID and MODAL_TOKEN_SECRET in your keys."
    if "not found" in low and APP_NAME in low:
        return "The video app isn't deployed on Modal (panda-h3)."
    if "cancel" in low:
        return "Stopped."
    return s[:300] or "It failed."


# ── Modal ───────────────────────────────────────────────────────────────────
_modal_lock = threading.Lock()


def modal_mod():
    """Import modal with the owner's token from the key vault (it never leaves the worker)."""
    with _modal_lock:
        try:
            with open(SECRETS_ENV) as f:
                for line in f:
                    m = re.match(r"\s*(?:export\s+)?(MODAL_TOKEN_ID|MODAL_TOKEN_SECRET)=(.*)", line)
                    if m:
                        os.environ[m.group(1)] = m.group(2).strip().strip('"').strip("'")
        except FileNotFoundError:
            pass
        if not (os.environ.get("MODAL_TOKEN_ID") and os.environ.get("MODAL_TOKEN_SECRET")):
            raise RuntimeError("Add MODAL_TOKEN_ID and MODAL_TOKEN_SECRET to your keys first.")
        import modal  # noqa: PLC0415  (installed in the background at start)
        return modal


def _f(row, key: str):
    """A Modal billing row: a dict in some client versions, an object in others."""
    return row.get(key) if isinstance(row, dict) else getattr(row, key, None)


# ── the engine (runs inside worker.py) ──────────────────────────────────────
RUN_ACTIVE = ("directing", "filming")


class RunCancelled(Exception):
    pass


class Engine:
    """One per worker. `sb` is worker.py's Supabase client; owner() returns who pressed Start."""

    def __init__(self, sb, sb_url: str, owner, log=print, notify=None, llm=None):
        self.sb, self.sb_url, self.owner, self.log = sb, sb_url.rstrip("/"), owner, log
        self.notify = notify          # notify(title, body) -> phone push
        self.llm = llm                # llm(prompt) -> text, or None
        self.lock = threading.RLock()
        self.jobs: list[dict] = []
        self.runs: list[dict] = []    # autopilot runs: one idea -> director -> draft -> final
        self.usage_cache: dict | None = None
        self.usage_lock = threading.Lock()
        self.loaded_for = ""
        self.dirty_at = 0.0
        self.saved_at = 0.0
        self.pool = ThreadPoolExecutor(max_workers=6)
        self.threads: dict[str, bool] = {}
        self.stop = threading.Event()

    # storage
    def _path(self, name: str) -> str:
        return f"{self.owner()}/video/{name}"

    def _put(self, name: str, data: bytes, mime: str):
        import requests  # noqa: PLC0415
        from urllib.parse import quote  # noqa: PLC0415
        url = f"{self.sb_url}/storage/v1/object/{FILES_BUCKET}/{quote(self._path(name))}"
        h = self.sb.headers({"x-upsert": "true", "Content-Type": mime, "cache-control": "max-age=0"})
        r = requests.post(url, data=data, headers=h, timeout=600)
        if r.status_code >= 400:
            raise RuntimeError(f"upload {name}: {r.status_code} {r.text[:200]}")

    def load(self):
        owner = self.owner()
        if self.loaded_for == owner:
            return
        tmp = tempfile.mktemp(suffix=".json")
        jobs, runs, stats = [], [], None
        try:
            if self.sb.download(self._path("jobs.json"), tmp, bucket=FILES_BUCKET):
                with open(tmp) as f:
                    d = json.load(f)
                jobs, runs, stats = d.get("jobs") or [], d.get("runs") or [], d.get("stats")
                if d.get("modal") and not self.usage_cache:
                    self.usage_cache = {**d["modal"], "stale": True}
        except Exception as exc:
            self.log("video: jobs.json unreadable:", str(exc)[:120])
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        if stats and not os.path.exists(STATS_FILE):
            save_stats(stats)  # the estimate's learning survives a fresh machine
        with self.lock:
            self.jobs, self.runs, self.loaded_for = jobs, runs, owner

    def gpu(self) -> dict | None:
        try:
            beat = modal_mod().Dict.from_name(PROGRESS_DICT).get("_container") or {}
            return {"state": beat.get("state"), "t": beat.get("t"), "gpu": beat.get("gpu")} if beat else None
        except Exception:
            return None

    def save(self, force: bool = False):
        if not force and time.time() - self.saved_at < 1.5:
            self.dirty_at = self.dirty_at or time.time()
            return
        busy = any(j.get("status") in ("queued", "rendering") for j in self.jobs) or any(
            r.get("status") in RUN_ACTIVE for r in self.runs)
        gpu = self.gpu() if busy or force else None
        with self.lock:
            self.dirty_at = time.time()
            self.saved_at = time.time()
            body = {"v": 2, "at": time.time(), "jobs": self.jobs[-300:], "runs": self.runs[-60:],
                    "stats": load_stats(), "rate_usd_s": round(USD_PER_SEC, 6), "budget": self.budget(),
                    "spent": self.spent(), **({"modal": self.usage_view()} if self.usage_cache else {}),
                    **({"gpu": gpu} if gpu else {})}
            data = json.dumps(body, separators=(",", ":")).encode()
        try:
            self._put("jobs.json", data, "application/json")
            self.dirty_at = 0.0
        except Exception as exc:
            self.log("video: save failed:", str(exc)[:160])

    def flusher(self):
        while not self.stop.wait(1.0):
            if not self.usage_cache or time.time() - float(self.usage_cache.get("at") or 0) > USAGE_TTL:
                self.pool.submit(self.usage, True)  # off this thread: a slow Modal call must not stall saves
                if self.usage_cache:
                    self.usage_cache["at"] = time.time()  # one refresh in flight at a time
            if self.dirty_at and time.time() - self.saved_at > 1.5:
                self.save(force=True)
            try:
                self.drain_spool()
            except Exception as exc:
                self.log("video spool:", str(exc)[:160])

    def start(self):
        os.makedirs(SPOOL, exist_ok=True)
        os.makedirs(STATUS, exist_ok=True)
        try:
            self.load()
            for j in self.jobs:  # the last machine stopped mid-render: pick the jobs back up
                if j.get("status") in ("queued", "rendering"):
                    self._run(j)
            for r in self.runs:  # and the autopilot runs that were steering them
                if r.get("status") in RUN_ACTIVE:
                    threading.Thread(target=self._autopilot, args=(r,), daemon=True).start()
        except Exception as exc:
            self.log("video: start failed:", str(exc)[:160])
        threading.Thread(target=self.flusher, daemon=True).start()

    # money
    def settings(self) -> dict:
        try:
            rows = self.sb.rest("GET", "user_settings", params={"owner": f"eq.{self.owner()}", "select": "settings"})
            return (rows[0]["settings"] if rows else {}) or {}
        except Exception:
            return {}

    def budget(self) -> float:
        """The owner's stop line for this month's Modal spend (Modal's $30 free credit unless they set less)."""
        v = self.settings().get("modalCap")
        try:
            return max(0.0, float(v)) if v is not None else DEFAULT_BUDGET
        except (TypeError, ValueError):
            return DEFAULT_BUDGET

    def usage(self, force: bool = False) -> dict:
        """What Modal itself says this month cost, from its billing API, cached for USAGE_TTL.

        Modal books compute by the hour and only reports whole hours, so clips that finished after the
        last hour it counted are added from Panda's own records ("not counted yet")."""
        cached = self.usage_cache
        if cached and not force and not cached.get("stale") and time.time() - float(cached.get("at") or 0) < USAGE_TTL:
            return cached
        if not self.usage_lock.acquire(blocking=False):
            return cached or {}
        try:
            modal = modal_mod()
            bill = modal.Workspace.from_context().billing
            summary = bill.summary()
            adj = {str(k).lower(): float(v) for k, v in (summary.adjustments or {}).items()}
            billed = float(summary.billed_cost or 0)
            used = round(-adj.get("credits", 0.0) + billed, 4)
            now = datetime.now(timezone.utc)
            month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            today = now.replace(hour=0, minute=0, second=0, microsecond=0)
            # Modal reports whole intervals only: whole days before today, whole hours today.
            rows = [r for r in bill.report(start=month_start, resolution="d") if _f(r, "interval_start") < today]
            rows += bill.report(start=today, resolution="h")
            seen = [_f(r, "interval_start") for r in rows if float(_f(r, "cost") or 0) > 0]
            last = max(seen) if seen else None
            counted_to = (last + (timedelta(hours=1) if last and last >= today else timedelta(days=1))).timestamp() \
                if last else month_start.timestamp()
            panda = sum(float(_f(r, "cost") or 0) for r in rows
                        if str(_f(r, "description") or "").startswith(VIDEO_APPS))
            self.usage_cache = {"at": time.time(), "used": used, "billed": round(billed, 4),
                                "credit": MODAL_CREDIT, "panda": round(panda, 4),
                                "other": round(max(0.0, used - panda), 4), "counted_to": counted_to,
                                "month": now.strftime("%Y-%m"), "source": "modal"}
            self.dirty_at = self.dirty_at or time.time()
        except Exception as exc:
            self.log("video: Modal billing unreadable:", str(exc)[:160])
            if not cached:  # no bill yet: Panda's own count, marked as a guess
                self.usage_cache = {"at": time.time(), "used": self.spent(), "credit": MODAL_CREDIT,
                                    "panda": self.spent(), "other": 0.0, "counted_to": time.time(),
                                    "month": month_key(time.time()), "source": "panda", "error": str(exc)[:120]}
            else:
                cached.pop("stale", None)
        finally:
            self.usage_lock.release()
        return self.usage_cache or {}

    def usage_now(self, args: dict) -> dict:
        self.usage(force=bool(args.get("force")))
        self.save(force=True)
        return self.usage_view()

    def unbilled(self, since: float) -> float:
        """Panda clips Modal hasn't put on the bill yet (they finished after the last hour it counted)."""
        with self.lock:
            return round(sum(float(j.get("cost") or 0) for j in self.jobs
                             if float(j.get("finished") or 0) >= since), 4)

    def usage_view(self) -> dict:
        u = dict(self.usage_cache or {})
        if not u:
            return {}
        if u.get("month") != month_key(time.time()):  # a new month started since the last read
            u.update(used=0.0, panda=0.0, other=0.0, billed=0.0)
        u["pending"] = self.unbilled(float(u.get("counted_to") or 0)) if u.get("source") == "modal" else 0.0
        u["booked"] = round(self.committed(), 4)
        u["now"] = round(float(u.get("used") or 0) + u["pending"], 4)
        u["cap"] = self.budget()
        u["left"] = round(max(0.0, min(u["cap"], u["credit"]) - u["now"] - u["booked"]), 4)
        u.pop("stale", None)
        return u

    def guard(self, need: float, what: str = "this video"):
        """Refuse anything that would take this month's Modal spend past the owner's stop line."""
        self.usage()
        u = self.usage_view() or {"now": self.spent(), "booked": self.committed(), "cap": self.budget()}
        cap = u["cap"]
        if u["now"] + u["booked"] + need > cap:
            left = max(0.0, cap - u["now"] - u["booked"])
            booked = f" and ${u['booked']:.2f} booked" if u["booked"] else ""
            raise ValueError(f"Not enough Modal credit left: ${u['now']:.2f} of ${cap:.0f} used this month{booked}, "
                             f"{what} needs up to ${need:.2f} (${left:.2f} left). It resets on the 1st.")

    def spent(self, month: str | None = None) -> float:
        month = month or month_key(time.time())
        with self.lock:
            return round(sum(float(j.get("cost") or 0) for j in self.jobs
                             if month_key(float(j.get("created") or 0)) == month), 3)

    def committed(self) -> float:
        """Money already promised to jobs that are still waiting or rendering."""
        with self.lock:
            return sum(float((j.get("est") or {}).get("usd_high") or 0) for j in self.jobs
                       if j.get("status") in ("queued", "rendering"))

    def warm(self) -> bool:
        try:
            beat = modal_mod().Dict.from_name(PROGRESS_DICT).get("_container") or {}
            return beat.get("state") in ("ready", "idle", "busy") and time.time() - float(beat.get("t") or 0) < 12
        except Exception:
            return False

    # public ops
    def write_prompt(self, args: dict) -> dict:
        idea = str(args.get("idea") or "").strip()
        if not idea:
            raise ValueError("Type an idea first.")
        mode = args.get("mode") or "clip"
        secs = ORBIT["secs"] if mode == "orbit" else float(args.get("secs") or 5)
        ratio = "1:1" if mode == "orbit" else (args.get("ratio") or "9:16")
        style = str(args.get("style") or "")
        refs = {"first": bool(args.get("first")), "last": bool(args.get("last"))}
        if self.llm:
            try:
                text = clean_prompt(self.llm(writer_prompt(idea, secs, ratio, style, mode, refs)))
                if has_fields(text):
                    return {"prompt": text, "by": "writer"}
                self.log("video: writer answer had no 3-field layout, using the template")
            except Exception as exc:
                self.log("video: writer failed:", str(exc)[:160])
        return {"prompt": template_prompt(idea, secs, style, mode), "by": "template"}

    def estimate(self, args: dict) -> dict:
        p = plan(args)
        seeds = min(4, max(1, int(args.get("seeds") or 1)))
        warm = self.warm()
        one = estimate(p, warm=warm)
        # seeds after the first ride the warm container
        rest = estimate(p, warm=True) if seeds > 1 else None
        usd = one["usd"] + (rest["usd"] * (seeds - 1) if rest else 0)
        high = one["usd_high"] + (rest["usd_high"] * (seeds - 1) if rest else 0)
        secs = one["secs"] + (rest["secs"] * (seeds - 1) if rest else 0)
        self.load()
        return {**p, "seeds": seeds, "est": one, "total_usd": round(usd, 3), "total_high": round(high, 3),
                "total_secs": round(secs), "warm": warm, "budget": self.budget(), "spent": self.spent(),
                "committed": round(self.committed(), 3), "rate_usd_h": round(USD_PER_SEC * 3600, 2),
                "modal": self.usage_view()}

    def submit(self, args: dict, source: str = "tab") -> dict:
        prompt = clean_prompt(str(args.get("prompt") or ""))
        if not prompt:
            raise ValueError("The prompt is empty.")
        if not has_fields(prompt):  # a plain idea typed straight in: wrap it, don't refuse it
            prompt = template_prompt(prompt, float(args.get("secs") or 5), str(args.get("style") or ""),
                                     args.get("mode") or "clip")
        info = self.estimate(args)
        if not args.get("_guarded"):  # an autopilot run checked its whole plan already
            self.guard(info["total_high"])
        base_seed = int(args.get("seed") or secrets.randbelow(2**31))
        group = secrets.token_hex(4)
        made = []
        for k in range(info["seeds"]):
            jid = time.strftime("%y%m%d-%H%M%S-") + secrets.token_hex(2)
            job = {"id": jid, "group": group, "created": time.time(), "source": source, "run": args.get("run") or "",
                   "chat_id": args.get("chat_id"), "title": str(args.get("title") or args.get("idea") or "")[:80],
                   "idea": str(args.get("idea") or "")[:1500], "style": args.get("style") or "",
                   "prompt": prompt, "ratio": args.get("ratio") or ("1:1" if info["mode"] == "orbit" else "9:16"),
                   "seed": (base_seed + 7919 * k) % (2**31), "first": args.get("first") or "",
                   "last": args.get("last") or ("" if info["mode"] != "orbit" else args.get("first") or ""),
                   "from": args.get("from") or "",
                   **{key: info[key] for key in ("mode", "quality", "w", "h", "secs", "frames", "steps", "loras", "audio")},
                   "est": estimate(info, warm=info["warm"] if k == 0 else True),
                   "status": "queued", "stage": "queued", "step": 0, "pct": 0}
            made.append(job)
        with self.lock:
            self.jobs.extend(made)
        self.save(force=True)
        for job in made:
            self._run(job)
        return {"jobs": made, "total_usd": info["total_usd"], "total_high": info["total_high"]}

    def cancel(self, args: dict) -> dict:
        jid = str(args.get("id") or "")
        job = self.find(jid)
        if not job:
            raise ValueError("no such video")
        if job.get("status") in ("queued", "rendering"):
            job["cancel"] = True
            if job.get("call_id"):
                try:
                    modal_mod().FunctionCall.from_id(job["call_id"]).cancel()
                except Exception as exc:
                    self.log("video cancel:", str(exc)[:160])
            self._finish(job, "cancelled", error="Stopped.")
        return {"ok": True, "id": jid}

    def delete(self, args: dict) -> dict:
        jid = str(args.get("id") or "")
        job = self.find(jid)
        if not job:
            return {"ok": True}
        if job.get("status") in ("queued", "rendering"):
            self.cancel({"id": jid})
        for key in ("video", "poster"):
            if job.get(key):
                try:
                    self.sb.delete_object(job[key], bucket=FILES_BUCKET)
                except Exception:
                    pass
        if job.get("file_id"):
            try:
                self.sb.rest("DELETE", "files", params={"id": f"eq.{job['file_id']}"})
            except Exception:
                pass
        with self.lock:
            self.jobs = [j for j in self.jobs if j.get("id") != jid]
        self.save(force=True)
        return {"ok": True}

    def find(self, jid: str) -> dict | None:
        self.load()
        with self.lock:
            return next((j for j in self.jobs if j.get("id") == jid), None)

    # ── autopilot: one idea in, a finished film out ─────────────────────────
    def auto(self, args: dict, source: str = "tab") -> dict:
        idea = str(args.get("idea") or "").strip()
        if not idea:
            raise ValueError("Tell Panda the idea first.")
        self.load()
        kind = "quick" if args.get("plan") == "quick" else "full"
        # Before anything is spent: the whole plan at the longest clip the director may pick.
        self.guard(self._plan_high({"ratio": "16:9", "secs": 8}, kind), "this film")
        run = {"id": "r" + time.strftime("%y%m%d-%H%M%S-") + secrets.token_hex(2), "created": time.time(),
               "source": "chat" if source == "chat" else "tab", "chat_id": args.get("chat_id"),
               "idea": idea[:1500], "first": str(args.get("first") or ""), "plan": kind, "status": "directing",
               "step": "director", "title": re.sub(r"\s+", " ", idea)[:60], "log": [], "jobs": [], "phases": {}}
        with self.lock:
            self.runs.append(run)
        self._say(run, "director", "Reading your idea")
        threading.Thread(target=self._autopilot, args=(run,), daemon=True).start()
        return {"run": run}

    def auto_cancel(self, args: dict) -> dict:
        run = self.find_run(str(args.get("id") or ""))
        if not run:
            raise ValueError("no such film")
        if run.get("status") in RUN_ACTIVE:
            run["cancel"] = True
            for jid in run.get("jobs") or []:
                job = self.find(jid)
                if job and job.get("status") in ("queued", "rendering"):
                    self.cancel({"id": jid})
            run.update(status="cancelled", finished=time.time())
            self._say(run, "cancelled", "Stopped")
        return {"ok": True}

    def auto_dismiss(self, args: dict) -> dict:
        run = self.find_run(str(args.get("id") or ""))
        if run and run.get("status") not in RUN_ACTIVE:
            run["hidden"] = True
            self.save(force=True)
        return {"ok": True}

    def find_run(self, rid: str) -> dict | None:
        self.load()
        with self.lock:
            return next((r for r in self.runs if r.get("id") == rid), None)

    def _plan_high(self, choice: dict, kind: str) -> float:
        base = {"mode": "clip", "ratio": choice.get("ratio") or "9:16", "secs": choice.get("secs") or 5}
        high = estimate(plan({**base, "quality": "draft"}), warm=self.warm())["usd_high"]
        if kind == "full":
            high += estimate(plan({**base, "quality": "final"}), warm=True)["usd_high"]
        return round(high, 3)

    def _say(self, run: dict, step: str, text: str):
        run["step"] = step
        run.setdefault("log", []).append({"t": round(time.time(), 1), "step": step, "text": text[:240]})
        run["log"] = run["log"][-30:]
        self.dirty_at = self.dirty_at or time.time()
        try:
            with open(os.path.join(STATUS, f"run-{run['id']}.json"), "w") as f:
                json.dump(run, f)
        except Exception:
            pass

    def direct(self, idea: str, has_picture: bool) -> dict:
        """The director: ratio, length, style, sound and the H3 prompt, from the idea alone."""
        if self.llm:
            try:
                d = parse_direction(self.llm(director_prompt(idea, has_picture)))
                if not d["prompt"]:
                    d["prompt"] = self.write_prompt({"idea": idea, "secs": d["secs"], "ratio": d["ratio"],
                                                     "style": d["style"], "first": has_picture})["prompt"]
                return {**d, "by": "director"}
            except Exception as exc:
                self.log("video: director failed, guessing:", str(exc)[:160])
        d = guess_direction(idea, has_picture)
        return {**d, "prompt": template_prompt(idea, d["secs"], d["style"]), "by": "guess"}

    def _autopilot(self, run: dict):
        try:
            if not run.get("choice"):
                run["status"] = "directing"
                self._say(run, "director", "Picking the shape, length, look and sound")
                d = self.direct(run["idea"], bool(run.get("first")))
                if run.get("cancel"):
                    raise RunCancelled()
                run["choice"] = {k: d[k] for k in ("ratio", "secs", "style", "audio")}
                run.update(title=d["title"] or run["title"], why=d["why"], prompt=d["prompt"], by=d["by"],
                           est_high=self._plan_high(run["choice"], run["plan"]))
                style = d["style"].replace("aura", "aura edit") or "natural look"
                self._say(run, "script", f"Wrote the script: {d['ratio']}, {d['secs']} s, {style}"
                                         f"{'' if d['audio'] else ', silent'}")
            c = run["choice"]
            spec = {"mode": "clip", "ratio": c["ratio"], "secs": c["secs"], "style": c["style"], "audio": c["audio"],
                    "idea": run["idea"], "prompt": run["prompt"], "first": run.get("first") or "", "seeds": 1,
                    "title": run["title"], "run": run["id"], "chat_id": run.get("chat_id")}
            run["status"] = "filming"
            draft = self._film(run, {**spec, "quality": "draft"}, "draft")
            best = draft
            if run["plan"] == "full":
                self._say(run, "final", "Filming the final in full quality, same take")
                best = self._film(run, {**spec, "quality": "final", "seed": draft["seed"], "from": draft["id"]}, "final")
            cost = sum(float((self.find(j) or {}).get("cost") or 0) for j in run["jobs"])
            run.update(status="done", finished=time.time(), best=best["id"], cost=round(cost, 4))
            self._say(run, "done", "Ready to watch")
            if self.notify:
                self.notify("Your film is ready", f"{run['title']} · ${cost:.2f}")
        except RunCancelled:
            run.update(status="cancelled", finished=run.get("finished") or time.time())
            if run.get("step") != "cancelled":
                self._say(run, "cancelled", "Stopped")
        except BaseException as exc:  # noqa: BLE001  the tab must hear about any failure
            run.update(status="failed", finished=time.time(), error=human_error(exc))
            self._say(run, "failed", run["error"])
            self.log("video autopilot failed:", run["id"], str(exc)[:300])
        finally:
            self.save(force=True)

    def _film(self, run: dict, spec: dict, phase: str) -> dict:
        """Film one phase, check it, and take it again once if it failed or came out black or frozen."""
        tries = run.setdefault("tries", {})
        phases = run.setdefault("phases", {})
        while True:
            if run.get("cancel"):
                raise RunCancelled()
            job = self.find(phases[phase]) if phases.get(phase) else None
            if not job:
                if phase == "draft":
                    self._say(run, "draft", "Filming a quick draft" + (", new take" if tries.get(phase) else ""))
                out = self.submit(spec, source=run["source"])
                job = out["jobs"][0]
                job["phase"] = phase
                phases[phase] = job["id"]
                run["jobs"].append(job["id"])
                self.save(force=True)
            while job.get("status") in ("queued", "rendering"):
                if run.get("cancel"):
                    self.cancel({"id": job["id"]})
                    raise RunCancelled()
                time.sleep(1.5)
            n = tries.get(phase, 0)
            if job["status"] == "cancelled":
                raise RunCancelled()
            if job["status"] == "done":
                chk = job.get("check") or {}
                if chk.get("ok", True) or n >= 1:
                    if phase == "draft" and "black" in chk:
                        self._say(run, "check", "Checked the draft: it moves and it's not dark" if chk["ok"]
                                  else "The new take is still a bit still, keeping it")
                    return job
                why = "came out dark" if chk.get("black", 0) >= chk.get("frozen", 0) else "barely moved"
                self._say(run, phase, f"The {phase} {why}, so Panda films a new take")
                spec = {k: v for k, v in spec.items() if k != "seed"}
            else:  # failed: one more go, shorter if the GPU ran out of memory
                if n >= 1:
                    raise RuntimeError(job.get("error") or "the video failed")
                shorter = "memory" in (job.get("error") or "").lower() and spec["secs"] > 3
                if shorter:
                    spec = {**spec, "secs": max(3, int(spec["secs"]) - 2)}
                self._say(run, phase, f"The {phase} didn't work ({(job.get('error') or 'error').rstrip('.')}). "
                                      f"Trying once more{', a bit shorter' if shorter else ''}")
            tries[phase] = n + 1
            phases.pop(phase, None)

    # rendering
    def _run(self, job: dict):
        if self.threads.get(job["id"]):
            return
        self.threads[job["id"]] = True
        self.pool.submit(self._render, job)

    def _ref_bytes(self, path: str) -> bytes | None:
        if not path:
            return None
        tmp = tempfile.mktemp()
        try:
            if path.startswith("/") and os.path.exists(path):
                shutil.copy(path, tmp)
            elif not self.sb.download(path, tmp, bucket=FILES_BUCKET):
                raise RuntimeError("could not read the reference picture")
            out = tmp + ".png"
            # The H3 node wants a plain RGB picture; ffmpeg converts any phone photo (HEIC needs Pillow).
            r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", tmp, "-frames:v", "1", out],
                               capture_output=True, text=True, timeout=60)
            if r.returncode != 0 or not os.path.exists(out):
                raise RuntimeError("that picture could not be read")
            with open(out, "rb") as f:
                return f.read()
        finally:
            for p in (tmp, tmp + ".png"):
                if os.path.exists(p):
                    os.remove(p)

    def _render(self, job: dict):
        jid = job["id"]
        try:
            modal = modal_mod()
            progress = modal.Dict.from_name(PROGRESS_DICT)
            if job.get("call_id"):
                call = modal.FunctionCall.from_id(job["call_id"])
            else:
                payload = {k: job[k] for k in ("id", "prompt", "w", "h", "frames", "steps", "seed", "loras", "audio")}
                payload["first_png"] = self._ref_bytes(job.get("first") or "")
                payload["last_png"] = self._ref_bytes(job.get("last") or "")
                # Hard cap per job, besides Modal's own 20 minutes: about 2.5x the estimate.
                payload["max_secs"] = int(min(18 * 60, max(240, (job.get("est") or {}).get("secs", 120) * 2.5)))
                h3 = modal.Cls.from_name(APP_NAME, CLS_NAME)()
                call = h3.render.spawn(payload)
                job["call_id"] = call.object_id
                job["submitted"] = time.time()
                self.save(force=True)
            self._write_status(job)
            last_stage = None
            while True:
                if job.get("cancel"):
                    return
                try:
                    result = call.get(timeout=1.5)
                    break
                except (TimeoutError, modal.exception.TimeoutError):
                    pass  # still running
                try:
                    pr = progress.get(jid) or {}
                except Exception:
                    pr = {}
                if pr:
                    stage = pr.get("stage") or job.get("stage")
                    if stage != last_stage:
                        job["stage_at"] = time.time()
                        last_stage = stage
                        if job.get("status") == "queued" and stage not in ("queued",):
                            job["status"], job["started"] = "rendering", float(pr.get("started") or time.time())
                    job.update(stage=stage, step=int(pr.get("step") or 0), steps=int(pr.get("steps") or job["steps"]),
                               cold=bool(pr.get("cold")))
                job["pct"] = progress_pct(job)
                job["eta"] = self._eta(job)
                self.dirty_at = self.dirty_at or time.time()
                self._write_status(job)
            self._done(job, result)
        except BaseException as exc:  # noqa: BLE001  any failure must reach the tab
            if job.get("status") not in ("cancelled",):
                self._finish(job, "failed", error=human_error(exc))
                self.log("video job failed:", jid, str(exc)[:300])
        finally:
            self.threads.pop(jid, None)

    def _eta(self, job: dict) -> int:
        est = (job.get("est") or {}).get("secs") or 60
        started = float(job.get("started") or job.get("submitted") or time.time())
        if job.get("stage") == "sampling" and job.get("step"):
            stats = load_stats()
            left = (job["steps"] - job["step"]) * per_step(job, stats)
            return int(left + stats["stage"]["decoding"] + stats["stage"]["saving"])
        return int(max(5, est - (time.time() - started)))

    def _done(self, job: dict, result: dict):
        video = result.get("video") or b""
        if not video:
            raise RuntimeError("the GPU finished without a video")
        timings = result.get("timings") or {}
        cold = bool(result.get("cold"))
        billed = float(timings.get("total") or 0) + (float(timings.get("container_up") or 0) if cold else 0)
        tmpdir = tempfile.mkdtemp()
        try:
            mp4 = os.path.join(tmpdir, "v.mp4")
            with open(mp4, "wb") as f:
                f.write(video)
            jpg = os.path.join(tmpdir, "p.jpg")
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", str(min(1.0, job["secs"] / 3)), "-i", mp4,
                            "-frames:v", "1", "-vf", "scale=480:-2", "-q:v", "4", jpg], timeout=60)
            if job.get("run"):  # the autopilot looks at its own drafts before going on
                job["check"] = check_clip(mp4, float(job.get("secs") or 5))
            name = f"{job['id']}.mp4"
            self._put(name, video, "video/mp4")
            job["video"] = self._path(name)
            if os.path.exists(jpg):
                with open(jpg, "rb") as f:
                    self._put(f"{job['id']}.jpg", f.read(), "image/jpeg")
                job["poster"] = self._path(f"{job['id']}.jpg")
            try:
                row = self.sb.rest("POST", "files", prefer="return=representation", body={
                    "owner": self.owner(), "chat_id": job.get("chat_id") or None, "kind": "output",
                    "name": safe_title(job) + ".mp4", "path": job["video"], "mime": "video/mp4", "size": len(video)})
                job["file_id"] = row[0]["id"] if row else ""
            except Exception as exc:
                self.log("video files row:", str(exc)[:160])
            if job.get("source") == "chat":  # the agent's pvideo --wait copies it into the reply
                keep = os.path.join(STATE_DIR, "done", name)
                os.makedirs(os.path.dirname(keep), exist_ok=True)
                shutil.copy(mp4, keep)
                job["local"] = keep
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
        job.update(timings=timings, gpu=result.get("gpu"), cold=cold, billed_secs=round(billed, 1),
                   cost=round(billed * USD_PER_SEC, 4), size=len(video))
        save_stats(learn(load_stats(), job, timings, cold))
        self._finish(job, "done")
        if self.notify and not job.get("run"):  # an autopilot run sends one message, at its end
            self.notify("Your video is ready", f"{job.get('title') or 'Video'} · ${job['cost']:.2f}")

    def _finish(self, job: dict, status: str, error: str = ""):
        job.update(status=status, finished=time.time(), stage="done" if status == "done" else status)
        job["pct"] = 100 if status == "done" else job.get("pct", 0)
        if error:
            job["error"] = error
        if status != "done" and job.get("started") and not job.get("cost"):
            # a failed or stopped job still used the GPU for a while: count it
            used = max(0.0, time.time() - float(job["started"]))
            job["cost"] = round(min(used, 20 * 60) * USD_PER_SEC, 4)
        self._write_status(job)
        self.save(force=True)

    def _write_status(self, job: dict):
        try:
            with open(os.path.join(STATUS, job["id"] + ".json"), "w") as f:
                json.dump(job, f)
        except Exception:
            pass

    # chat agent requests (pvideo / the Python API below)
    def agent_op(self, op: str, req: dict) -> dict:
        """One request from a chat agent. Every op that spends money goes through guard()."""
        if op == "auto":
            return {"run": self.auto(req, source="chat")["run"]["id"]}
        if op == "make":
            out = self.submit(req, source="chat")
            return {"jobs": [j["id"] for j in out["jobs"]], "total_usd": out["total_usd"], "total_high": out["total_high"]}
        if op == "write":
            return self.write_prompt(req)
        if op == "estimate":
            e = self.estimate(req)
            return {k: e[k] for k in ("mode", "quality", "w", "h", "secs", "frames", "steps", "seeds", "total_usd",
                                      "total_high", "total_secs", "warm")} | {"left": (e.get("modal") or {}).get("left")}
        if op == "budget":
            u = self.usage_now({"force": bool(req.get("force"))}) or {}
            return {k: u.get(k) for k in ("now", "booked", "cap", "left", "credit", "source", "month")}
        if op == "cancel":
            jid = str(req.get("id") or "")
            return self.auto_cancel({"id": jid}) if jid.startswith("r") else self.cancel({"id": jid})
        if op == "list":
            self.load()
            n = max(1, min(20, int(req.get("n") or 8)))
            keep = ("id", "status", "stage", "pct", "title", "quality", "mode", "ratio", "secs", "cost", "error", "run")
            with self.lock:
                jobs = [{k: j.get(k) for k in keep} for j in sorted(self.jobs, key=lambda j: -j.get("created", 0))[:n]]
                runs = [{k: r.get(k) for k in ("id", "status", "step", "title", "plan", "best", "cost", "error", "jobs")}
                        for r in sorted(self.runs, key=lambda r: -r.get("created", 0))[:n]]
            return {"jobs": jobs, "runs": runs}
        raise ValueError(f"unknown request: {op}")

    def drain_spool(self):
        if not os.path.isdir(SPOOL):
            return
        for name in sorted(os.listdir(SPOOL)):
            if not name.endswith(".json"):
                continue
            path = os.path.join(SPOOL, name)
            try:
                with open(path) as f:
                    req = json.load(f)
                os.remove(path)
            except Exception:
                continue
            rid = req.pop("request_id", name[:-5])
            try:
                res = {"ok": True, **self.agent_op(req.pop("op", "") or "make", req)}
            except Exception as exc:
                res = {"ok": False, "error": str(exc)[:400]}
            with open(os.path.join(STATUS, f"req-{rid}.json"), "w") as f:
                json.dump(res, f)


def safe_title(job: dict) -> str:
    t = re.sub(r"[^\w\- ]+", "", job.get("title") or job.get("idea") or "video").strip()[:40] or "video"
    return re.sub(r"\s+", "-", t).lower() + f"-{job.get('quality', '')}-{job['id'][-4:]}"



# ── Agent API: the same calls as `pvideo`, for agents that would rather write Python ──
#   import sys; sys.path.insert(0, os.path.expanduser("~/.kiro/crew/skills/panda-video")); import video
#   rid = video.auto("a pelican on a bike")            -> run id;   video.wait_run(rid) -> {"file": ..., ...}
#   ids = video.make(prompt, quality="draft")["jobs"]  -> job ids;  video.wait_jobs(ids) -> [{"file": ...}]
#   video.budget(), video.estimate(...), video.write(idea), video.recent(), video.cancel(id)
# Every call goes through the running engine in Panda's worker, so it needs Panda on, it honours the
# owner's Modal stop line, and everything made here also shows in the Video tab.

def request(op: str, **args) -> dict:
    """Hand one request to the engine and return its answer. Raises RuntimeError when it refuses."""
    if not os.path.isdir(SPOOL):
        raise RuntimeError("The video engine isn't running (Panda must be on).")
    rid = secrets.token_hex(6)
    args.setdefault("chat_id", os.environ.get("PANDA_CHAT_ID") or None)
    tmp = os.path.join(SPOOL, f".{rid}.tmp")
    with open(tmp, "w") as f:
        json.dump({**args, "op": op, "request_id": rid}, f)
    os.replace(tmp, os.path.join(SPOOL, f"{rid}.json"))  # never half-read by the engine
    res_path = os.path.join(STATUS, f"req-{rid}.json")
    for _ in range(180 if op == "write" else 60):
        if os.path.exists(res_path):
            break
        time.sleep(1)
    else:
        raise RuntimeError("The video engine didn't pick the request up.")
    with open(res_path) as f:
        res = json.load(f)
    os.remove(res_path)
    if not res.pop("ok", False):
        raise RuntimeError(res.get("error") or "refused")
    return res


def _abs(p: str) -> str:
    return os.path.abspath(os.path.expanduser(p)) if p else ""


def _read(path: str) -> dict | None:
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return None


def auto(idea: str, plan: str = "full", first: str = "") -> str:
    """Autopilot: the director picks everything. Returns the run id."""
    return request("auto", idea=idea, plan=plan, first=_abs(first))["run"]


def make(prompt: str, idea: str = "", mode: str = "clip", quality: str = "draft", ratio: str = "9:16", secs: float = 5,
         seeds: int = 1, first: str = "", last: str = "", style: str = "", title: str = "", seed: int | None = None) -> dict:
    """One clip (or `seeds` takes) from an H3 prompt or a plain idea. Returns {"jobs": [ids], "total_usd", "total_high"}."""
    if prompt and os.path.exists(os.path.expanduser(prompt)):
        with open(os.path.expanduser(prompt)) as f:
            prompt = f.read()
    spec = {"prompt": prompt or idea, "idea": idea or prompt[:200], "mode": mode, "quality": quality, "ratio": ratio,
            "secs": secs, "seeds": seeds, "first": _abs(first), "last": _abs(last), "style": style, "title": title}
    if seed is not None:
        spec["seed"] = seed
    return request("make", **spec)


def estimate(**spec) -> dict:
    """Price before spending: total_usd (likely), total_high (worst case), total_secs, left (credit left)."""
    return request("estimate", **spec)


def write(idea: str, secs: float = 5, ratio: str = "9:16", style: str = "", mode: str = "clip") -> dict:
    """Panda's prompt writer: idea -> H3 3-field prompt. Returns {"prompt", "by"}."""
    return request("write", idea=idea, secs=secs, ratio=ratio, style=style, mode=mode)


def budget(force: bool = False) -> dict:
    """This month's Modal money: now (spent), booked (promised to running jobs), cap, left."""
    return request("budget", force=force)


def recent(n: int = 8) -> dict:
    """Newest jobs and autopilot runs, with status, progress and cost."""
    return request("list", n=n)


def cancel(id: str) -> dict:
    """Stop a job (id like 261027-...) or a whole autopilot run (id starting with r)."""
    return request("cancel", id=id)


def _deliver(j: dict, keep: bool = True) -> str:
    """Copy a finished clip to ~/outputs (so the chat shows it as a card) and return the path."""
    local = j.get("local")
    if not local or not os.path.exists(local):
        return ""
    dest = ""
    if keep:
        os.makedirs(OUTPUTS, exist_ok=True)
        dest = os.path.join(OUTPUTS, safe_title(j) + ".mp4")
        shutil.copy(local, dest)
    os.remove(local)
    return dest


def wait_jobs(ids: list[str], on_update=None, timeout: float = 3600) -> list[dict]:
    """Wait for jobs to finish; returns one {"id", "status", "file", "cost", "error"} per job."""
    pending, out, seen, end = list(ids), {}, {}, time.time() + timeout
    while pending and time.time() < end:
        time.sleep(4)
        for jid in list(pending):
            j = _read(os.path.join(STATUS, jid + ".json"))
            if not j:
                continue
            mark = (j.get("status"), j.get("stage"), j.get("pct"))
            if on_update and seen.get(jid) != mark:
                on_update(j)
            seen[jid] = mark
            if j.get("status") in ("done", "failed", "cancelled"):
                pending.remove(jid)
                out[jid] = {"id": jid, "status": j["status"], "file": _deliver(j) if j["status"] == "done" else "",
                            "cost": j.get("cost") or 0, "error": j.get("error") or ""}
    for jid in pending:
        out[jid] = {"id": jid, "status": "waiting", "file": "", "cost": 0, "error": "still rendering; check later"}
    return [out[i] for i in ids]


def wait_run(rid: str, on_step=None, timeout: float = 3600) -> dict:
    """Wait for an autopilot run. on_step(text) gets each director note. Returns status, title, file, cost, error."""
    path, said, end, run = os.path.join(STATUS, f"run-{rid}.json"), 0, time.time() + timeout, None
    while time.time() < end:
        time.sleep(3)
        run = _read(path) or run
        if not run:
            continue
        for line in run.get("log", [])[said:]:
            on_step and on_step(line["text"])
        said = len(run.get("log", []))
        if run.get("status") not in RUN_ACTIVE:
            break
    if not run:
        return {"id": rid, "status": "unknown", "file": "", "cost": 0, "error": "no such film"}
    file = ""
    if run.get("status") == "done":
        for jid in run.get("jobs") or []:
            j = _read(os.path.join(STATUS, jid + ".json"))
            if j:
                got = _deliver(j, keep=jid == run.get("best"))  # only the best take goes into the reply
                file = got or file
    return {"id": rid, "status": run.get("status") if run.get("status") not in RUN_ACTIVE else "waiting",
            "title": run.get("title"), "file": file, "cost": run.get("cost") or 0, "error": run.get("error") or "",
            "choice": run.get("choice"), "why": run.get("why")}


# ── pvideo: the chat agent's command (thin shell over the API above) ──
def _cli():
    ap = argparse.ArgumentParser(prog="pvideo", description="Make videos on the owner's Modal GPU (MiniMax H3).")
    ap.add_argument("--json", action="store_true", help="print one JSON object instead of text")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("estimate", "make"):
        s = sub.add_parser(name)
        s.add_argument("--prompt", default="", help="H3 3-field prompt, or a file path holding it")
        s.add_argument("--idea", default="")
        s.add_argument("--mode", default="clip", choices=MODES)
        s.add_argument("--quality", default="draft", choices=tuple(QUALITY))
        s.add_argument("--ratio", default="9:16", choices=("9:16", "16:9", "1:1"))
        s.add_argument("--secs", type=float, default=5)
        s.add_argument("--seeds", type=int, default=1)
        s.add_argument("--seed", type=int, default=None, help="reuse a seed (e.g. the draft's) for the final")
        s.add_argument("--first", default="", help="start picture (local path)")
        s.add_argument("--last", default="", help="end picture (local path)")
        s.add_argument("--style", default="", choices=("",) + tuple(STYLES))
        s.add_argument("--title", default="")
        s.add_argument("--wait", action="store_true", help="wait and copy the clips to ~/outputs")
    au = sub.add_parser("auto", help="autopilot: one idea in, Panda directs, films a draft, then the final")
    au.add_argument("--idea", required=True)
    au.add_argument("--plan", default="full", choices=("full", "quick"), help="quick = draft only")
    au.add_argument("--first", default="", help="start picture (local path)")
    au.add_argument("--wait", action="store_true", help="wait and copy the film to ~/outputs")
    wr = sub.add_parser("write", help="idea -> H3 prompt (Panda's writer)")
    wr.add_argument("--idea", required=True)
    wr.add_argument("--secs", type=float, default=5)
    wr.add_argument("--ratio", default="9:16", choices=("9:16", "16:9", "1:1"))
    wr.add_argument("--style", default="", choices=("",) + tuple(STYLES))
    wr.add_argument("--mode", default="clip", choices=MODES)
    wr.add_argument("--out", default="", help="also save the prompt to this file")
    sub.add_parser("budget", help="Modal money this month: spent, booked, cap, left")
    ls = sub.add_parser("list", help="newest jobs and autopilot runs")
    ls.add_argument("-n", type=int, default=8)
    ca = sub.add_parser("cancel", help="stop a job or a whole autopilot run")
    ca.add_argument("id")
    wt = sub.add_parser("wait", help="wait for a job or run started earlier (ids starting with r are runs)")
    wt.add_argument("ids", nargs="+")
    st = sub.add_parser("status")
    st.add_argument("ids", nargs="*")
    a = ap.parse_args()
    say = (lambda *x: None) if a.json else (lambda *x: print(*x, flush=True))
    try:
        out = _cli_run(a, say)
    except RuntimeError as exc:
        if a.json:
            print(json.dumps({"ok": False, "error": str(exc)}))
            sys.exit(1)
        sys.exit(str(exc))
    if a.json:
        print(json.dumps({"ok": True, **out}, default=str))


def _cli_run(a, say) -> dict:
    if a.cmd == "status":
        ids = a.ids or sorted((n[:-5] for n in os.listdir(STATUS) if not n.startswith(("req-", "run-"))), reverse=True)[:5]
        rows = []
        for jid in ids:
            j = _read(os.path.join(STATUS, jid + ".json")) or _read(os.path.join(STATUS, f"run-{jid}.json")) or {}
            rows.append({"id": jid, **{k: j.get(k) for k in ("status", "stage", "step", "pct", "cost", "error")}})
            say(f"{jid}: {j.get('status', 'unknown')} {j.get('stage') or j.get('step') or ''} {j.get('pct') or 0}%"
                f" cost ${j.get('cost') or 0:.2f}{' — ' + j['error'] if j.get('error') else ''}")
        return {"items": rows}
    if a.cmd == "budget":
        b = budget(force=True)
        say(f"Modal this month: ${b.get('now') or 0:.2f} spent, ${b.get('booked') or 0:.2f} booked, "
            f"${b.get('left') or 0:.2f} left of ${b.get('cap') or 0:.0f}")
        return b
    if a.cmd == "list":
        r = recent(a.n)
        for run in r["runs"]:
            say(f"film {run['id']}: {run['status']} ({run.get('step')}) · {run.get('title')} · ${run.get('cost') or 0:.2f}")
        for j in r["jobs"]:
            say(f"clip {j['id']}: {j['status']} {j.get('pct') or 0}% · {j.get('title')} · {j.get('quality')} "
                f"{j.get('ratio')} {j.get('secs')} s · ${j.get('cost') or 0:.2f}")
        return r
    if a.cmd == "cancel":
        cancel(a.id)
        say(f"stopped {a.id}")
        return {"id": a.id}
    if a.cmd == "write":
        w = write(a.idea, a.secs, a.ratio, a.style, a.mode)
        if a.out:
            with open(os.path.expanduser(a.out), "w") as f:
                f.write(w["prompt"])
            w["file"] = _abs(a.out)
        say(w["prompt"])
        return w
    if a.cmd == "wait":
        runs = [i for i in a.ids if i.startswith("r")]
        jobs = [i for i in a.ids if not i.startswith("r")]
        res = [wait_run(r, on_step=lambda t: say("·", t)) for r in runs] + wait_jobs(jobs)
        for r in res:
            say(f"{r['status']} {r['id']}: {r['file'] or r['error']} · ${r['cost'] or 0:.2f}")
        return {"results": res}
    if a.cmd == "auto":
        rid = auto(a.idea, a.plan, a.first)
        say(f"autopilot {rid} started · watch it in the Video tab")
        if not a.wait:
            return {"run": rid}
        r = wait_run(rid, on_step=lambda t: say("·", t))
        if r["status"] != "done":
            raise RuntimeError(f"{r['status']}: {r['error']}")
        say(f"done: {r['file']} · {r['title']} · total ${r['cost']:.2f}")
        return r
    spec = {k: getattr(a, k) for k in ("prompt", "idea", "mode", "quality", "ratio", "secs", "seeds", "first", "last",
                                        "style", "title")}
    if a.cmd == "estimate":
        e = estimate(**{**spec, "prompt": spec["prompt"] or spec["idea"], "first": _abs(a.first), "last": _abs(a.last)})
        say(json.dumps(e))
        return e
    res = make(**spec, seed=a.seed)
    say(f"queued {', '.join(res['jobs'])} · about ${res['total_usd']:.2f} · watch it in the Video tab")
    if not a.wait:
        return res
    done = wait_jobs(res["jobs"], on_update=lambda j: None)
    for r in done:
        say(f"{r['status']} {r['id']}: {r['file'] or r['error']} · ${r['cost'] or 0:.2f}")
    return {**res, "results": done}


if __name__ == "__main__":
    _cli()
