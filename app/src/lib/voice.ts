import { useEffect, useRef, useState } from "react";
import { runCommand } from "./commands";

/**
 * Voice input. Two ways, picked per tap:
 *  - "record": the phone records a short clip, the worker turns it into text with a local Whisper
 *    model (free, works in every browser, also iPhone home-screen apps).
 *  - "browser": the browser's own speech recognition, used only while Panda is offline.
 */
export type VoiceState = "idle" | "recording" | "transcribing" | "listening";

const MAX_SECS = 180;
const LANGS: Record<string, string> = { en: "en-US", it: "it-IT", uk: "uk-UA" };

type SR = { start: () => void; stop: () => void; abort: () => void; lang: string; interimResults: boolean;
  continuous: boolean; onresult: unknown; onerror: unknown; onend: unknown };
const SRCtor = (): (new () => SR) | undefined => {
  const w = window as unknown as { SpeechRecognition?: new () => SR; webkitSpeechRecognition?: new () => SR };
  return w.SpeechRecognition || w.webkitSpeechRecognition;
};
const canRecord = () => typeof window !== "undefined" && !!navigator.mediaDevices?.getUserMedia && typeof MediaRecorder !== "undefined";

function pickMime() {
  for (const m of ["audio/webm;codecs=opus", "audio/mp4", "audio/ogg;codecs=opus", "audio/webm"]) {
    if (MediaRecorder.isTypeSupported?.(m)) return m;
  }
  return "";
}

function micError(e: unknown) {
  const name = (e as { name?: string; error?: string })?.name || (e as { error?: string })?.error || "";
  if (name === "NotAllowedError" || name === "not-allowed" || name === "service-not-allowed" || name === "SecurityError")
    return "Panda can't use the microphone. Allow it in your browser or phone settings.";
  if (name === "NotFoundError" || name === "audio-capture") return "No microphone was found.";
  if (name === "NotReadableError") return "Another app is using the microphone.";
  if (name === "network") return "Voice needs Panda to be running. Press Start, then try again.";
  if (name === "no-speech") return "I didn't hear anything. Try again a bit closer.";
  return "The microphone didn't start. Try again.";
}

const toBase64 = (b: Blob) => new Promise<string>((ok, bad) => {
  const r = new FileReader();
  r.onload = () => ok(String(r.result).split(",")[1] || "");
  r.onerror = () => bad(r.error);
  r.readAsDataURL(b);
});

/**
 * `onText(said, final, send)`: `said` is everything heard since the tap (interim results replace it);
 * `send` is set when the user tapped the send arrow instead of the stop square.
 * `onCancel`: the user tapped X; the box goes back to what it held before.
 */
