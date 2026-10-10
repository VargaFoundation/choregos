// SPDX-License-Identifier: Apache-2.0
"use client";

import { useMemo, useRef, useState, type KeyboardEvent } from "react";
import { LEGENDE } from "@/components/ui";
import { cn } from "@/lib/cn";
import type { ProcessStep } from "@/lib/types";
import {
  type AreteDuGraphe,
  commentOnYTombe,
  COULEUR_DU_GENRE,
  defaults,
  disposerLaCarte,
  genreDActeur,
  type Graphe,
  LIBELLE_DU_GENRE,
  navigation,
  type NoeudDuGraphe,
  sortiesDe,
} from "./disposition";

type Acteurs = Record<string, { type?: string; group?: string; sla_hours?: number; role?: string } | undefined>;
type Choix = (kind: "node" | "edge", id: string) => void;

/** Au-delà, une case nomme le nombre de garanties, pas chacune : la case reste lisible. */
const GARANTIES_NOMMEES = 3;

/**
 * La carte d'un workflow, à plat (S21-07) : le chemin nominal en une colonne, de haut en bas ; chaque
 * état est une case colorée par QUI fait avancer le ticket, et le dit en toutes lettres ; une flèche
 * entre deux cases est la transition, cliquable. Ce qui sort du chemin — reprises, rejets, escalades,
 * attentes humaines — se range dans la colonne d'à côté, avec d'où l'on y tombe. Pas de zoom, pas de
 * glisser : elle se lit comme une page, même à dix-huit états, et s'imprime.
 *
 * Au clavier : Tab atteint les cases et les flèches ; ↓ ↑ suivent l'ordre de lecture, → ← la
 * transition nominale, Début/Fin les extrémités, Entrée ouvre le panneau. L'état sous le curseur est
 * décrit sous la carte (`aria-live`).
 */
