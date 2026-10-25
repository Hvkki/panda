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

export function useVoice({ online, language, onText, onNotice }: {
  online: boolean; language: string; onText: (said: string, final: boolean) => void; onNotice: (msg: string) => void;
}) {
  const [state, setState] = useState<VoiceState>("idle");
  const [secs, setSecs] = useState(0);
  const rec = useRef<MediaRecorder | null>(null);
  const stream = useRef<MediaStream | null>(null);
  const sr = useRef<SR | null>(null);
  const timer = useRef<number>(0);
  const cancelled = useRef(false);
  const cb = useRef({ onText, onNotice });
  useEffect(() => { cb.current = { onText, onNotice }; });

  const available = canRecord() || !!SRCtor();

  const release = () => {
    window.clearInterval(timer.current);
    stream.current?.getTracks().forEach((t) => t.stop());
    stream.current = null;
  };
  useEffect(() => () => {
    cancelled.current = true;
    if (rec.current?.state === "recording") rec.current.stop();
    sr.current?.abort();
    release();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const transcribe = async (blob: Blob, mime: string) => {
    if (blob.size < 1200) { setState("idle"); cb.current.onNotice("That was too short. Hold on a little longer."); return; }
    setState("transcribing");
    try {
      const audio = await toBase64(blob);
      const r = await runCommand<{ text: string }>("stt", { audio, mime, lang: language }, 170000);
      const said = (r.text || "").trim();
      if (said) cb.current.onText(said, true);
      else cb.current.onNotice("I didn't catch any words. Try again.");
    } catch (e) {
      cb.current.onNotice(`Voice didn't work: ${(e as Error).message || "unknown error"}`);
    } finally {
      if (!cancelled.current) setState("idle");
    }
  };

  const startRecording = async () => {
    let s: MediaStream;
    try {
      s = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
    } catch (e) { cb.current.onNotice(micError(e)); return; }
    stream.current = s;
    const mime = pickMime();
    let r: MediaRecorder;
    try {
      r = new MediaRecorder(s, { ...(mime ? { mimeType: mime } : {}), audioBitsPerSecond: 32000 });
    } catch (e) { release(); cb.current.onNotice(micError(e)); return; }
    const chunks: Blob[] = [];
    r.ondataavailable = (ev) => { if (ev.data.size) chunks.push(ev.data); };
    r.onstop = () => {
      release();
      if (cancelled.current) return;
      const type = r.mimeType || mime || "audio/webm";
      transcribe(new Blob(chunks, { type }), type);
    };
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
    r.onresult = (e: { results: ArrayLike<ArrayLike<{ transcript: string }> & { isFinal?: boolean }> }) => {
      let said = "";
      for (let i = 0; i < e.results.length; i++) said += e.results[i][0].transcript;
      heard = heard || !!said.trim();
      cb.current.onText(said, false);
    };
    r.onerror = (e: { error?: string }) => { if (e.error !== "aborted") cb.current.onNotice(micError(e)); };
    r.onend = () => { sr.current = null; setState("idle"); if (heard) cb.current.onText("", true); };
    try { r.start(); } catch (e) { cb.current.onNotice(micError(e)); return; }
    sr.current = r;
    setState("listening");
  };

  const toggle = () => {
    if (state === "recording") { rec.current?.stop(); return; }
    if (state === "listening") { sr.current?.stop(); return; }
    if (state === "transcribing") return;
    if (canRecord() && (online || !SRCtor())) {
      if (!online) { cb.current.onNotice("Voice needs Panda to be running. Press Start, then try again."); return; }
      startRecording();
      return;
    }
    if (SRCtor()) { startBrowser(); return; }
    cb.current.onNotice("This browser can't use the microphone.");
  };

  return { state, secs, available, toggle };
}
