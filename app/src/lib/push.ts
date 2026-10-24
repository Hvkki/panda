import { VAPID_PUBLIC_KEY } from "./config";
import { supabase } from "./supabase";

const b64ToBytes = (b64: string) => {
  const pad = "=".repeat((4 - (b64.length % 4)) % 4);
  const raw = atob((b64 + pad).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(raw, (c) => c.charCodeAt(0));
};

export const pushSupported = () => "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;

export async function pushState(): Promise<"unsupported" | "off" | "on" | "blocked"> {
  if (!pushSupported()) return "unsupported";
  if (Notification.permission === "denied") return "blocked";
  const reg = await navigator.serviceWorker.getRegistration();
  const sub = await reg?.pushManager.getSubscription();
  return sub ? "on" : "off";
}

/** Phone notifications even when the app is closed (scheduled tasks, long replies). */
export async function enablePush(owner: string) {
  if (!pushSupported()) throw new Error("This browser can't receive notifications. On iPhone, add Panda to the Home Screen first.");
  if ((await Notification.requestPermission()) !== "granted") throw new Error("Notifications are blocked for this site.");
  const reg = (await navigator.serviceWorker.getRegistration()) || (await navigator.serviceWorker.register("sw.js"));
  await navigator.serviceWorker.ready;
  const sub = (await reg.pushManager.getSubscription()) ||
    (await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64ToBytes(VAPID_PUBLIC_KEY) }));
  const j = sub.toJSON();
  const { error } = await supabase.from("push_subscriptions").upsert({ endpoint: j.endpoint, owner, keys: j.keys });
  if (error) throw error;
}

export async function disablePush() {
  const reg = await navigator.serviceWorker.getRegistration();
  const sub = await reg?.pushManager.getSubscription();
  if (sub) {
    await supabase.from("push_subscriptions").delete().eq("endpoint", sub.endpoint);
    await sub.unsubscribe();
  }
}