export function CarteDuWorkflow({
  graph,
  process,
  initial,
  acteurs = {},
  onSelect,
}: {
  graph: Graphe;
  process?: ProcessStep[];
  initial?: string | null;
  acteurs?: Acteurs;
  onSelect?: Choix;
}) {
  const disposition = useMemo(() => disposerLaCarte(graph, initial), [graph, initial]);
  const nav = useMemo(() => navigation(graph, initial), [graph, initial]);
  const parId = useMemo(() => new Map((graph.nodes as NoeudDuGraphe[]).map((n) => [n.id, n])), [graph]);
  const etapes = useMemo(() => new Map((process ?? []).map((e) => [e.id, e])), [process]);
  const [focus, setFocus] = useState<string | null>(null);
  const [survol, setSurvol] = useState<string | null>(null);
  const [toutes, setToutes] = useState(false);
  const conteneur = useRef<HTMLDivElement>(null);
  const autour = survol ?? focus;
  // Les cibles des issues de l'état survolé s'éclairent dans la colonne d'à côté.
  const eclaires = new Set(
    autour ? sortiesDe(graph, autour).secondaires.map((a) => a.to) : [],
  );

  function allerA(id: string) {
    conteneur.current?.querySelector<HTMLElement>(`[data-testid="etat-${CSS.escape(id)}"]`)?.focus();
  }

  function auClavier(event: KeyboardEvent<HTMLButtonElement>) {
    const courant = (event.target as HTMLElement).closest<HTMLElement>("[data-etat]")?.dataset.etat;
    if (!courant) return;
    const cible = nav.deplacer(courant, event.key);
    if (cible === undefined) return;
    event.preventDefault();
    if (cible) allerA(cible);
  }

  const secondaires = (graph.edges as AreteDuGraphe[]).filter((a) => a.kind && a.kind !== "nominal" && a.kind !== "default").length;
  const nom = (id: string) => parId.get(id)?.display ?? id;

  const carte = (id: string, suivant?: string, deCote?: { comment: string; id: string }[]) => {
    const noeud = parId.get(id);
    if (!noeud) return null;
    const genre = genreDActeur(noeud.lane);
    const couleur = COULEUR_DU_GENRE[genre];
    const { principale, autres, secondaires: issues } = sortiesDe(graph, id, suivant);
    const etape = principale?.id ? etapes.get(principale.id) : undefined;
    const acteur = principale?.actor ? acteurs[principale.actor] : undefined;
    const rejet = issues.find((a) => a.kind === "reject");
    const montrerIssues = toutes || autour === id;
    const garanties = principale?.gates ?? [];
    return (
      // Le survol n'est qu'un raccourci : le focus du bouton de la case montre les mêmes issues, au clavier.
      // eslint-disable-next-line jsx-a11y/no-static-element-interactions
      <div
        className={cn("raised border border-l-[3px] border-line px-3 py-2 text-sm", deCote && "border-dashed")}
        data-genre={genre}
        style={{
          borderLeftColor: couleur.bord,
          borderLeftStyle: "solid",
          outline: eclaires.has(id) ? "2px solid var(--varga-focus)" : undefined,
        }}
        onMouseEnter={() => setSurvol(id)}
        onMouseLeave={() => setSurvol(null)}
      >
        <button
          type="button"
          data-testid={`etat-${id}`}
          data-etat={id}
          className="block w-full text-left focus-visible:outline-2"
          aria-label={`${noeud.display}, ${LIBELLE_DU_GENRE[genre]}${noeud.terminal ? ", terminal" : ""}`}
          onFocus={() => setFocus(id)}
          onKeyDown={auClavier}
          onBlur={() => setFocus(null)}
          onClick={() => onSelect?.("node", id)}
        >
          <span className="flex items-baseline justify-between gap-2 text-xs">
            <span className={cn(LEGENDE, "font-medium")} style={{ color: couleur.texte }}>
              {LIBELLE_DU_GENRE[genre]}
              {principale?.actor ? ` · ${principale.actor}` : ""}
            </span>
            {acteur?.group && (
              <span className="text-ink-muted">
                {acteur.group}
                {acteur.sla_hours ? `, within ${acteur.sla_hours} h` : ""}
              </span>
            )}
          </span>
          <span data-titre className="mt-0.5 block text-[15px] font-semibold text-ink">
            {noeud.display}
          </span>
          <span className="block font-mono text-xs text-ink-muted">{id}</span>
        </button>
        {(etape?.outputs?.length || garanties.length || principale?.timeout_hours || rejet) && (
          <ul className="mt-1.5 space-y-0.5 text-xs text-ink-muted">
            {etape?.outputs && etape.outputs.length > 0 && <li>produces {etape.outputs.join(", ")}</li>}
            {garanties.length > 0 && (
              <li title={garanties.join(", ")}>
                only if {garanties.length > GARANTIES_NOMMEES ? `${garanties.length} gates hold` : garanties.join(", ")}
              </li>
            )}
            {principale?.timeout_hours ? <li>times out after {principale.timeout_hours} h</li> : null}
            {rejet && <li>if rejected → {nom(rejet.to)}</li>}
          </ul>
        )}
        {autres.length > 0 && (
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {autres.map((a) => (
              <button
                key={a.id ?? a.to}
                type="button"
                data-testid={`transition-${a.id}`}
                className="border border-line bg-surface px-1.5 py-0.5 font-mono text-[11px] hover:border-line-strong pointer-coarse:min-h-11"
                onClick={() => a.id && onSelect?.("edge", a.id)}
              >
                also → {nom(a.to)}
                {a.actor ? ` (by ${a.actor})` : ""}
              </button>
            ))}
          </div>
        )}
        {issues.length > 0 && montrerIssues && (
          <ul className="mt-1.5 space-y-0.5 border-t border-line pt-1.5 text-xs text-retrying-ink" data-testid={`secondaires-${id}`}>
            {issues.map((a) => (
              <li key={a.id ?? `${a.to}-${a.kind}`}>
                {commentOnYTombe(a)} → {nom(a.to)}
              </li>
            ))}
          </ul>
        )}
        {deCote && deCote.length > 0 && (
          <p className="mt-1.5 text-xs text-ink-muted">
            reached from {deCote.map((d) => `${nom(d.id)} (${d.comment})`).join("; ")}
          </p>
        )}
      </div>
    );
  };

  const fleche = (de: string, vers: string) => {
    const { principale } = sortiesDe(graph, de, vers);
    if (!principale) return null;
    const qui = principale.actor ? `by ${principale.actor}` : principale.via ? "by the release train" : "";
    const garanties = principale.gates?.length ? `${principale.gates.length} gate${principale.gates.length > 1 ? "s" : ""}` : "";
    const effet = etapes.get(principale.id ?? "")?.who.includes("opens the pull request")
      ? "opens the PR"
      : etapes.get(principale.id ?? "")?.who.includes("merges the pull request")
        ? "merges the PR"
        : "";
    return (
      <button
        type="button"
        data-testid={`transition-${principale.id}`}
        className="group mx-auto flex flex-col items-center py-1 text-xs text-ink-muted hover:text-ink"
        aria-label={`transition ${principale.id}: ${nom(de)} to ${nom(vers)}${qui ? `, ${qui}` : ""}`}
        onClick={() => principale.id && onSelect?.("edge", principale.id)}
      >
        <span aria-hidden className="h-3 w-px bg-line-strong group-hover:bg-ink" />
        <span className="px-1.5 font-mono text-[11px]">{[qui, garanties, effet].filter(Boolean).join(" · ") || principale.id}</span>
        <span aria-hidden className="leading-none">
          ▼
        </span>
      </button>
    );
  };

  const description = focus ? nav.decrire(focus) : "Tab reaches the states and the arrows; arrows move along the workflow; Enter opens one.";

  return (
    <div className="space-y-4" data-testid="workflow-graph">
      <LegendeDeLaCarte graph={graph} toutes={toutes} onToutes={setToutes} secondaires={secondaires} jokers={disposition.jokers} nom={nom} onSelect={onSelect} />
      {/* Les flèches du clavier se lisent sur chaque case (`auClavier`) ; Tab reste libre. */}
      <div
        ref={conteneur}
        role="group"
        aria-label="workflow map"
        className="grid gap-x-6 gap-y-3 md:grid-cols-[minmax(0,1fr)_17rem]"
      >
        <ol aria-label="nominal path" className="space-y-0">
          {disposition.chemin.map((id, i) => (
            <li key={id}>
              {carte(id, disposition.chemin[i + 1])}
              {disposition.chemin[i + 1] && fleche(id, disposition.chemin[i + 1]!)}
            </li>
          ))}
        </ol>
        <div className="space-y-2">
          {disposition.cotes.length > 0 && <p className={LEGENDE}>off the main path</p>}
          <ul aria-label="off the main path" className="space-y-2">
            {disposition.cotes.map((cote) => (
              <li key={cote.id}>{carte(cote.id, undefined, cote.depuis)}</li>
            ))}
          </ul>
        </div>
      </div>
      <p className="text-xs text-ink-muted" aria-live="polite" data-testid="workflow-graph-focus">
        {description}
      </p>
    </div>
  );
}

