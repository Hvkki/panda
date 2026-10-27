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
from datetime import datetime, timezone

HOME = os.path.expanduser("~")
STATE_DIR = os.path.join(HOME, ".panda", "video")
SPOOL = os.path.join(STATE_DIR, "spool")      # pvideo requests waiting for the engine
STATUS = os.path.join(STATE_DIR, "status")    # one json per job, for pvideo --wait
STATS_FILE = os.path.join(STATE_DIR, "stats.json")
SECRETS_ENV = os.path.join(HOME, ".panda", "secrets.env")
OUTPUTS = os.path.join(HOME, "outputs")

APP_NAME, CLS_NAME, PROGRESS_DICT = "panda-h3", "H3", "panda-h3-progress"
FILES_BUCKET = "panda-files"

# Modal prices (modal.com/pricing): RTX PRO 6000 $3.03/h, CPU $0.0000131 per core-second,
# memory $0.00000222 per GiB-second. The renderer asks for 4 cores and 48 GiB.
GPU_USD_S = 3.03 / 3600
USD_PER_SEC = GPU_USD_S + 4 * 0.0000131 + 48 * 0.00000222   # ~= $0.001 a second, $3.60 an hour
DEFAULT_BUDGET = 10.0    # USD a month unless the owner sets another cap in the Video tab
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


