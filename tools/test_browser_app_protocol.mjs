// Does the real app's Realtime client (supabase-js, protocol 2.0.0 with binary broadcast pushes)
// reach the browser engine (Python, protocol 1.0.0) and back? Plays the phone with the exact client
// library the app ships, against a running engine on the same private topic.
//
//   node tools/test_browser_app_protocol.mjs <supabase-url> <publishable-key> <topic>
// Run from app/ so `@supabase/supabase-js` resolves (see tools/test_browser_app.sh).
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";

// Resolve the app's own copy of supabase-js (run from app/).
const require = createRequire(pathToFileURL(process.cwd() + "/"));
const { createClient } = await import(pathToFileURL(require.resolve("@supabase/supabase-js")).href);

const [url, key, topic] = process.argv.slice(2);
const sb = createClient(url, key, { auth: { persistSession: false }, realtime: { params: { eventsPerSecond: 20 } } });
const got = { frames: 0, status: [], actions: 0, lastControl: "" };
const ch = sb.channel(topic, { config: { broadcast: { self: false, ack: false } } });
ch.on("broadcast", { event: "frame" }, ({ payload }) => { got.frames++; got.lastFrameKB = Math.round((payload?.d?.length || 0) * 0.75 / 1024); });
ch.on("broadcast", { event: "status" }, ({ payload }) => { got.status.push(payload); got.lastControl = payload.control; });
ch.on("broadcast", { event: "action" }, () => { got.actions++; });
const send = (payload) => ch.send({ type: "broadcast", event: "input", payload });
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

await new Promise((resolve, reject) => {
  ch.subscribe((s) => { if (s === "SUBSCRIBED") resolve(); else if (s === "CHANNEL_ERROR" || s === "TIMED_OUT") reject(new Error(s)); });
});
await wait(800);
await send({ t: "hello", mode: "desktop", vw: 0, vh: 0 });
await wait(3000);
const r = { subscribed: true, frames_after_hello: got.frames, status_events: got.status.length, frame_kb: got.lastFrameKB };
await send({ t: "takeover" });
await wait(2000);
r.control_after_takeover = got.lastControl;
await send({ t: "nav", url: "https://example.org" });
await wait(4000);
r.url_after_nav = got.status.at(-1)?.url;
await send({ t: "done" });
await wait(1500);
r.control_after_done = got.lastControl;
console.log(JSON.stringify(r));
await sb.removeChannel(ch);
process.exit(0);
