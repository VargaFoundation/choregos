// SPDX-License-Identifier: Apache-2.0
/**
 * Keeps the Live model and the status line's figures up to date (ADR 0155 §6).
 *
 * It lists `GET /orgs/{org}/activity`, then follows `…/activity/stream`. The stream has no resumption point,
 * so after any interruption the feed lists again before it follows again. When the stream cannot be held, the
 * feed lists every 30 seconds and says it is not live; a 401 stops it (the person must sign in again) rather
 * than retry a refused credential.
 */
import type { ActivityList, CostReport, Me } from "@choregos/contracts/api";
import { ApiError, type ChoregosClient } from "../api/client";
import { LiveModel } from "./model";

export const POLL_MS = 30_000;
export const RECONNECT_MS = 1_000;

export type FeedState =
  | { kind: "signed-out" }
  | { kind: "no-organisation"; reason: string }
  | { kind: "connecting"; org: string }
  | { kind: "live"; org: string }
  | { kind: "polling"; org: string; reason: string };

export interface FeedDeps {
  /** A client under the person's credential, or nothing when they are not signed in. */
  client: () => Promise<ChoregosClient | undefined>;
  /** The organisation setting; empty means the person's first organisation. */
  organisation: () => string;
  sleep: (ms: number, signal: AbortSignal) => Promise<void>;
  log: (line: string) => void;
}

function isAborted(signal: AbortSignal): boolean {
  return signal.aborted;
}

export function today(now: Date): string {
  const pad = (n: number): string => String(n).padStart(2, "0");
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

export class LiveFeed {
  readonly model = new LiveModel();
  state: FeedState = { kind: "signed-out" };
  /** Today's spend of the organisation in USD; `undefined` when the person may not read it or it is unknown. */
  spendToday: number | undefined;
  private listeners: (() => void)[] = [];
  private controller: AbortController | undefined;
  private running: Promise<void> | undefined;

  constructor(
    private readonly deps: FeedDeps,
    private readonly now: () => Date = () => new Date(),
  ) {}

  onDidChange(listener: () => void): { dispose(): void } {
    this.listeners.push(listener);
    return {
      dispose: () => {
        this.listeners = this.listeners.filter((l) => l !== listener);
      },
    };
  }

  /** (Re)starts following, for the credential and the settings of now. */
  async restart(): Promise<void> {
    await this.stop();
    const controller = new AbortController();
    this.controller = controller;
    this.running = this.run(controller.signal);
  }

  async stop(): Promise<void> {
    this.controller?.abort();
    await this.running;
    this.controller = undefined;
    this.running = undefined;
  }

  private changed(state?: FeedState): void {
    if (state) this.state = state;
    for (const listener of [...this.listeners]) listener();
  }

  private async run(signal: AbortSignal): Promise<void> {
    const client = await this.deps.client();
    if (!client) {
      this.model.load({ in_flight: [], finished: [] });
      this.spendToday = undefined;
      this.changed({ kind: "signed-out" });
      return;
    }
    let org: string;
    try {
      org = await this.organisation(client, signal);
    } catch (error) {
      if (signal.aborted) return;
      this.stopped(error);
      return;
    }
    this.changed({ kind: "connecting", org });
    while (!signal.aborted) {
      let wait = RECONNECT_MS;
      try {
        await this.list(client, org, signal);
        for await (const message of client.stream(
          `/orgs/${encodeURIComponent(org)}/activity/stream`,
          undefined,
          {
            signal,
            opened: () => {
              this.changed({ kind: "live", org });
            },
          },
        )) {
          if (this.model.apply(message.event, message.data)) {
            if (message.event === "activity.ended") await this.spend(client, org, signal);
            this.changed();
          }
        }
      } catch (error) {
        // Read again: the stream may have been aborted while it was awaited.
        if (isAborted(signal)) return;
        if (error instanceof ApiError && error.status === 401) {
          this.stopped(error);
          return;
        }
        const reason = error instanceof Error ? error.message : String(error);
        this.deps.log(`activity of ${org}: ${reason}`);
        this.changed({ kind: "polling", org, reason });
        wait = POLL_MS;
      }
      await this.deps.sleep(wait, signal);
    }
  }

  private stopped(error: unknown): void {
    const reason = error instanceof Error ? error.message : String(error);
    this.deps.log(`activity stopped: ${reason}`);
    if (error instanceof ApiError && error.status === 401) {
      this.changed({ kind: "signed-out" });
      return;
    }
    this.changed({ kind: "no-organisation", reason });
  }

  private async organisation(client: ChoregosClient, signal: AbortSignal): Promise<string> {
    const chosen = this.deps.organisation().trim();
    if (chosen) return chosen;
    const me = await client.get<Me>("/me", undefined, signal);
    const first = me.memberships[0]?.org;
    if (!first) throw new Error("you belong to no organisation on this instance");
    return first;
  }

  private async list(client: ChoregosClient, org: string, signal: AbortSignal): Promise<void> {
    const list = await client.get<ActivityList>(
      `/orgs/${encodeURIComponent(org)}/activity`,
      undefined,
      signal,
    );
    this.model.load(list);
    await this.spend(client, org, signal);
    this.changed();
  }

  /** Today's spend, read from the ledger; a person who may not read the organisation's costs sees `—`. */
  private async spend(client: ChoregosClient, org: string, signal: AbortSignal): Promise<void> {
    const day = today(this.now());
    try {
      const report = await client.get<CostReport>(
        `/orgs/${encodeURIComponent(org)}/costs`,
        { since: day, until: day },
        signal,
      );
      this.spendToday = report.total.cost_usd;
    } catch (error) {
      if (signal.aborted) throw error;
      this.spendToday = undefined;
    }
  }
}
