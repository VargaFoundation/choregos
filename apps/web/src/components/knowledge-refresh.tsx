// SPDX-License-Identifier: Apache-2.0
"use client";

import type { ReactNode } from "react";
import { Input, LEGENDE, Select } from "@/components/ui";
import { browserTimezone, describeRefresh, inTimezone, WEEK, type RefreshDraft } from "@/lib/knowledge";

function timezones(): string[] {
  const own = browserTimezone();
  let known: string[] = [];
  try {
    known = (Intl as unknown as { supportedValuesOf?: (key: string) => string[] }).supportedValuesOf?.("timeZone") ?? [];
  } catch {
    known = [];
  }
  return Array.from(new Set(["UTC", own, ...known]));
}

const HOURS = [1, 2, 3, 4, 6, 8, 12, 24, 48, 168];

/** A control with its name above it: the refresh's fields are read, not guessed from a placeholder. */
function Labelled({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="flex min-w-[9rem] flex-col gap-1">
      <span className={LEGENDE}>{label}</span>
      {children}
    </label>
  );
}

/**
 * The refresh of a knowledge source (S39-19, S47-48): a choice a person reads — every N minutes or
 * hours, every day, every weekday, chosen days of the week or a day of the month at a time in a time
 * zone, a cron of one's own, or only on demand — with what it means said in a sentence under it. Only
 * the fields the choice needs are shown; the API stores it as an interval, a cron or manual.
 */
export function RefreshFields({ value, onChange }: { value: RefreshDraft; onChange: (next: RefreshDraft) => void }) {
  const set = (patch: Partial<RefreshDraft>) => onChange({ ...value, ...patch });
  const toggle = (day: number) =>
    set({ days: value.days.includes(day) ? value.days.filter((d) => d !== day) : [...value.days, day] });
  return (
    <fieldset className="space-y-2 border border-line p-3 sm:col-span-3" data-testid="refresh-fields">
      <legend className="px-1 text-xs font-semibold text-ink">Refresh</legend>
      <div className="flex flex-wrap items-end gap-3">
        <Labelled label="Sync">
          <Select aria-label="refresh mode" value={value.mode} onChange={(e) => set({ mode: e.target.value as RefreshDraft["mode"] })}>
            <option value="hours">every N hours</option>
            <option value="minutes">every N minutes</option>
            <option value="daily">every day</option>
            <option value="weekdays">every weekday (Mon–Fri)</option>
            <option value="weekly">on chosen days of the week</option>
            <option value="monthly">once a month</option>
            <option value="cron">custom cron expression</option>
            <option value="manual">manually only</option>
          </Select>
        </Labelled>
        {value.mode === "minutes" && (
          <Labelled label="Minutes (at least 5)">
            <Input
              aria-label="interval in minutes"
              type="number"
              min={5}
              max={10080}
              value={value.interval}
              onChange={(e) => set({ interval: e.target.value })}
            />
          </Labelled>
        )}
        {value.mode === "hours" && (
          <Labelled label="Hours">
            <Select aria-label="interval in hours" value={value.hours} onChange={(e) => set({ hours: e.target.value })}>
              {HOURS.map((n) => (
                <option key={n} value={String(n)}>
                  {n === 24 ? "24 (once a day)" : n === 168 ? "168 (once a week)" : n}
                </option>
              ))}
            </Select>
          </Labelled>
        )}
        {value.mode === "monthly" && (
          <Labelled label="Day of the month">
            <Select aria-label="day of the month" value={value.dayOfMonth} onChange={(e) => set({ dayOfMonth: e.target.value })}>
              {Array.from({ length: 28 }, (_, i) => String(i + 1)).map((d) => (
                <option key={d} value={d}>
                  {d}
                </option>
              ))}
            </Select>
          </Labelled>
        )}
        {inTimezone(value.mode) && value.mode !== "cron" && (
          <Labelled label="At">
            <Input aria-label="time of day" type="time" value={value.time} onChange={(e) => set({ time: e.target.value })} />
          </Labelled>
        )}
        {value.mode === "cron" && (
          <Labelled label="Cron (minute hour day month weekday)">
            <Input
              aria-label="cron expression"
              placeholder="0 6 * * 1-5"
              className="font-mono"
              value={value.cron}
              onChange={(e) => set({ cron: e.target.value })}
            />
          </Labelled>
        )}
        {inTimezone(value.mode) && (
          <Labelled label="Time zone">
            <Select aria-label="time zone" value={value.timezone} onChange={(e) => set({ timezone: e.target.value })}>
              {timezones().map((zone) => (
                <option key={zone} value={zone}>
                  {zone}
                </option>
              ))}
            </Select>
          </Labelled>
        )}
      </div>
      {value.mode === "weekly" && (
        <div role="group" aria-label="days of the week" className="flex flex-wrap gap-2 text-xs">
          {WEEK.map(({ day, short }) => (
            <label key={day} className="flex items-center gap-1 border border-line px-2 py-1">
              <input type="checkbox" checked={value.days.includes(day)} onChange={() => toggle(day)} />
              {short}
            </label>
          ))}
        </div>
      )}
      <p className="text-xs text-ink-muted" data-testid="refresh-summary">
        Syncs {describeRefresh(value)}
        {inTimezone(value.mode) ? `, ${value.timezone} time` : ""}.
        {value.mode === "weekly" && value.days.length === 0 ? " Pick at least one day (Monday otherwise)." : ""}
      </p>
    </fieldset>
  );
}