function LegendeDeLaCarte({
  graph,
  toutes,
  onToutes,
  secondaires,
  jokers,
  nom,
  onSelect,
}: {
  graph: Graphe;
  toutes: boolean;
  onToutes: (v: boolean) => void;
  secondaires: number;
  jokers: { cle: string; to: string; actor?: string | null }[];
  nom: (id: string) => string;
  onSelect?: Choix;
}) {
  const noeuds = graph.nodes as NoeudDuGraphe[];
  const comptes = new Map<string, number>();
  for (const n of noeuds) {
    const genre = genreDActeur(n.lane);
    comptes.set(genre, (comptes.get(genre) ?? 0) + 1);
  }
  const replis = defaults(graph);
  return (
    <div className="space-y-2 text-xs text-ink-muted" data-testid="workflow-legend">
      <ul className="flex flex-wrap gap-x-4 gap-y-1" aria-label="who moves the work item">
        {(Object.keys(COULEUR_DU_GENRE) as (keyof typeof COULEUR_DU_GENRE)[])
          .filter((genre) => comptes.has(genre))
          .map((genre) => (
            <li key={genre} className="inline-flex items-center gap-1.5">
              <span aria-hidden className="inline-block h-3 w-[3px]" style={{ background: COULEUR_DU_GENRE[genre].bord }} />
              {LIBELLE_DU_GENRE[genre]} · {comptes.get(genre)}
            </li>
          ))}
      </ul>
      {replis.length > 0 && (
        <div>
          <p className="text-ink">from any agent state</p>
          <ul className="mt-1 space-y-0.5" data-testid="workflow-defaults">
            {replis.map((repli) => (
              <li key={`${repli.label}->${repli.to}`}>
                {repli.label} → {repli.to}
                <span className="sr-only"> (from {repli.from.join(", ")})</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {jokers.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {jokers.map((joker) => (
            <button
              key={joker.cle}
              type="button"
              data-testid={`transition-${joker.cle}`}
              className="border border-line bg-surface px-1.5 py-0.5 font-mono text-[11px] hover:border-line-strong pointer-coarse:min-h-11"
              onClick={() => onSelect?.("edge", joker.cle)}
            >
              from any agent step → {nom(joker.to)}
              {joker.actor ? ` (by ${joker.actor})` : ""}
            </button>
          ))}
        </div>
      )}
      {secondaires > 0 && (
        <label className="inline-flex items-center gap-2">
          <input type="checkbox" checked={toutes} onChange={(event) => onToutes(event.target.checked)} />
          show every retry, rejection and escalation ({secondaires}) — or hover a state for its own
        </label>
      )}
    </div>
  );
}
