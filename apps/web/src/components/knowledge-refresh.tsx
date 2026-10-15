// SPDX-License-Identifier: Apache-2.0
"use client";

import { Input, Select } from "@/components/ui";
import { browserTimezone, type RefreshDraft } from "@/lib/knowledge";

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

/**
 * The refresh of a knowledge source (S39-19): every N minutes, on a cron in a time zone, or only on
 * demand. The three fields the mode needs, nothing else.
 */
export function RefreshFields({ value, onChange }: { value: RefreshDraft; onChange: (next: RefreshDraft) => void }) {
  return (
    <>
      <Select
        aria-label="refresh mode"
        value={value.mode}
        onChange={(e) => onChange({ ...value, mode: e.target.value as RefreshDraft["mode"] })}
      >
        <option value="interval">every N minutes</option>
        <option value="cron">on a cron schedule</option>
        <option value="manual">manual only</option>
      </Select>
      {value.mode === "interval" && (
        <Input
          aria-label="interval in minutes"
          placeholder="every … minutes"
          value={value.interval}
          onChange={(e) => onChange({ ...value, interval: e.target.value })}
        />
      )}
      {value.mode === "cron" && (
        <>
          <Input
            aria-label="cron expression"
            placeholder="0 6 * * 1-5"
            value={value.cron}
            onChange={(e) => onChange({ ...value, cron: e.target.value })}
          />
          <Select aria-label="time zone" value={value.timezone} onChange={(e) => onChange({ ...value, timezone: e.target.value })}>
            {timezones().map((zone) => (
              <option key={zone} value={zone}>
                {zone}
              </option>
            ))}
          </Select>
        </>
      )}
    </>
  );
}
