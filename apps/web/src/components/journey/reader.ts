// SPDX-License-Identifier: Apache-2.0
/**
 * The journey's reader (S22-02): the live view (the instant is "now", the map follows the API) or
 * the replay, which moves from one event to the next at the chosen speed. A pure reducer: the
 * tests drive it without a clock, and the page only sends it ticks.
 */
"use client";

import { useEffect, useReducer } from "react";

export type Speed = 0.5 | 1 | 2;
export type ReaderState = {
  mode: "live" | "replay";
  index: number;
  playing: boolean;
  speed: Speed;
};
export type Gesture =
  | { type: "play"; count: number }
  | { type: "pause" }
  | { type: "restart"; count: number }
  | { type: "tick"; count: number }
  | { type: "goto"; index: number; count: number }
  | { type: "live" }
  | { type: "speed"; speed: Speed };

/** One frame per event, at 1×: slow enough to follow a box, quick enough for a whole life. */
export const FRAME_DURATION_MS = 1100;

export const INITIAL: ReaderState = {
  mode: "live",
  index: 0,
  playing: false,
  speed: 1,
};

export function reduce(state: ReaderState, gesture: Gesture): ReaderState {
  switch (gesture.type) {
    case "play":
      if (gesture.count === 0) return state;
      // From the live view, or at the end: the replay starts again from the beginning.
      if (state.mode === "live" || state.index >= gesture.count - 1)
        return { ...state, mode: "replay", index: 0, playing: true };
      return { ...state, playing: true };
    case "pause":
      return { ...state, playing: false };
    case "restart":
      return gesture.count === 0 ? state : { ...state, mode: "replay", index: 0, playing: true };
    case "tick":
      if (!state.playing) return state;
      return state.index + 1 >= gesture.count
        ? { ...state, index: Math.max(gesture.count - 1, 0), playing: false }
        : { ...state, index: state.index + 1 };
    case "goto":
      return {
        ...state,
        mode: "replay",
        playing: false,
        index: Math.min(Math.max(gesture.index, 0), Math.max(gesture.count - 1, 0)),
      };
    case "live":
      return { ...state, mode: "live", playing: false };
    case "speed":
      return { ...state, speed: gesture.speed };
  }
}

export function useReader(count: number) {
  const [state, dispatch] = useReducer(reduce, INITIAL);
  useEffect(() => {
    if (!state.playing) return;
    const timer = setInterval(() => dispatch({ type: "tick", count }), FRAME_DURATION_MS / state.speed);
    return () => clearInterval(timer);
  }, [state.playing, state.speed, count]);
  return {
    ...state,
    play: () => dispatch({ type: "play", count }),
    pause: () => dispatch({ type: "pause" }),
    restart: () => dispatch({ type: "restart", count }),
    goTo: (index: number) => dispatch({ type: "goto", index, count }),
    live: () => dispatch({ type: "live" }),
    setSpeed: (speed: Speed) => dispatch({ type: "speed", speed }),
  };
}