export function useVoice({ online, language, onText, onNotice, onCancel }: {
  online: boolean; language: string; onText: (said: string, final: boolean, send: boolean) => void;
  onNotice: (msg: string) => void; onCancel?: () => void;
}) {
  const [state, setState] = useState<VoiceState>("idle");
  const [secs, setSecs] = useState(0);
  const rec = useRef<MediaRecorder | null>(null);
  const stream = useRef<MediaStream | null>(null);
  const sr = useRef<SR | null>(null);
  const timer = useRef<number>(0);
  const cancelled = useRef(false);   // unmounted
  const discard = useRef(false);     // this take was cancelled with X
  const sendAfter = useRef(false);   // this take ends with "send"
  const take = useRef(0);            // which take is current; older answers are ignored
  const analyser = useRef<{ ctx: AudioContext; node: AnalyserNode; buf: Uint8Array<ArrayBuffer> } | null>(null);
  const cb = useRef({ onText, onNotice, onCancel });
  useEffect(() => { cb.current = { onText, onNotice, onCancel }; });

  const available = canRecord() || !!SRCtor();

  const release = () => {
    window.clearInterval(timer.current);
    stream.current?.getTracks().forEach((t) => t.stop());
    stream.current = null;
    analyser.current?.ctx.close().catch(() => {});
    analyser.current = null;
  };

  /** How loud the mic is right now, 0..1 (0 when there is no live mic, e.g. browser recognition). */
  const level = () => {
    const a = analyser.current;
    if (!a) return 0;
    a.node.getByteTimeDomainData(a.buf);
    let sum = 0;
    for (let i = 0; i < a.buf.length; i++) { const v = (a.buf[i] - 128) / 128; sum += v * v; }
    return Math.min(1, Math.sqrt(sum / a.buf.length) * 4.2);
  };
  useEffect(() => () => {
    cancelled.current = true;
    if (rec.current?.state === "recording") rec.current.stop();
    sr.current?.abort();
    release();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const transcribe = async (blob: Blob, mime: string, id: number) => {
    if (blob.size < 1200) { setState("idle"); cb.current.onNotice("That was too short. Hold on a little longer."); return; }
    setState("transcribing");
    try {
      const audio = await toBase64(blob);
      const r = await runCommand<{ text: string }>("stt", { audio, mime, lang: language }, 170000);
      const said = (r.text || "").trim();
      if (discard.current || id !== take.current) return;
      if (said) cb.current.onText(said, true, sendAfter.current);
      else cb.current.onNotice("I didn't catch any words. Try again.");
    } catch (e) {
      if (id === take.current && !discard.current) cb.current.onNotice(`Voice didn't work: ${(e as Error).message || "unknown error"}`);
    } finally {
      if (!cancelled.current && id === take.current) setState("idle");
    }
  };

  const startRecording = async () => {
    let s: MediaStream;
    try {
      s = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
    } catch (e) { cb.current.onNotice(micError(e)); return; }
    stream.current = s;
    try {
      const AC = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
      const ctx = new AC();
      const node = ctx.createAnalyser();
      node.fftSize = 1024;
      ctx.createMediaStreamSource(s).connect(node);
      analyser.current = { ctx, node, buf: new Uint8Array(node.fftSize) };
    } catch { /* no level meter; recording still works */ }
    const mime = pickMime();
    let r: MediaRecorder;
    try {
      r = new MediaRecorder(s, { ...(mime ? { mimeType: mime } : {}), audioBitsPerSecond: 32000 });
    } catch (e) { release(); cb.current.onNotice(micError(e)); return; }
    const chunks: Blob[] = [];
    const id = ++take.current;
    r.ondataavailable = (ev) => { if (ev.data.size) chunks.push(ev.data); };
    r.onstop = () => {
      release();
      if (cancelled.current) return;
      if (discard.current) { setState("idle"); return; }
      const type = r.mimeType || mime || "audio/webm";
      transcribe(new Blob(chunks, { type }), type, id);
    };
    discard.current = false;
    sendAfter.current = false;
    r.start(1000);
    rec.current = r;
    setSecs(0);
    setState("recording");
    const t0 = Date.now();
    timer.current = window.setInterval(() => {
      const n = Math.floor((Date.now() - t0) / 1000);
      setSecs(n);
      if (n >= MAX_SECS && r.state === "recording") r.stop();
    }, 250);
  };

  const startBrowser = () => {
    const Ctor = SRCtor()!;
    let r: SR;
    try { r = new Ctor(); } catch (e) { cb.current.onNotice(micError(e)); return; }
    r.lang = LANGS[language] || navigator.language || "en-US";
    r.interimResults = true;
    r.continuous = true;
    let heard = false;
    let last = "";
    discard.current = false;
    sendAfter.current = false;
    r.onresult = (e: { results: ArrayLike<ArrayLike<{ transcript: string }> & { isFinal?: boolean }> }) => {
      let said = "";
      for (let i = 0; i < e.results.length; i++) said += e.results[i][0].transcript;
      heard = heard || !!said.trim();
      last = said;
      if (!discard.current) cb.current.onText(said, false, false);
    };
    r.onerror = (e: { error?: string }) => { if (e.error !== "aborted") cb.current.onNotice(micError(e)); };
    r.onend = () => {
      sr.current = null;
      setState("idle");
      if (heard && !discard.current) cb.current.onText(last, true, sendAfter.current);
    };
    try { r.start(); } catch (e) { cb.current.onNotice(micError(e)); return; }
    sr.current = r;
    setState("listening");
  };

  /** Stop listening and turn it into text; with `send`, it is sent right after. */
  const finish = (send = false) => {
    sendAfter.current = send;
    if (state === "recording" && rec.current?.state === "recording") rec.current.stop();
    else if (state === "listening") sr.current?.stop();
  };

  /** Throw this take away. A clip already being turned into text is dropped when it comes back. */
  const cancel = () => {
    discard.current = true;
    take.current++;
    if (rec.current?.state === "recording") rec.current.stop();
    sr.current?.abort();
    setState("idle");
    cb.current.onCancel?.();
  };

  const toggle = () => {
    if (state === "recording" || state === "listening") { finish(false); return; }
    if (state === "transcribing") return;
    if (canRecord() && (online || !SRCtor())) {
      if (!online) { cb.current.onNotice("Voice needs Panda to be running. Press Start, then try again."); return; }
      startRecording();
      return;
    }
    if (SRCtor()) { startBrowser(); return; }
    cb.current.onNotice("This browser can't use the microphone.");
  };

  return { state, secs, available, toggle, finish, cancel, level, metered: () => !!analyser.current };
}
