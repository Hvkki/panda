"""Panda's video renderer on Modal: MiniMax H3 in headless ComfyUI on one RTX PRO 6000.

The worker calls the DEPLOYED app (never `modal run`), so every chat and the Video tab share one
queue: `max_containers=1` means one GPU at a time, ever, and jobs run one after the other.

  modal deploy h3_app.py                 # after a change to this file
  modal run h3_app.py::fill_volume       # once: weights -> Volume h3-weights (CPU only, ~5 min)
  modal run h3_app.py::validate          # CPU only: ComfyUI checks the graphs, no sampling

Why it is cheap:
  * Weights live on the Volume, filled once by a CPU box. A GPU never downloads them.
  * ComfyUI starts once per container (@modal.enter) and keeps the models in VRAM (--highvram).
    A job that lands on a warm container pays only its own sampling time.
  * A failed job stops the container right after it (no idle GPU), and every job has a hard
    time cap besides Modal's 20-minute timeout.
  * The GPU waits only 20 s for the next job. Seeds and back-to-back jobs are already queued, so
    they still share one warm container; an idle 2-minute wait cost more ($0.12) than a cold start.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid

import modal

APP_NAME = "panda-h3"
GPU = "RTX-PRO-6000"
MODELS = "/models"
COMFY = "/comfy"
COMFY_COMMIT = "426f5ee"  # ComfyUI with the MiniMax H3 nodes (comfy_extras/nodes_minimax_h3.py)
VOL = modal.Volume.from_name("h3-weights", create_if_missing=True)
# Live progress for the worker: <job id> -> {stage, step, steps, t}; "_container" -> heartbeat.
PROGRESS = modal.Dict.from_name("panda-h3-progress", create_if_missing=True)

FILES = {  # key: (Hugging Face repo, path in repo, ComfyUI folder)
    "unet": ("Comfy-Org/MiniMax-H3", "diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors",
             "diffusion_models"),
    "te": ("rzgar/qwen3-vl-32b-minimax-h3-fp8-comfyui", "qwen3-vl-32b-minimax-h3-fp8-comfyui.safetensors",
           "text_encoders"),
    "vvae": ("Comfy-Org/MiniMax-H3", "vae/minimax_h3_video_vae_fp16.safetensors", "vae"),
    "avae": ("Comfy-Org/MiniMax-H3", "vae/minimax_h3_audio_vae_fp32.safetensors", "vae"),
    "turbo8": ("Comfy-Org/MiniMax-H3", "loras/minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors", "loras"),
    "orbit": ("pablodawson/MiniMax-H3-360-Orbit-LoRA", "minimax_h3_flf2v_lora_v1.safetensors", "loras"),
}
NAME = {k: os.path.basename(v[1]) for k, v in FILES.items()}
PATHS_YAML = (f"h3:\n  base_path: {MODELS}\n  diffusion_models: diffusion_models\n  text_encoders: text_encoders\n"
              f"  vae: vae\n  loras: loras\n")

dl_image = modal.Image.debian_slim(python_version="3.12").pip_install("huggingface_hub[hf_xet]==0.36.0")
gpu_image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("git", "ffmpeg")
    .pip_install("huggingface_hub[hf_xet]==0.36.0")
    .pip_install("torch==2.10.0", "torchvision", "torchaudio", index_url="https://download.pytorch.org/whl/cu130")
    .run_commands(
        f"git clone https://github.com/comfyanonymous/ComfyUI {COMFY} && cd {COMFY} && git checkout {COMFY_COMMIT}",
        f"pip install -r {COMFY}/requirements.txt",
        f"grep -q MiniMaxH3ImageToVideo {COMFY}/comfy_extras/nodes_minimax_h3.py",
    )
    .pip_install("websocket-client==1.8.0")
)

app = modal.App(APP_NAME)


# ── the ComfyUI graph ───────────────────────────────────────────────────────
def graph(job: dict) -> dict:
    """API-format graph for text/first/last-frame to video with audio (T2VA, I2VA, FL2VA).

    job: prompt, w, h, frames, steps, seed, loras [[key, strength]], first/last (input file names),
    audio (bool). Sampling shift is H3's default (video 12, audio 3), set by the model config.
    """
    model = ["1", 0]
    g = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": NAME["unet"], "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": NAME["te"], "type": "minimax", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": NAME["vvae"]}},
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": NAME["avae"]}},
    }
    for i, (key, strength) in enumerate(job.get("loras") or []):
        nid = str(20 + i)
        g[nid] = {"class_type": "LoraLoaderModelOnly",
                  "inputs": {"model": model, "lora_name": NAME[key], "strength_model": float(strength)}}
        model = [nid, 0]
    frames = {}
    if job.get("first"):
        g["30"] = {"class_type": "LoadImage", "inputs": {"image": job["first"]}}
        frames["first_frame"] = ["30", 0]
    if job.get("last"):
        g["31"] = {"class_type": "LoadImage", "inputs": {"image": job["last"]}}
        frames["last_frame"] = ["31", 0]
    g.update({
        "5": {"class_type": "MiniMaxH3ImageToVideo", "inputs": {
            "clip": ["2", 0], "vae": ["3", 0], "prompt": job["prompt"],
            "width": int(job["w"]), "height": int(job["h"]), "length": int(job["frames"]), **frames}},
        "6": {"class_type": "RandomNoise", "inputs": {"noise_seed": int(job["seed"])}},
        "7": {"class_type": "BasicGuider", "inputs": {"model": model, "conditioning": ["5", 0]}},
        "8": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "res_multistep"}},
        "9": {"class_type": "BasicScheduler", "inputs": {"model": model, "scheduler": "simple",
                                                         "steps": int(job["steps"]), "denoise": 1.0}},
        "10": {"class_type": "SamplerCustomAdvanced", "inputs": {
            "noise": ["6", 0], "guider": ["7", 0], "sampler": ["8", 0], "sigmas": ["9", 0], "latent_image": ["5", 1]}},
        "11": {"class_type": "VAEDecode", "inputs": {"samples": ["10", 0], "vae": ["3", 0]}},
        "12": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["10", 0], "vae": ["4", 0]}},
        "13": {"class_type": "CreateVideo", "inputs": {"images": ["11", 0], "fps": 24,
                                                       **({"audio": ["12", 0]} if job.get("audio", True) else {})}},
        "14": {"class_type": "SaveVideo", "inputs": {"video": ["13", 0], "filename_prefix": f"panda/{job['id']}",
                                                     "format": "auto", "format.codec": "auto"}},
    })
    return g


STAGES = {"1": "loading", "2": "loading", "3": "loading", "4": "loading", "5": "reading prompt",
          "10": "sampling", "11": "decoding", "12": "decoding", "13": "saving", "14": "saving"}


def _start_comfy(extra: list) -> subprocess.Popen:
    with open(f"{COMFY}/extra_model_paths.yaml", "w") as f:
        f.write(PATHS_YAML)
    srv = subprocess.Popen(["python", "main.py", "--listen", "127.0.0.1", "--port", "8188",
                            "--disable-auto-launch"] + extra, cwd=COMFY)
    for _ in range(900):
        if srv.poll() is not None:
            raise RuntimeError("ComfyUI exited while starting")
        try:
            urllib.request.urlopen("http://127.0.0.1:8188/system_stats", timeout=5)
            return srv
        except Exception:
            time.sleep(1)
    raise RuntimeError("ComfyUI did not start")


def _submit(g: dict, client_id: str = "") -> str:
    body = json.dumps({"prompt": g, "client_id": client_id}).encode()
    req = urllib.request.Request("http://127.0.0.1:8188/prompt", body, {"Content-Type": "application/json"})
    try:
        return json.load(urllib.request.urlopen(req, timeout=60))["prompt_id"]
    except urllib.error.HTTPError as e:
        raise RuntimeError("ComfyUI refused the graph: " + e.read().decode()[:2000])


def _interrupt():
    try:
        urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:8188/interrupt", b"", {}), timeout=10)
    except Exception:
        pass


@app.cls(image=gpu_image, gpu=GPU, volumes={MODELS: VOL}, cpu=4.0, memory=49152,
         timeout=20 * 60, scaledown_window=20, max_containers=1)
class H3:
    @modal.enter()
    def start(self):
        self.up = time.time()
        self.jobs = 0
        self.busy = ""
        self.gpu = subprocess.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                                  capture_output=True, text=True).stdout.strip()
        self._beat("starting")
        threading.Thread(target=self._heartbeat, daemon=True).start()
        self.srv = _start_comfy(["--highvram"])
        self.ready = time.time()
        self._beat("ready")
        print(f"ComfyUI up in {self.ready - self.up:.0f}s on {self.gpu}")

    def _beat(self, state: str = ""):
        try:
            PROGRESS.put("_container", {"t": time.time(), "up": self.up, "busy": self.busy, "jobs": self.jobs,
                                        "gpu": self.gpu, "state": state or ("busy" if self.busy else "idle")})
        except Exception:
            pass

    def _heartbeat(self):
        while True:
            time.sleep(5)
            self._beat()

    @modal.method()
    def render(self, job: dict) -> dict:
        """One clip. job: id, prompt, w, h, frames, steps, seed, loras, first_png/last_png (bytes),
        audio, max_secs. Returns {video (mp4 bytes), timings, gpu, cold}."""
        import websocket  # websocket-client

        jid = str(job["id"])
        t0 = time.time()
        cold = self.jobs == 0
        self.busy, self.jobs = jid, self.jobs + 1
        times = {"container_up": round(self.ready - self.up, 1), "stages": {}}
        state = {"stage": "starting", "step": 0, "steps": int(job["steps"]), "t": t0, "started": t0, "cold": cold}

        def report(force=False, **kw):
            state.update(kw)
            now = time.time()
            if force or now - state.get("_sent", 0) > 1.0:
                state["_sent"] = now
                try:
                    PROGRESS.put(jid, {k: v for k, v in state.items() if not k.startswith("_")} | {"t": now})
                except Exception:
                    pass

        try:
            report(force=True)
            os.makedirs(f"{COMFY}/input", exist_ok=True)
            g_job = dict(job)
            for side in ("first", "last"):
                data = job.get(f"{side}_png")
                if data:
                    name = f"{jid}-{side}.png"
                    with open(f"{COMFY}/input/{name}", "wb") as f:
                        f.write(data)
                    g_job[side] = name
            client = uuid.uuid4().hex
            ws = websocket.create_connection(f"ws://127.0.0.1:8188/ws?clientId={client}", timeout=5)
            pid = _submit(graph(g_job), client)
            max_secs = float(job.get("max_secs") or 18 * 60)
            stage_t, stage = time.time(), "queued"
            done, error = False, ""
            while not done:
                if time.time() - t0 > max_secs:
                    _interrupt()
                    raise TimeoutError(f"stopped at the job's time cap ({max_secs:.0f}s)")
                if self.srv.poll() is not None:
                    raise RuntimeError("ComfyUI exited (out of memory?)")
                try:
                    raw = ws.recv()
                except websocket.WebSocketTimeoutException:
                    report()
                    continue
                if not isinstance(raw, str):
                    continue  # binary preview frames
                msg = json.loads(raw)
                kind, data = msg.get("type"), msg.get("data") or {}
                if data.get("prompt_id") not in (None, pid):
                    continue
                if kind == "executing":
                    node = data.get("node")
                    if node is None:
                        done = True
                        continue
                    new = STAGES.get(str(node), stage)
                    if new != stage:
                        times["stages"][stage] = round(times["stages"].get(stage, 0) + time.time() - stage_t, 1)
                        stage, stage_t = new, time.time()
                        report(force=True, stage=stage)
                elif kind == "progress" and stage == "sampling":
                    report(step=int(data.get("value", 0)), steps=int(data.get("max", job["steps"])))
                elif kind == "execution_error":
                    error = (data.get("exception_message") or "ComfyUI error")[:1500]
                    done = True
                elif kind == "execution_success":
                    done = True
            ws.close()
            times["stages"][stage] = round(times["stages"].get(stage, 0) + time.time() - stage_t, 1)
            if error:
                raise RuntimeError(error)
            hist = json.load(urllib.request.urlopen(f"http://127.0.0.1:8188/history/{pid}", timeout=30))[pid]
            outs = [v for o in hist["outputs"].values() for k, vs in o.items()
                    if k in ("images", "videos", "gifs") for v in vs]
            if not outs:
                raise RuntimeError("ComfyUI finished without a video")
            out = outs[0]
            path = os.path.join(f"{COMFY}/output", out.get("subfolder", ""), out["filename"])
            with open(path, "rb") as f:
                video = f.read()
            os.remove(path)
            times["total"] = round(time.time() - t0, 1)
            report(force=True, stage="done", step=int(job["steps"]))
            return {"video": video, "timings": times, "gpu": self.gpu, "cold": cold}
        except BaseException as exc:
            report(force=True, stage="failed", error=str(exc)[:500])
            # A failed job must not leave a GPU idling for the scale-down window.
            try:
                modal.experimental.stop_fetching_inputs()
            except Exception:
                pass
            raise
        finally:
            self.busy = ""
            self._beat()

    @modal.exit()
    def stop(self):
        try:
            PROGRESS.put("_container", {"t": time.time(), "state": "stopped", "gpu": self.gpu})
        except Exception:
            pass
        if getattr(self, "srv", None) and self.srv.poll() is None:
            self.srv.terminate()


# ── one-time and check helpers (CPU only) ───────────────────────────────────
@app.function(image=dl_image, volumes={MODELS: VOL}, cpu=8, memory=8192, timeout=3600,
              secrets=[modal.Secret.from_dict({"HF_TOKEN": os.environ.get("HF_TOKEN", "")})])
def fill_volume():
    """Download every model file into the Volume, once. Files already there are kept."""
    os.environ["HF_XET_HIGH_PERFORMANCE"] = "1"
    from huggingface_hub import hf_hub_download
    t0 = time.time()
    for repo, path, sub in FILES.values():
        dest = f"{MODELS}/{sub}/{os.path.basename(path)}"
        if os.path.exists(dest):
            print("have", dest)
            continue
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.move(hf_hub_download(repo, path, local_dir="/tmp/dl"), dest)  # /tmp -> Volume is cross-device
        VOL.commit()
        print(f"got {dest} at {time.time() - t0:.0f}s")


@app.function(image=gpu_image, cpu=2, memory=4096, timeout=900)
def validate(jobs: list | None = None) -> list:
    """ComfyUI checks each graph against empty placeholder files: wiring, node names, inputs."""
    for _, path, sub in FILES.values():
        p = f"{MODELS}/{sub}/{os.path.basename(path)}"
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "wb").close()
    srv = _start_comfy(["--cpu"])
    jobs = jobs or [
        {"id": "t2v", "prompt": "integrated_multimodal_description: test", "w": 1344, "h": 768, "frames": 124,
         "steps": 20, "seed": 1},
        {"id": "draft", "prompt": "x", "w": 768, "h": 1344, "frames": 73, "steps": 8, "seed": 2,
         "loras": [["turbo8", 0.8]], "audio": False},
        {"id": "fl2v", "prompt": "x", "w": 768, "h": 768, "frames": 73, "steps": 28, "seed": 3,
         "loras": [["orbit", 1.0]], "first": "check.png", "last": "check.png"},
    ]
    os.makedirs(f"{COMFY}/input", exist_ok=True)
    from PIL import Image
    Image.new("RGB", (64, 64), (120, 80, 40)).save(f"{COMFY}/input/check.png")
    out = []
    for job in jobs:
        try:
            _submit(graph(job))
            out.append(f"{job['id']}: ok")
            _interrupt()
        except Exception as e:
            out.append(f"{job['id']}: {e}")
    srv.terminate()
    return out


@app.local_entrypoint()
def main():
    print("\n".join(validate.remote()))
