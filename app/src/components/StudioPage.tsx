import { useCallback, useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import {
  blenderInstall, blenderJob, blenderRender, blenderStatus, downloadFile, fetchWorkFile,
  type BlenderStatus, type RenderJob,
} from "../lib/api";
import { StartNote } from "./StartNote";

const KEY = "panda.studio.script";
const DEFAULT_SCRIPT = `import bpy

# Floor
bpy.ops.mesh.primitive_plane_add(size=12, location=(0, 0, 0))
floor = bpy.context.object
fm = bpy.data.materials.new("Floor"); fm.use_nodes = True
fm.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (0.12, 0.13, 0.16, 1)
floor.data.materials.append(fm)

# Metal sphere
bpy.ops.mesh.primitive_uv_sphere_add(radius=1, location=(0, 0, 1))
sph = bpy.context.object
bpy.ops.object.shade_smooth()
m = bpy.data.materials.new("Metal"); m.use_nodes = True
b = m.node_tree.nodes["Principled BSDF"]
b.inputs["Base Color"].default_value = (0.9, 0.55, 0.2, 1)
b.inputs["Metallic"].default_value = 1.0
b.inputs["Roughness"].default_value = 0.2
sph.data.materials.append(m)

# Ring around it
bpy.ops.mesh.primitive_torus_add(major_radius=1.8, minor_radius=0.18, location=(0, 0, 1))
ring = bpy.context.object
bpy.ops.object.shade_smooth()
rm = bpy.data.materials.new("Ring"); rm.use_nodes = True
rm.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (0.2, 0.5, 0.95, 1)
ring.data.materials.append(rm)
`;

function Viewer({ url }: { url: string }) {
  const host = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = host.current;
    if (!el) return;
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x101218);
    const camera = new THREE.PerspectiveCamera(45, 1, 0.01, 1000);
    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    el.appendChild(renderer.domElement);
    scene.add(new THREE.HemisphereLight(0xffffff, 0x333344, 1.4));
    const sun = new THREE.DirectionalLight(0xffffff, 2.2);
    sun.position.set(4, 8, 5);
    scene.add(sun);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    const size = () => {
      const w = el.clientWidth, h = el.clientHeight || 360;
      renderer.setSize(w, h);
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
    };
    size();
    const ro = new ResizeObserver(size);
    ro.observe(el);
    let raf = 0, dead = false;
    new GLTFLoader().load(url, (g) => {
      if (dead) return;
      scene.add(g.scene);
      const box = new THREE.Box3().setFromObject(g.scene);
      const c = box.getCenter(new THREE.Vector3());
      const r = Math.max(box.getSize(new THREE.Vector3()).length() / 2, 0.1);
      controls.target.copy(c);
      camera.position.copy(c).add(new THREE.Vector3(1, 0.7, 1).normalize().multiplyScalar(r * 2.6));
      camera.near = r / 100; camera.far = r * 100;
      camera.updateProjectionMatrix();
    }, undefined, () => {});
    const loop = () => { raf = requestAnimationFrame(loop); controls.update(); renderer.render(scene, camera); };
    loop();
    return () => {
      dead = true;
      cancelAnimationFrame(raf);
      ro.disconnect();
      controls.dispose();
      scene.traverse((o) => {
        const m = o as THREE.Mesh;
        m.geometry?.dispose();
        const mat = m.material;
        (Array.isArray(mat) ? mat : mat ? [mat] : []).forEach((x) => x.dispose());
      });
      renderer.dispose();
      renderer.domElement.remove();
    };
  }, [url]);
  return <div ref={host} className="studio-viewer" />;
}

