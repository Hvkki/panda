import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);

// New deploys: an open tab (or installed app) keeps running the old bundle until it reloads,
// and sw.js rarely changes, so the SW never triggers that. Compare the page's bundle with the
// live one; when it changed, reload the next time the app comes to the front, or on a tap.
if (import.meta.env.PROD) {
  const mine = new URL(import.meta.url).pathname.split("/").pop();
  let fresh = false;
  const check = async () => {
    if (fresh) return;
    try {
      const html = await (await fetch(`./?v=${Date.now()}`, { cache: "no-store" })).text();
      const live = html.match(/assets\/(index-[\w-]+\.js)/)?.[1];
      if (live && mine && live !== mine) { fresh = true; showUpdate(); }
    } catch { /* offline: try later */ }
  };
  const showUpdate = () => {
    const b = document.createElement("button");
    b.className = "update-banner";
    b.textContent = "Panda was updated. Tap to reload.";
    b.onclick = () => location.reload();
    document.body.appendChild(b);
  };
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState !== "visible") return;
    if (fresh) location.reload(); else check();
  });
  setInterval(check, 3 * 60_000);
  setTimeout(check, 15_000);
}

// Installable app (PWA). When a new version deploys, activate it and reload once
// so updates land immediately instead of after several visits.
if ("serviceWorker" in navigator && import.meta.env.PROD) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("sw.js").then((reg) => {
      reg.addEventListener("updatefound", () => {
        const sw = reg.installing;
        sw?.addEventListener("statechange", () => {
          if (sw.state === "installed" && navigator.serviceWorker.controller) sw.postMessage?.("skip");
        });
      });
      setInterval(() => reg.update().catch(() => {}), 60 * 60 * 1000);
    }).catch(() => {});
    let reloaded = false;
    navigator.serviceWorker.addEventListener("controllerchange", () => {
      if (!reloaded) { reloaded = true; location.reload(); }
    });
  });
}
