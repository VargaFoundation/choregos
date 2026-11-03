// SPDX-License-Identifier: Apache-2.0
"use client";

import { useCallback, useSyncExternalStore } from "react";

/** Whether this viewer asked the board to show its empty columns; kept in this browser only. */
const KEY = "choregos.board.show-empty-columns";
const EVENT = "choregos:board-show-empty-columns";

/** The choice when the browser refuses storage (private window, blocked site data): this page only. */
let unstored = false;

function read(): boolean {
  try {
    return window.localStorage.getItem(KEY) === "1";
  } catch {
    return unstored;
  }
}

function subscribe(onChange: () => void): () => void {
  const onStorage = (event: StorageEvent) => {
    if (event.key === KEY) onChange();
  };
  window.addEventListener("storage", onStorage);
  window.addEventListener(EVENT, onChange);
  return () => {
    window.removeEventListener("storage", onStorage);
    window.removeEventListener(EVENT, onChange);
  };
}

/**
 * The board's "Show empty columns" choice: off by default and on the server, remembered per viewer
 * when the browser lets us store it, and still applied to the current page when it does not.
 */
export function useShowEmptyColumns(): [boolean, (value: boolean) => void] {
  const shown = useSyncExternalStore(subscribe, read, () => false);
  const set = useCallback((value: boolean) => {
    unstored = value;
    try {
      window.localStorage.setItem(KEY, value ? "1" : "0");
    } catch {
      // Refused: `read` falls back to `unstored`.
    }
    window.dispatchEvent(new Event(EVENT));
  }, []);
  return [shown, set];
}
