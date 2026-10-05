// SPDX-License-Identifier: Apache-2.0
import { Code } from "@varga/design-system";
import { Statut } from "@/components/actions/statut";
import { Card } from "@/components/ui";
import type { Action } from "@/lib/types";

interface Ontologie {
  action_type?: string;
  target_ids?: string[];
  params?: Record<string, unknown>;
}

interface Entree {
  index?: number;
  type?: string;
  name?: string;
  status?: string;
  expect?: string;
  error?: string;
  url?: string;
}

/**
 * Ce qu'une action de l'ontologie touche (S20-08) : son type, ses cibles, ses paramètres — puis ce que
 * ses effets ont rendu et ses preuves recueilli. Le cœur ne voit d'elle que `ontology.effet` et
 * `ontology.preuve` : le sens est ici, dans `params.ontologie` et `result`.
 *
 * Avant S20-10, une page « proposals » à part le disait ; une proposition est une action du cœur,
 * sous le même identifiant, et ses anciens liens mènent ici.
 */
export function DetailOntologie({ action }: { action: Action }) {
  const meta = (action.params as { ontologie?: Ontologie } | undefined)?.ontologie;
  if (action.origin !== "ontology" || !meta) return null;
  const resultat = (action.result ?? {}) as { effects?: Entree[]; evidence?: Entree[] };
  const effets = resultat.effects ?? [];
  const preuves = resultat.evidence ?? [];
  const cibles = meta.target_ids ?? [];
  return (
    <Card title="ontology">
      <dl className="grid grid-cols-[8rem_1fr] gap-x-4 gap-y-1 text-sm" data-testid="ontologie">
        <dt className="text-ink-muted">action type</dt>
        <dd>
          <Code>{meta.action_type ?? "—"}</Code>
        </dd>
        <dt className="text-ink-muted">target</dt>
        <dd className="flex flex-wrap gap-1">{cibles.length ? cibles.map((c) => <Code key={c}>{c}</Code>) : "—"}</dd>
        <dt className="text-ink-muted">parameters</dt>
        <dd>
          <pre className="whitespace-pre-wrap break-all text-xs">{JSON.stringify(meta.params ?? {}, null, 2)}</pre>
        </dd>
      </dl>
      {effets.length + preuves.length > 0 && (
        <ul className="mt-3 space-y-1 text-xs" data-testid="ontologie-dossier">
          {effets.map((effet, position) => (
            <li key={`e${position}`} className="flex flex-wrap items-center gap-2">
              <span>effect</span>
              <code>{effet.type ?? String(effet.index ?? position)}</code>
              {effet.status && <Statut statut={effet.status} />}
              {effet.url && <span className="text-ink-muted">{effet.url}</span>}
              {effet.error && <span className="text-danger">{effet.error}</span>}
            </li>
          ))}
          {preuves.map((preuve, position) => (
            <li key={`p${position}`} className="flex flex-wrap items-center gap-2">
              <span>evidence</span>
              <code>{preuve.name ?? String(preuve.index ?? position)}</code>
              {preuve.status && <Statut statut={preuve.status} />}
              {preuve.expect && <span className="text-ink-muted">expects {preuve.expect}</span>}
              {preuve.error && <span className="text-danger">{preuve.error}</span>}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
