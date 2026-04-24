/**
 * Tiny ``CodeView`` preference store with localStorage persistence and
 * cross-instance broadcast. Every ``CodeView`` mounts its own subscription
 * so toggling a setting in one instance updates the others live.
 */

import { useEffect, useState } from "react";

export type CodeViewPrefs = {
  wordWrap: boolean;
  showWhitespace: boolean;
  fontSize: "s" | "m" | "l";
  highlightCurrentLine: boolean;
  theme: "dark" | "hc";
};

export const DEFAULT_PREFS: CodeViewPrefs = {
  wordWrap: false,
  showWhitespace: false,
  fontSize: "m",
  highlightCurrentLine: false,
  theme: "dark",
};

const STORAGE_KEY = "androscan.codeview.prefs.v1";
const EVENT_NAME = "androscan:codeview-prefs";

function readFromStorage(): CodeViewPrefs {
  if (typeof window === "undefined") return DEFAULT_PREFS;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULT_PREFS;
    const parsed = JSON.parse(raw) as Partial<CodeViewPrefs>;
    return { ...DEFAULT_PREFS, ...parsed };
  } catch {
    return DEFAULT_PREFS;
  }
}

function writeToStorage(p: CodeViewPrefs): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(p));
  } catch {
    /* private mode / quota — silently ignore, prefs still live in memory */
  }
}

export function useCodeViewPrefs(): [
  CodeViewPrefs,
  (patch: Partial<CodeViewPrefs>) => void,
] {
  const [prefs, setPrefs] = useState<CodeViewPrefs>(() => readFromStorage());

  // Keep this instance in sync with mutations made by *other* CodeView
  // instances on the page (and with native `storage` events from other tabs).
  useEffect(() => {
    const onCustom = (e: Event) => {
      const detail = (e as CustomEvent<CodeViewPrefs>).detail;
      if (detail) setPrefs(detail);
    };
    const onStorage = (e: StorageEvent) => {
      if (e.key === STORAGE_KEY) setPrefs(readFromStorage());
    };
    window.addEventListener(EVENT_NAME, onCustom as EventListener);
    window.addEventListener("storage", onStorage);
    return () => {
      window.removeEventListener(EVENT_NAME, onCustom as EventListener);
      window.removeEventListener("storage", onStorage);
    };
  }, []);

  const update = (patch: Partial<CodeViewPrefs>) => {
    setPrefs((prev) => {
      const next = { ...prev, ...patch };
      writeToStorage(next);
      window.dispatchEvent(
        new CustomEvent<CodeViewPrefs>(EVENT_NAME, { detail: next }),
      );
      return next;
    });
  };

  return [prefs, update];
}

export const FONT_SIZE_REM: Record<CodeViewPrefs["fontSize"], string> = {
  s: "0.66rem",
  m: "0.78rem",
  l: "0.92rem",
};