# ── the engine (runs inside worker.py) ──────────────────────────────────────
class Engine:
    """One per worker. `sb` is worker.py's Supabase client; owner() returns who pressed Start."""

    def __init__(self, sb, sb_url: str, owner, log=print, notify=None, llm=None):
        self.sb, self.sb_url, self.owner, self.log = sb, sb_url.rstrip("/"), owner, log
        self.notify = notify          # notify(title, body) -> phone push
        self.llm = llm                # llm(prompt) -> text, or None
        self.lock = threading.RLock()
        self.jobs: list[dict] = []
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
        jobs, stats = [], None
        try:
            if self.sb.download(self._path("jobs.json"), tmp, bucket=FILES_BUCKET):
                with open(tmp) as f:
                    d = json.load(f)
                jobs, stats = d.get("jobs") or [], d.get("stats")
        except Exception as exc:
            self.log("video: jobs.json unreadable:", str(exc)[:120])
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        if stats and not os.path.exists(STATS_FILE):
            save_stats(stats)  # the estimate's learning survives a fresh machine
        with self.lock:
            self.jobs, self.loaded_for = jobs, owner

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
        busy = any(j.get("status") in ("queued", "rendering") for j in self.jobs)
        gpu = self.gpu() if busy or force else None
        with self.lock:
            self.dirty_at = time.time()
            self.saved_at = time.time()
            body = {"v": 1, "at": time.time(), "jobs": self.jobs[-300:], "stats": load_stats(),
                    "rate_usd_s": round(USD_PER_SEC, 6), "budget": self.budget(), "spent": self.spent(),
                    **({"gpu": gpu} if gpu else {})}
            data = json.dumps(body, separators=(",", ":")).encode()
        try:
            self._put("jobs.json", data, "application/json")
            self.dirty_at = 0.0
        except Exception as exc:
            self.log("video: save failed:", str(exc)[:160])

    def flusher(self):
        while not self.stop.wait(1.0):
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
        v = self.settings().get("videoBudget")
        try:
            return float(v) if v is not None else DEFAULT_BUDGET
        except (TypeError, ValueError):
            return DEFAULT_BUDGET

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
                "committed": round(self.committed(), 3), "rate_usd_h": round(USD_PER_SEC * 3600, 2)}

    def submit(self, args: dict, source: str = "tab") -> dict:
        prompt = clean_prompt(str(args.get("prompt") or ""))
        if not prompt:
            raise ValueError("The prompt is empty.")
        if not has_fields(prompt):  # a plain idea typed straight in: wrap it, don't refuse it
            prompt = template_prompt(prompt, float(args.get("secs") or 5), str(args.get("style") or ""),
                                     args.get("mode") or "clip")
        info = self.estimate(args)
        budget, spent, committed = info["budget"], info["spent"], info["committed"]
        if budget >= 0 and spent + committed + info["total_high"] > budget:
            raise ValueError(f"Over your monthly video budget: ${spent + committed:.2f} of ${budget:.2f} used or booked, "
                             f"this needs up to ${info['total_high']:.2f}. Raise the budget in the Video tab.")
        base_seed = int(args.get("seed") or secrets.randbelow(2**31))
        group = secrets.token_hex(4)
        made = []
        for k in range(info["seeds"]):
            jid = time.strftime("%y%m%d-%H%M%S-") + secrets.token_hex(2)
            job = {"id": jid, "group": group, "created": time.time(), "source": source,
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
        if self.notify:
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

    # chat agent requests (pvideo)
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
                out = self.submit(req, source="chat")
                res = {"ok": True, "jobs": [j["id"] for j in out["jobs"]], "total_usd": out["total_usd"]}
            except Exception as exc:
                res = {"ok": False, "error": str(exc)[:400]}
            with open(os.path.join(STATUS, f"req-{rid}.json"), "w") as f:
                json.dump(res, f)


def safe_title(job: dict) -> str:
    t = re.sub(r"[^\w\- ]+", "", job.get("title") or job.get("idea") or "video").strip()[:40] or "video"
    return re.sub(r"\s+", "-", t).lower() + f"-{job.get('quality', '')}-{job['id'][-4:]}"


# ── pvideo: the chat agent's command ────────────────────────────────────────
def _cli():
    ap = argparse.ArgumentParser(prog="pvideo", description="Make videos on the owner's Modal GPU (MiniMax H3).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("estimate", "make"):
        s = sub.add_parser(name)
        s.add_argument("--prompt", help="H3 3-field prompt, or a file path holding it")
        s.add_argument("--idea", default="")
        s.add_argument("--mode", default="clip", choices=MODES)
        s.add_argument("--quality", default="draft", choices=tuple(QUALITY))
        s.add_argument("--ratio", default="9:16", choices=("9:16", "16:9", "1:1"))
        s.add_argument("--secs", type=float, default=5)
        s.add_argument("--seeds", type=int, default=1)
        s.add_argument("--first", default="", help="start picture (local path)")
        s.add_argument("--last", default="", help="end picture (local path)")
        s.add_argument("--style", default="", choices=("",) + tuple(STYLES))
        s.add_argument("--title", default="")
        s.add_argument("--wait", action="store_true", help="wait and copy the clips to ~/outputs")
    st = sub.add_parser("status")
    st.add_argument("ids", nargs="*")
    a = ap.parse_args()

    if a.cmd == "status":
        ids = a.ids or sorted((n[:-5] for n in os.listdir(STATUS) if not n.startswith("req-")), reverse=True)[:5]
        for jid in ids:
            try:
                with open(os.path.join(STATUS, jid + ".json")) as f:
                    j = json.load(f)
                print(f"{jid}: {j.get('status')} {j.get('stage')} {j.get('pct', 0)}% cost ${j.get('cost') or 0:.2f}"
                      f"{' — ' + j['error'] if j.get('error') else ''}")
            except FileNotFoundError:
                print(f"{jid}: unknown")
        return

    prompt = a.prompt or ""
    if prompt and os.path.exists(prompt):
        with open(prompt) as f:
            prompt = f.read()
    spec = {"prompt": prompt or a.idea, "idea": a.idea or prompt[:200], "mode": a.mode, "quality": a.quality,
            "ratio": a.ratio, "secs": a.secs, "seeds": a.seeds, "first": os.path.abspath(a.first) if a.first else "",
            "last": os.path.abspath(a.last) if a.last else "", "style": a.style, "title": a.title,
            "chat_id": os.environ.get("PANDA_CHAT_ID") or None}
    if a.cmd == "estimate":
        p = plan(spec)
        e1, e2 = estimate(p), estimate(p, warm=True)
        n = min(4, max(1, a.seeds))
        print(json.dumps({"size": f"{p['w']}x{p['h']}", "frames": p["frames"], "steps": p["steps"],
                          "usd": round(e1["usd"] + e2["usd"] * (n - 1), 3),
                          "usd_high": round(e1["usd_high"] + e2["usd_high"] * (n - 1), 3),
                          "secs": e1["secs"] + e2["secs"] * (n - 1)}))
        return
    if not os.path.isdir(SPOOL):
        sys.exit("The video engine isn't running (Panda must be on).")
    rid = secrets.token_hex(6)
    with open(os.path.join(SPOOL, f"{rid}.json"), "w") as f:
        json.dump({**spec, "request_id": rid}, f)
    res_path = os.path.join(STATUS, f"req-{rid}.json")
    for _ in range(60):
        if os.path.exists(res_path):
            break
        time.sleep(1)
    else:
        sys.exit("The video engine didn't pick the job up.")
    with open(res_path) as f:
        res = json.load(f)
    if not res.get("ok"):
        sys.exit(res.get("error") or "refused")
    print(f"queued {', '.join(res['jobs'])} · about ${res['total_usd']:.2f} · watch it in the Video tab")
    if not a.wait:
        return
    pending = set(res["jobs"])
    while pending:
        time.sleep(4)
        for jid in list(pending):
            try:
                with open(os.path.join(STATUS, jid + ".json")) as f:
                    j = json.load(f)
            except (FileNotFoundError, ValueError):
                continue
            if j.get("status") in ("done", "failed", "cancelled"):
                pending.discard(jid)
                if j.get("status") == "done" and j.get("local") and os.path.exists(j["local"]):
                    os.makedirs(OUTPUTS, exist_ok=True)
                    dest = os.path.join(OUTPUTS, safe_title(j) + ".mp4")
                    shutil.copy(j["local"], dest)
                    os.remove(j["local"])  # the copy in ~/outputs is the one the chat shows
                    print(f"done {jid}: {dest} · ${j.get('cost') or 0:.2f}")
                else:
                    print(f"{j.get('status')} {jid}: {j.get('error', '')}")


if __name__ == "__main__":
    _cli()
