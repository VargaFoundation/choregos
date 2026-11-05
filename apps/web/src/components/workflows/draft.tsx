// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { useWorkflow } from "@/components/workflows/use-workflow";
import { ApiError, api } from "@/lib/api";
import type { WorkflowEditResult, WorkflowOperation, WorkflowValidation } from "@/lib/types";
import type { WorkflowDocument } from "@/components/workflows/document";

/** A gesture to undo: a typed operation (its inverse, replayed by the API) or a typing session (the text before). */
type Gesture = { inverse: WorkflowOperation[] } | { textBefore: string };

type DraftState = {
  yaml: string;
  /** What the API says about the text: the result of a typed edit, or the validation of the typing. */
  result: WorkflowValidation | WorkflowEditResult | null;
  diff: string;
  /** Oldest to newest: undoing reverts the last one. */
  gestures: Gesture[];
  /** The version the draft started from: it is the one the publication announces. */
  baseVersion: number;
  /** True while the typing continues the same session: a session is undone in one go. */
  typing: boolean;
};

export type ValidationState = { state: "current" } | { state: "running" } | { state: "unreachable"; message: string };

/** The delay between the last keystroke and the validation: enough not to validate every letter. */
export const VALIDATION_DELAY_MS = 400;

export type Draft = ReturnType<typeof useDraftInternal>;

/**
 * The draft survives navigation in the console (S23-06): `beforeunload` only holds back a reload
 * and the closing of the tab; a click on "board" unmounted the context and the draft went with it,
 * without a word. It is kept in the tab's storage, and resumed on return with its starting
 * version: if another was published meanwhile, the conflict is told as before.
 */
export function draftKey(slug: string, name: string): string {
  return `choregos.draft.${slug}.${name}`;
}

function readDraft(key: string): DraftState | null {
  // The key and the fields a draft had before S45-27 are no longer read (removed in 0.24.0, S45-49).
  try {
    const raw = window.sessionStorage.getItem(key);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as DraftState;
    return typeof parsed?.yaml === "string" && typeof parsed.baseVersion === "number" && Array.isArray(parsed.gestures)
      ? { ...parsed, typing: false }
      : null;
  } catch {
    return null;
  }
}

function keepDraft(key: string, state: DraftState | null) {
  try {
    if (state) window.sessionStorage.setItem(key, JSON.stringify(state));
    else window.sessionStorage.removeItem(key);
  } catch {
    // Storage refused (private browsing, quota): the draft lives as before, for the time of the page.
  }
}

/**
 * A workflow's draft, the ONLY state of the three views (S21-06): typing in the YAML tab, the
 * gestures of the map and of the process view write into the same text, and a single publication
 * sends it. Before, the YAML tab held its own text: two "publish" buttons, and the map's draft,
 * published after it, overwrote it without a 409: it re-read the version at publishing time.
 *
 * A typed gesture goes through `POST /workflows/edit` (S16-11) and pushes its inverse; a typing
 * session pushes the text before. The validation follows the typing (debounced, cancelled if
 * another overtakes it); publishing requires a validated, valid text, and announces the STARTING
 * version: if another was published meanwhile, the API refuses (409) and the text stays.
 */
