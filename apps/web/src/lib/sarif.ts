// SPDX-License-Identifier: Apache-2.0
/** A SARIF 2.1 report read as rows: what the console's table shows (ADR 0049). */

export type SarifRow = { tool: string; rule: string; level: string; message: string; location: string };

type SarifLike = {
  runs?: {
    tool?: { driver?: { name?: string } };
    results?: {
      ruleId?: string;
      level?: string;
      message?: { text?: string };
      locations?: { physicalLocation?: { artifactLocation?: { uri?: string }; region?: { startLine?: number } } }[];
    }[];
  }[];
};

export function sarifRows(text: string): SarifRow[] {
  const report = JSON.parse(text) as SarifLike;
  const rows: SarifRow[] = [];
  for (const run of report.runs ?? []) {
    const tool = run.tool?.driver?.name ?? "";
    for (const result of run.results ?? []) {
      const physical = result.locations?.[0]?.physicalLocation;
      const uri = physical?.artifactLocation?.uri ?? "";
      const line = physical?.region?.startLine;
      rows.push({
        tool,
        rule: result.ruleId ?? "",
        level: result.level ?? "warning",
        message: result.message?.text ?? "",
        location: uri ? `${uri}${line ? `:${line}` : ""}` : "",
      });
    }
  }
  return rows;
}