/** Write a Blender script, render it on the worker, and look at the picture or turn the model around. */
export function StudioPage({ online }: { online: boolean }) {
  const [status, setStatus] = useState<BlenderStatus | null>(null);
  const [script, setScript] = useState(() => { try { return localStorage.getItem(KEY) || DEFAULT_SCRIPT; } catch { return DEFAULT_SCRIPT; } });
  const [name, setName] = useState("scene");
  const [w, setW] = useState(960);
  const [h, setH] = useState(540);
  const [samples, setSamples] = useState(24);
  const [job, setJob] = useState<RenderJob | null>(null);
  const [busy, setBusy] = useState(false);
  const [secs, setSecs] = useState(0);
  const [err, setErr] = useState("");
  const [tab, setTab] = useState<"render" | "model">("render");
  const [imgUrl, setImgUrl] = useState("");
  const [modelUrl, setModelUrl] = useState("");

  useEffect(() => { try { localStorage.setItem(KEY, script); } catch { /* blocked */ } }, [script]);

  const refresh = useCallback(() => blenderStatus().then(setStatus).catch((e) => setErr(e.message)), []);
  useEffect(() => { if (online) refresh(); }, [online, refresh]);
  useEffect(() => {
    if (!status?.installing) return;
    const t = setInterval(refresh, 4000);
    return () => clearInterval(t);
  }, [status?.installing, refresh]);

  useEffect(() => {
    if (!busy) return;
    const t0 = Date.now();
    const t = setInterval(() => setSecs(Math.round((Date.now() - t0) / 1000)), 1000);
    return () => clearInterval(t);
  }, [busy]);

  // Load the finished render and model through the worker's login.
  useEffect(() => {
    let dead = false, url = "";
    if (!job?.image) { setImgUrl(""); return; }
    fetchWorkFile(job.image).then((b) => { if (dead) return; url = URL.createObjectURL(b); setImgUrl(url); }).catch((e) => setErr(e.message));
    return () => { dead = true; if (url) URL.revokeObjectURL(url); };
  }, [job?.image]);
  useEffect(() => {
    let dead = false, url = "";
    if (!job?.model) { setModelUrl(""); return; }
    fetchWorkFile(job.model).then((b) => { if (dead) return; url = URL.createObjectURL(b); setModelUrl(url); }).catch((e) => setErr(e.message));
    return () => { dead = true; if (url) URL.revokeObjectURL(url); };
  }, [job?.model]);

  const install = async () => {
    setErr("");
    try { await blenderInstall(); await refresh(); } catch (e) { setErr((e as Error).message); }
  };

  const render = async () => {
    setErr(""); setBusy(true); setSecs(0); setJob(null);
    try {
      const { id } = await blenderRender({ script, name, width: w, height: h, samples });
      for (;;) {
        await new Promise((r) => setTimeout(r, 2000));
        const j = await blenderJob(id);
        setJob(j);
        if (j.state !== "running") { if (j.state === "error") setErr(j.error || "Render failed"); else setTab("render"); break; }
      }
    } catch (e) { setErr((e as Error).message); }
    setBusy(false);
  };

  if (!online) return <div className="studio"><StartNote what="the 3D studio" /></div>;
  const num = (set: (n: number) => void) => (e: React.ChangeEvent<HTMLInputElement>) => set(Number(e.target.value) || 0);

  return (
    <div className="studio">
      {status && !status.ready && (
        <div className="studio-card">
          <b>Blender is not installed on the worker yet.</b>
          <p className="c-dim">It installs once per worker run and takes a few minutes.</p>
          <button className="btn primary" onClick={install} disabled={status.installing}>{status.installing ? "Installing…" : "Install Blender"}</button>
          {status.log && <pre className="studio-log">{status.log}</pre>}
          {status.error && <p className="studio-err">{status.error}</p>}
        </div>
      )}
      <div className="studio-cols">
        <div className="studio-col">
          <textarea className="studio-code" value={script} spellCheck={false} onChange={(e) => setScript(e.target.value)} />
          <div className="studio-row">
            <label>Name <input value={name} onChange={(e) => setName(e.target.value)} /></label>
            <label>Width <input type="number" value={w} onChange={num(setW)} /></label>
            <label>Height <input type="number" value={h} onChange={num(setH)} /></label>
            <label>Samples <input type="number" value={samples} onChange={num(setSamples)} /></label>
          </div>
          <div className="studio-row">
            <button className="btn primary" onClick={render} disabled={busy || !status?.ready}>{busy ? `Rendering… ${secs}s` : "Render"}</button>
            <button className="btn ghost" onClick={() => setScript(DEFAULT_SCRIPT)} disabled={busy}>Reset script</button>
          </div>
          {err && <p className="studio-err">{err}</p>}
          {job?.log && <details><summary>Render log</summary><pre className="studio-log">{job.log}</pre></details>}
        </div>
        <div className="studio-col">
          <div className="studio-tabs">
            <button className={tab === "render" ? "active" : ""} onClick={() => setTab("render")}>Render</button>
            <button className={tab === "model" ? "active" : ""} onClick={() => setTab("model")}>3D preview</button>
          </div>
          {tab === "render" ? (
            imgUrl ? <img className="studio-img" src={imgUrl} alt="Render" /> : <p className="c-dim">Press Render to see the picture here.</p>
          ) : modelUrl ? <Viewer url={modelUrl} /> : <p className="c-dim">Render once, then drag to turn the model around.</p>}
          {job?.state === "done" && (
            <div className="studio-row">
              {job.image && <button className="btn" onClick={() => downloadFile(job.image!)}>PNG</button>}
              {job.model && <button className="btn" onClick={() => downloadFile(job.model!)}>GLB</button>}
              {job.blend && <button className="btn" onClick={() => downloadFile(job.blend!)}>.blend</button>}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