function useDraftInternal(slug: string, name: string) {
  const { definition, validation } = useWorkflow(slug, name);
  const client = useQueryClient();
  const [state, setState] = useState<DraftState | null>(null);
  const [textValidation, setTextValidation] = useState<ValidationState>({ state: "current" });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [conflict409, setConflict409] = useState(false);
  // `undefined` until the kept draft is read: before that, writing "nothing" would erase it.
  const [resumed, setResumed] = useState<DraftState | null | undefined>(undefined);
  const key = draftKey(slug, name);
  const base = definition.data?.yaml ?? "";
  const yaml = state?.yaml ?? base;

  // The gestures read the CURRENT text, even when started from an older closure.
  const currentText = useRef(yaml);
  useEffect(() => {
    currentText.current = yaml;
  }, [yaml]);
  // Every validation carries a serial: only the last one asked may write its verdict.
  const sequence = useRef(0);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const inFlight = useRef<AbortController | null>(null);

  const stopValidation = useCallback(() => {
    sequence.current += 1;
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
    inFlight.current?.abort();
    inFlight.current = null;
  }, []);

  const validate = useCallback(
    (text: string) => {
      stopValidation();
      const serial = sequence.current;
      setTextValidation({ state: "running" });
      timer.current = setTimeout(() => {
        const controller = new AbortController();
        inFlight.current = controller;
        api
          .validateWorkflow(text, controller.signal)
          .then((result) => {
            if (serial !== sequence.current) return;
            setState((current) => (current && current.yaml === text ? { ...current, result } : current));
            setTextValidation({ state: "current" });
          })
          .catch((cause: unknown) => {
            if (serial !== sequence.current || controller.signal.aborted) return;
            const message = cause instanceof Error ? cause.message : "the server did not answer";
            setTextValidation({ state: "unreachable", message });
          });
      }, VALIDATION_DELAY_MS);
    },
    [stopValidation],
  );

  useEffect(() => stopValidation, [stopValidation]);

  // Storage only exists in the browser: the resumption waits for the mount.
  useEffect(() => {
    const kept = readDraft(key);
    // eslint-disable-next-line react-hooks/set-state-in-effect -- read once, at mount, outside server rendering
    setState(kept);
    setResumed(kept);
    if (kept) validate(kept.yaml);
  }, [key, validate]);

  useEffect(() => {
    if (resumed !== undefined) keepDraft(key, state);
  }, [key, state, resumed]);

  // Leaving the page with an unpublished draft: the browser asks for confirmation.
  useEffect(() => {
    if (!state) return;
    const hold = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", hold);
    return () => window.removeEventListener("beforeunload", hold);
  }, [state]);

  /** The typing of the YAML tab. */
  function write(text: string) {
    if (!definition.data) return;
    const readVersion = definition.data.version;
    setError(null);
    setState((current) => {
      if (!current) {
        if (text === base) return null;
        return {
          yaml: text,
          result: null,
          diff: "",
          gestures: [{ textBefore: base }],
          baseVersion: readVersion,
          typing: true,
        };
      }
      const gestures = current.typing ? current.gestures : [...current.gestures, { textBefore: current.yaml }];
      return { ...current, yaml: text, gestures, typing: true, diff: "" };
    });
    validate(text);
  }

  async function apply(...ops: WorkflowOperation[]): Promise<boolean> {
    // Before the read, there is no text to graft onto: a gesture would go onto an empty document.
    if (!definition.data) return false;
    const readVersion = definition.data.version;
    setError(null);
    setBusy(true);
    try {
      const result = await api.editWorkflow(currentText.current, ops);
      // The next gesture may start before React renders this one (a double click, or a caller that
      // chains two edits): it must graft onto this text, not the one the last render saw (#678).
      currentText.current = result.yaml;
      // The edit's verdict counts as a validation: a typing validation still in flight is stale.
      stopValidation();
      setTextValidation({ state: "current" });
      setState((current) => ({
        yaml: result.yaml,
        result,
        diff: result.diff,
        gestures: [...(current?.gestures ?? []), { inverse: result.inverse }],
        baseVersion: current?.baseVersion ?? readVersion,
        typing: false,
      }));
      return true;
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "edit refused");
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function undo() {
    if (!state) return;
    const last = state.gestures.at(-1);
    if (!last) return;
    const remaining = state.gestures.slice(0, -1);
    if ("textBefore" in last) {
      if (remaining.length === 0) {
        stopValidation();
        setTextValidation({ state: "current" });
        setState(null);
        return;
      }
      setState({ ...state, yaml: last.textBefore, gestures: remaining, typing: false, diff: "" });
      validate(last.textBefore);
      return;
    }
    setBusy(true);
    try {
      const result = await api.editWorkflow(state.yaml, last.inverse);
      currentText.current = remaining.length === 0 ? base : result.yaml;
      stopValidation();
      setTextValidation({ state: "current" });
      setState(
        remaining.length === 0
          ? null
          : { ...state, yaml: result.yaml, result, diff: result.diff, gestures: remaining, typing: false },
      );
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "undo refused");
    } finally {
      setBusy(false);
    }
  }

  async function send(announcedVersion: number): Promise<string | null> {
    if (!state) return null;
    setBusy(true);
    setError(null);
    try {
      const published = await api.putWorkflowNamed(slug, name, state.yaml, announcedVersion);
      stopValidation();
      setState(null);
      setConflict409(false);
      await client.invalidateQueries({ queryKey: ["workflow", slug, name] });
      await client.invalidateQueries({ queryKey: ["workflows", slug] });
      await client.invalidateQueries({ queryKey: ["workflow-versions", slug, name] });
      return `${published.name} v${published.version} published — running items finish on their version`;
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 409) {
        setConflict409(true);
        await client.invalidateQueries({ queryKey: ["workflow", slug, name] });
      } else {
        setError(cause instanceof Error ? cause.message : "publication refused");
      }
      return null;
    } finally {
      setBusy(false);
    }
  }

  /** Publish: the draft's STARTING version, never the one read since. */
  function publish() {
    return state ? send(state.baseVersion) : Promise.resolve(null);
  }

  /** Publish over the most recent version: an explicit choice, the other version stays in the history. */
  async function overwrite() {
    const reread = await definition.refetch();
    const latest = reread.data?.version;
    return latest === undefined ? null : send(latest);
  }

  /** Abandon the draft and start again from the published version. */
  async function reload() {
    stopValidation();
    setTextValidation({ state: "current" });
    setState(null);
    setConflict409(false);
    setError(null);
    await definition.refetch();
  }

  // The last graph of a VALID text: when the typing makes the YAML unreadable, the map keeps the
  // previous one (greyed) rather than disappearing.
  const result = state?.result ?? null;
  const publishedValidation = validation.data;
  const current = state ? result : (publishedValidation ?? null);
  const [lastValid, setLastValid] = useState<WorkflowValidation | null>(null);
  if (current?.valid && current.graph && current !== lastValid) setLastValid(current);
  const readable = Boolean(current?.graph && current.process);
  const shown = readable ? current : lastValid;

  const publishedVersion = definition.data?.version;
  const conflict =
    state && publishedVersion !== undefined && (conflict409 || publishedVersion !== state.baseVersion)
      ? { readVersion: state.baseVersion, publishedVersion }
      : null;
  const valid = state ? Boolean(result?.valid) : (publishedValidation?.valid ?? true);

  return {
    definition,
    yaml,
    pending: state?.gestures.length ?? 0,
    /** The draft comes from a previous visit in this tab, and nothing touched it since. */
    resumed: Boolean(state && resumed && state.yaml === resumed.yaml),
    graph: shown?.graph ?? undefined,
    process: shown?.process ?? undefined,
    /** The draft as the engine read it (ADR 0112): its actors and transitions, before publishing. */
    document: (shown?.document ?? definition.data?.json ?? undefined) as WorkflowDocument | undefined,
    /** True when the map shows the last valid text, not the current text. */
    graphStale: Boolean(state) && !readable && Boolean(lastValid),
    valid,
    errors: current?.errors ?? [],
    warnings: current?.warnings ?? [],
    lastDiff: state?.diff ?? "",
    notices: result && "notices" in result ? ((result as WorkflowEditResult).notices ?? []) : [],
    validation: textValidation,
    publishable: Boolean(state) && textValidation.state === "current" && valid && !busy && Boolean(result),
    conflict,
    error,
    busy,
    write,
    apply,
    undo,
    abandon: () => void reload(),
    publish,
    overwrite,
    reload,
    revalidate: () => validate(yaml),
  };
}

const DraftContext = createContext<Draft | null>(null);

export function DraftProvider({ slug, name, children }: { slug: string; name: string; children: ReactNode }) {
  const draft = useDraftInternal(slug, name);
  return <DraftContext.Provider value={draft}>{children}</DraftContext.Provider>;
}

export function useDraft(): Draft {
  const draft = useContext(DraftContext);
  if (!draft) throw new Error("useDraft outside DraftProvider");
  return draft;
}
