// SPDX-License-Identifier: Apache-2.0
import { Status } from "@/components/actions/status";
import { Card, Code } from "@/components/ui";
import type { Action } from "@/lib/types";

interface Ontology {
  action_type?: string;
  target_ids?: string[];
  params?: Record<string, unknown>;
}

interface Entry {
  index?: number;
  type?: string;
  name?: string;
  status?: string;
  expect?: string;
  error?: string;
  url?: string;
}

/**
 * What an ontology action touches (S20-08): its type, its targets, its parameters, then what
 * its effects returned and its evidence gathered. The core sees only `ontology.effet` and
 * `ontology.preuve` of it: the meaning is here, in `params.ontologie` and `result`.
 *
 * Before S20-10, a separate "proposals" page said it; a proposal is a core action,
 * under the same identifier, and its old links lead here.
 */
export function OntologyDetail({ action }: { action: Action }) {
  const meta = (action.params as { ontologie?: Ontology } | undefined)?.ontologie;
  if (action.origin !== "ontology" || !meta) return null;
  const result = (action.result ?? {}) as { effects?: Entry[]; evidence?: Entry[] };
  const effects = result.effects ?? [];
  const evidence = result.evidence ?? [];
  const targets = meta.target_ids ?? [];
  return (
    <Card title="Ontology">
      <dl className="grid grid-cols-[8rem_1fr] gap-x-4 gap-y-1 text-sm" data-testid="ontology">
        <dt className="text-ink-muted">Action type</dt>
        <dd>
          <Code>{meta.action_type ?? "—"}</Code>
        </dd>
        <dt className="text-ink-muted">Target</dt>
        <dd className="flex flex-wrap gap-1">{targets.length ? targets.map((c) => <Code key={c}>{c}</Code>) : "—"}</dd>
        <dt className="text-ink-muted">Parameters</dt>
        <dd>
          <pre className="whitespace-pre-wrap break-all text-xs">{JSON.stringify(meta.params ?? {}, null, 2)}</pre>
        </dd>
      </dl>
      {effects.length + evidence.length > 0 && (
        <ul className="mt-3 space-y-1 text-xs" data-testid="ontology-folder">
          {effects.map((effect, position) => (
            <li key={`e${position}`} className="flex flex-wrap items-center gap-2">
              <span>Effect</span>
              <code>{effect.type ?? String(effect.index ?? position)}</code>
              {effect.status && <Status statut={effect.status} />}
              {effect.url && <span className="text-ink-muted">{effect.url}</span>}
              {effect.error && <span className="text-danger">{effect.error}</span>}
            </li>
          ))}
          {evidence.map((proof, position) => (
            <li key={`p${position}`} className="flex flex-wrap items-center gap-2">
              <span>Evidence</span>
              <code>{proof.name ?? String(proof.index ?? position)}</code>
              {proof.status && <Status statut={proof.status} />}
              {proof.expect && <span className="text-ink-muted">Expects {proof.expect}</span>}
              {proof.error && <span className="text-danger">{proof.error}</span>}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
