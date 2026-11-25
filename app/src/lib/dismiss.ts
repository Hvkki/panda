import { useEffect, useRef, type RefObject } from "react";

/**
 * Close a popup when the user taps or clicks outside it, or presses Escape.
 * Works with touch (no hover needed). `ref` is the popup plus whatever opened it, so the opener's
 * own tap doesn't count as "outside".
 */
export function useDismiss(open: boolean, onClose: () => void, refs: RefObject<HTMLElement | null>[]) {
  const close = useRef(onClose);
  close.current = onClose;
  useEffect(() => {
    if (!open) return;
    const outside = (e: PointerEvent) => {
      const t = e.target as Node;
      if (!refs.some((r) => r.current?.contains(t))) close.current();
    };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") { e.stopPropagation(); close.current(); } };
    // Attach after the current event, so the tap that opened the popup doesn't close it again.
    const id = window.setTimeout(() => document.addEventListener("pointerdown", outside, true), 0);
    document.addEventListener("keydown", esc, true);
    return () => { window.clearTimeout(id); document.removeEventListener("pointerdown", outside, true); document.removeEventListener("keydown", esc, true); };
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps
}

/** Escape closes a dialog. Only the topmost open dialog reacts. */
const stack: (() => void)[] = [];
let listening = false;
export function useEscape(onClose: () => void) {
  const close = useRef(onClose);
  close.current = onClose;
  useEffect(() => {
    const fn = () => close.current();
    stack.push(fn);
    if (!listening) {
      listening = true;
      document.addEventListener("keydown", (e) => { if (e.key === "Escape" && stack.length) stack[stack.length - 1](); });
    }
    return () => { const i = stack.lastIndexOf(fn); if (i >= 0) stack.splice(i, 1); };
  }, []);
}

/** True while a dialog that listens for Escape is open. */
export const dialogOpen = () => stack.length > 0;
