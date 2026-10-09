// SPDX-License-Identifier: Apache-2.0
"use client";

import { type RefObject, useEffect, useId, useMemo, useRef, useState } from "react";
import { cn } from "@/lib/cn";
import { relative, shortDate, usd } from "@/lib/format";
import type { WorkItemJourney } from "@/lib/types";
import { placer, RAYON, tronquer, type Placement, type Point } from "./disposition";
import { useLecteur, type Vitesse } from "./lecteur";
import {
  aLInstant,
  debutDePhase,
  type Etape,
  type EtatEtape,
  instants,
  type Instant,
  modeler,
  type Modele,
  raconter,
  type StatutEtape,
  structure,
} from "./modele";
import { PanneauDEtape } from "./panneau-d-etape";

/** La couleur d'un statut, prise dans les jetons de la fondation : clair et sombre suivent. */
const COULEUR: Record<StatutEtape, string> = {
  a_venir: "var(--varga-line-strong)",
  en_cours: "var(--varga-accent-strong)",
  attend: "var(--varga-ink)",
  fait: "var(--varga-ok)",
  renvoye: "var(--varga-warn)",
  echoue: "var(--varga-danger)",
};

/** Ce que dit un statut, en toutes lettres : la couleur ne porte jamais seule le sens. */
export const LIBELLE_DU_STATUT: Record<StatutEtape, string> = {
  a_venir: "not reached",
  en_cours: "in progress",
  attend: "waiting for a person",
  fait: "done",
  renvoye: "sent back",
  echoue: "failed",
};

const LEGENDE: [StatutEtape, string][] = [
  ["en_cours", "in progress"],
  ["attend", "waiting for a person"],
  ["renvoye", "sent back"],
  ["fait", "done"],
  ["echoue", "failed"],
];

const VITESSES: Vitesse[] = [0.5, 1, 2];

/**
 * Le parcours d'un ticket, animé (S22-02 ; revue du 08/10 : « dynamique, lisible… et pour chaque
 * agent on peut voir les logs et actions »).
 *
 * Le chemin du workflow serpente de gauche à droite puis de droite à gauche ; chaque case est une
 * étape portée par un agent (rond), une personne (rond et silhouette), la plateforme (carré) ou le
 * train (gélule). Ce qui tourne pulse, ce qui attend quelqu'un aussi, plus lentement ; une revue qui
 * renvoie trace un arc orangé et compte ses tours ; un détour (reprise de revue, réparation de CI)
 * pend sous sa rangée. En direct, la carte suit l'API ; « Replay » rejoue la vie du ticket événement
 * par événement, à la vitesse choisie, et une phase cliquée y saute. Cliquer une case ouvre son
 * panneau : chaque tentative, ce que l'agent a fait, son journal.
 */
export function CarteDuParcours({
  journey,
  slug,
  itemId,
  largeurInitiale = 1100,
  onDecided,
}: {
  journey: WorkItemJourney;
  slug: string;
  itemId: string;
  largeurInitiale?: number;
  onDecided?: () => void;
}) {
  const modele = useMemo(() => modeler(journey), [journey]);
  const images = useMemo(() => instants(journey), [journey]);
  const lecteur = useLecteur(images.length);
  const t = lecteur.mode === "direct" ? Number.POSITIVE_INFINITY : (images[lecteur.index] ?? Number.POSITIVE_INFINITY);
  const instant = useMemo(() => aLInstant(journey, modele, t), [journey, modele, t]);
  const [choix, setChoix] = useState<string | null>(null);
  const [cadre, largeur] = useLargeur(largeurInitiale);
  const placement = useMemo(() => placer(modele, largeur), [modele, largeur]);
  const etapeChoisie = choix ? modele.parId.get(choix) : undefined;
  const recit = lecteur.mode === "relecture" ? raconter(modele, instant.dernier) : "";

  return (
    <section className="space-y-3" data-testid="parcours" aria-label={`journey of ${journey.tracker_key ?? "the work item"}`}>
      <EnTete journey={journey} modele={modele} instant={instant} />
      <BarreDuLecteur lecteur={lecteur} images={images} recit={recit} />
      <div className={cn("grid gap-4", etapeChoisie && "xl:grid-cols-[minmax(0,1fr)_26rem]")}>
        <div ref={cadre} className="min-w-0">
          <Carte
            modele={modele}
            placement={placement}
            instant={instant}
            cle={journey.tracker_key ?? ""}
            choix={choix}
            onChoisir={(id) => setChoix(id === choix ? null : id)}
          />
          <Exceptions modele={modele} instant={instant} />
        </div>
        {etapeChoisie && (
          <PanneauDEtape
            etape={etapeChoisie}
            etat={instant.etapes.get(etapeChoisie.id)!}
            process={journey.process.find((p) => p.id === etapeChoisie.id)}
            slug={slug}
            itemId={itemId}
            onClose={() => setChoix(null)}
            onDecided={onDecided}
          />
        )}
      </div>
      <Phases journey={journey} modele={modele} instant={instant} images={images} onAller={lecteur.allerA} />
    </section>
  );
}

const LEGENDE_DES_GENRES: [Etape["genre"], string][] = [
  ["agent", "agent"],
  ["human", "person"],
  ["platform", "platform"],
  ["train", "release train"],
];

/**
 * La carte d'un workflow, sans ticket (S22-05 ; « l'affichage est très linéaire, pas comme les
 * captures ») : le même serpentin que le parcours d'un ticket — boucles en arcs, détours sous leur
 * rangée, une forme par porteur —, mais rien n'y bouge : chaque case prend la couleur de qui la porte.
 * Cliquer une case rend son identifiant de transition, que la page d'édition ouvre dans son panneau.
 */
export function CarteEnSerpentin({
  graph,
  process,
  initial,
  choix = null,
  onChoisir,
  largeurInitiale = 1100,
}: {
  graph: WorkItemJourney["graph"];
  process: WorkItemJourney["process"];
  initial?: string | null;
  choix?: string | null;
  onChoisir?: (transitionId: string) => void;
  largeurInitiale?: number;
}) {
  const modele = useMemo(() => modeler({ graph, process: process ?? [], initial: initial ?? null }), [graph, process, initial]);
  const instant = useMemo(() => structure(modele), [modele]);
  const [cadre, largeur] = useLargeur(largeurInitiale);
  const placement = useMemo(() => placer(modele, largeur), [modele, largeur]);
  return (
    <section className="space-y-3" data-testid="carte-en-serpentin" aria-label="workflow diagram">
      <ul className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-muted" aria-label="who carries each step">
        {LEGENDE_DES_GENRES.map(([genre, libelle]) => (
          <li key={genre} className="inline-flex items-center gap-1.5">
            <span
              aria-hidden
              className="inline-block size-3 rounded-full border-2"
              style={{ borderColor: COULEUR_DU_GENRE[genre] }}
            />
            {libelle}
          </li>
        ))}
        <li className="inline-flex items-center gap-1.5">
          <span aria-hidden className="inline-block h-0.5 w-4 border-t-2 border-dashed border-line-strong" />
          sent back, or a detour
        </li>
      </ul>
      <div ref={cadre} className="min-w-0">
        <Carte
          modele={modele}
          placement={placement}
          instant={instant}
          cle=""
          choix={choix}
          onChoisir={(id) => onChoisir?.(id)}
          statique
        />
        <Exceptions modele={modele} instant={instant} />
      </div>
    </section>
  );
}

/** La largeur du cadre, suivie : la carte change de nombre de colonnes, elle ne déborde pas. */
function useLargeur(initiale: number): [RefObject<HTMLDivElement | null>, number] {
  const cadre = useRef<HTMLDivElement>(null);
  const [largeur, setLargeur] = useState(initiale);
  useEffect(() => {
    const element = cadre.current;
    if (!element || typeof ResizeObserver === "undefined") return;
    const observateur = new ResizeObserver(([entree]) => {
      const mesure = Math.floor(entree?.contentRect.width ?? 0);
      if (mesure > 0) setLargeur(mesure);
    });
    observateur.observe(element);
    return () => observateur.disconnect();
  }, []);
  return [cadre, largeur];
}

function EnTete({ journey, modele, instant }: { journey: WorkItemJourney; modele: Modele; instant: Instant }) {
  const cout = journey.steps.reduce((somme, s) => somme + (s.cost_usd ?? 0), 0);
  const actives = modele.etapes.concat(modele.detours.map((d) => d.etape)).filter((e) => {
    const s = instant.etapes.get(e.id)?.statut;
    return s === "en_cours" || s === "attend";
  });
  return (
    <p className="flex flex-wrap items-baseline gap-x-3 gap-y-1 text-sm" data-testid="parcours-maintenant">
      <span className="text-ink-muted">
        {journey.workflow_name} v{journey.workflow_version}
      </span>
      <span>
        now in <strong>{modele.nom(instant.etat)}</strong>
        {actives.length > 0 && (
          <span className="text-ink-muted">
            {" "}
            ·{" "}
            {actives
              .map((e) => `${e.libelle} ${instant.etapes.get(e.id)?.statut === "attend" ? "awaited" : "working"}`)
              .join(", ")}
          </span>
        )}
      </span>
      <span className="text-ink-muted">· {usd(cout)} spent by agents</span>
    </p>
  );
}

function BarreDuLecteur({ lecteur, images, recit }: { lecteur: ReturnType<typeof useLecteur>; images: number[]; recit: string }) {
  const direct = lecteur.mode === "direct";
  const bouton = "inline-flex items-center gap-1.5 rounded border px-2.5 py-1 text-xs font-medium transition-colors";
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2" role="toolbar" aria-label="replay the journey">
        {lecteur.enLecture ? (
          <button
            type="button"
            aria-label="Pause the replay"
            className={cn(bouton, "border-ink bg-ink text-surface")}
            onClick={lecteur.pause}
          >
            <span aria-hidden>❚❚</span> Pause
          </button>
        ) : (
          <button
            type="button"
            className={cn(bouton, "border-ink bg-ink text-surface")}
            onClick={lecteur.lire}
            disabled={images.length === 0}
            aria-label={direct ? "Replay the journey" : "Play the replay"}
          >
            <span aria-hidden>▶</span> {direct ? "Replay" : "Play"}
          </button>
        )}
        <button
          type="button"
          className={cn(bouton, "border-line-strong hover:border-ink")}
          onClick={lecteur.recommencer}
          disabled={images.length === 0}
          aria-label="Restart the replay"
        >
          <span aria-hidden>↺</span> Restart
        </button>
        <span className="inline-flex overflow-hidden rounded border border-line-strong" role="group" aria-label="speed">
          {VITESSES.map((v) => (
            <button
              key={v}
              type="button"
              aria-pressed={lecteur.vitesse === v}
              className={cn(
                "px-2 py-1 font-mono text-xs",
                lecteur.vitesse === v ? "bg-surface-sunken text-ink" : "text-ink-muted hover:text-ink",
              )}
              onClick={() => lecteur.choisirLaVitesse(v)}
            >
              {v}×
            </button>
          ))}
        </span>
        <button
          type="button"
          aria-pressed={direct}
          className={cn(
            bouton,
            direct ? "border-accent-strong text-accent-strong" : "border-line-strong text-ink-muted hover:text-ink",
          )}
          onClick={lecteur.direct}
          aria-label="Live: follow the work item"
        >
          <span aria-hidden className={cn("inline-block size-1.5 rounded-full bg-current", direct && "parcours-pouls-point")} />
          Live
        </button>
        {!direct && images.length > 0 && (
          <label className="ml-1 inline-flex min-w-48 flex-1 items-center gap-2 text-xs text-ink-muted">
            <input
              type="range"
              min={0}
              max={images.length - 1}
              value={lecteur.index}
              onChange={(event) => lecteur.allerA(Number(event.target.value))}
              className="flex-1 accent-[var(--varga-accent-strong)]"
              aria-label="moment of the journey"
            />
            <span className="whitespace-nowrap font-mono" data-testid="parcours-moment">
              {lecteur.index + 1} / {images.length} · {shortDate(new Date(images[lecteur.index]!).toISOString())}
            </span>
          </label>
        )}
        <ul className="ml-auto flex flex-wrap gap-x-3 gap-y-1 text-xs text-ink-muted" aria-label="legend">
          {LEGENDE.map(([statut, libelle]) => (
            <li key={statut} className="inline-flex items-center gap-1.5">
              <Pastille statut={statut} />
              {libelle}
            </li>
          ))}
        </ul>
      </div>
      <p className={cn("min-h-5 text-sm", recit ? "text-ink" : "text-ink-muted")} aria-live="polite" data-testid="parcours-recit">
        {recit || (direct ? "Live: the map follows the work item. Replay plays its life back, event by event." : "")}
      </p>
    </div>
  );
}

/**
 * La pastille d'un statut dans la légende, dessinée comme la case de la carte (S23-12) : un trait
 * de couleur ne suffisait pas — « in progress » (turquoise) et « done » (vert) se confondaient.
 * Ce qui vit porte un anneau autour, ce qui tourne est rempli ; ce qui est fait ne porte ni l'un
 * ni l'autre.
 */
function Pastille({ statut }: { statut: StatutEtape }) {
  const vivante = statut === "en_cours" || statut === "attend";
  return (
    <span
      aria-hidden
      className="inline-flex size-4 shrink-0 items-center justify-center rounded-full border"
      style={{ borderColor: vivante ? COULEUR[statut] : "transparent" }}
    >
      <span
        className="size-2.5 rounded-full border-2"
        style={{
          borderColor: COULEUR[statut],
          background: statut === "en_cours" ? "var(--varga-accent-soft)" : "var(--varga-surface)",
        }}
      />
    </span>
  );
}

function Carte({
  modele,
  placement,
  instant,
  cle,
  choix,
  onChoisir,
  statique = false,
}: {
  modele: Modele;
  placement: Placement;
  instant: Instant;
  cle: string;
  choix: string | null;
  onChoisir: (id: string) => void;
  /** La carte d'un workflow sans ticket : chaque case prend la couleur de qui la porte (S22-05). */
  statique?: boolean;
}) {
  const toutes = useMemo(() => [...modele.etapes, ...modele.detours.map((d) => d.etape)], [modele]);
  const fini = instant.etat === modele.chemin.at(-1);
  // Les pointes de flèche des arcs : une pour l'arc pris (orangé), une pour la structure (gris).
  const fleche = useId().replace(/:/g, "");
  return (
    <div className="overflow-x-auto">
      <div
        className="relative"
        style={{ height: placement.hauteur, width: placement.largeur }}
        role="group"
        aria-label="the work item's path"
      >
        <svg aria-hidden className="absolute inset-0 overflow-visible" width={placement.largeur} height={placement.hauteur}>
          <defs>
            {(
              [
                ["pris", "var(--varga-warn)"],
                ["structure", "var(--varga-line-strong)"],
              ] as const
            ).map(([nom, couleur]) => (
              <marker
                key={nom}
                id={`${fleche}-${nom}`}
                viewBox="0 0 10 10"
                refX={8}
                refY={5}
                markerWidth={7}
                markerHeight={7}
                orient="auto-start-reverse"
              >
                <path d="M 0 0 L 10 5 L 0 10 z" fill={couleur} />
              </marker>
            ))}
          </defs>
          {placement.segments.map((segment, k) => {
            const actif = instant.etat === segment.etat && !fini;
            const parcouru = instant.visites.has(segment.etat) && !actif;
            const etiquette = segment.etiquette;
            return (
              <g
                key={`${segment.etat}-${k}`}
                data-segment={segment.etat}
                data-statut={actif ? "actif" : parcouru ? "parcouru" : "a_venir"}
              >
                <path
                  d={segment.d}
                  fill="none"
                  stroke={actif || parcouru ? "var(--varga-accent-strong)" : "var(--varga-line-strong)"}
                  strokeWidth={actif || parcouru ? 3 : 2}
                  strokeLinecap="round"
                  // Franchi, le tronçon se trace : `pathLength` ramène sa longueur à 1, l'animation fait le reste.
                  pathLength={actif || parcouru ? 1 : undefined}
                  strokeDasharray={actif || parcouru ? 1 : undefined}
                  className={cn("parcours-trait", (actif || parcouru) && "parcours-trace")}
                />
                {actif && (
                  <circle r={4.5} fill="var(--varga-accent-strong)" className="parcours-anime">
                    <animateMotion dur="1.8s" repeatCount="indefinite" path={segment.d} />
                  </circle>
                )}
                <text
                  stroke="var(--varga-surface)"
                  strokeWidth={4}
                  paintOrder="stroke"
                  x={etiquette.x}
                  y={etiquette.y}
                  textAnchor={etiquette.ancre}
                  fontSize={11}
                  fontWeight={actif ? 600 : 400}
                  fill={actif ? "var(--varga-accent-strong)" : "var(--varga-ink-subtle)"}
                >
                  <title>{modele.nom(segment.etat)}</title>
                  {tronquer(actif ? `now · ${modele.nom(segment.etat)}` : modele.nom(segment.etat), etiquette.largeurMax)}
                </text>
              </g>
            );
          })}
          {modele.arcs.map((arc) => {
            const trace = placement.arcs.get(arc.cle);
            if (!trace) return null;
            const pris = instant.arcs.get(arc.cle) ?? {
              fois: 0,
              dernier: false,
            };
            return (
              <g key={arc.cle} data-arc={arc.cle} data-fois={pris.fois}>
                <path
                  d={trace.d}
                  fill="none"
                  stroke={pris.fois > 0 ? "var(--varga-warn)" : "var(--varga-line-strong)"}
                  strokeWidth={pris.fois > 0 ? 2.5 : 1.5}
                  strokeDasharray={pris.fois > 0 ? undefined : "4 5"}
                  opacity={pris.fois > 0 ? 1 : 0.55}
                  markerEnd={`url(#${fleche}-${pris.fois > 0 ? "pris" : "structure"})`}
                  className="parcours-trait"
                />
                {pris.dernier && (
                  <circle r={4} fill="var(--varga-warn)" className="parcours-anime">
                    <animateMotion dur="1.4s" repeatCount="indefinite" path={trace.d} />
                  </circle>
                )}
                {pris.fois > 0 && arc.genre === "renvoi" && (
                  <text
                    stroke="var(--varga-surface)"
                    strokeWidth={4}
                    paintOrder="stroke"
                    x={trace.etiquette.x}
                    y={trace.etiquette.y}
                    textAnchor="middle"
                    fontSize={11}
                    fontWeight={600}
                    fill="var(--varga-warn)"
                  >
                    {arc.libelle}
                    {pris.fois > 1 ? ` ×${pris.fois}` : ""}
                  </text>
                )}
              </g>
            );
          })}
          <Borne point={placement.debut} />
          <Borne point={placement.fin} atteinte={fini} />
          {toutes.map((etape) => {
            const point = placement.etapes.get(etape.id);
            const etat = instant.etapes.get(etape.id);
            if (!point || !etat) return null;
            return (
              <Case key={etape.id} etape={etape} etat={etat} point={point} choisie={choix === etape.id} statique={statique} />
            );
          })}
        </svg>
        <Libelle
          point={placement.debut}
          largeur={placement.largeur / placement.colonnes}
          titre="Work item in"
          sous={statique ? modele.nom(modele.chemin[0] ?? "") : cle}
          decalage={30}
        />
        <Libelle
          point={placement.fin}
          largeur={placement.largeur / placement.colonnes}
          titre={fini ? `${modele.nom(modele.chemin.at(-1) ?? "")} ✓` : modele.nom(modele.chemin.at(-1) ?? "")}
          sous="the end of the path"
          decalage={30}
        />
        {toutes.map((etape) => {
          const point = placement.etapes.get(etape.id);
          const etat = instant.etapes.get(etape.id);
          if (!point || !etat) return null;
          return (
            <BoutonDEtape
              key={etape.id}
              etape={etape}
              etat={etat}
              point={point}
              largeur={placement.largeur / placement.colonnes}
              choisie={choix === etape.id}
              onChoisir={() => onChoisir(etape.id)}
              statique={statique}
            />
          );
        })}
      </div>
    </div>
  );
}

function Borne({ point, atteinte = false }: { point: Point; atteinte?: boolean }) {
  return (
    <rect
      x={point.x - 3}
      y={point.y - 20}
      width={6}
      height={40}
      rx={3}
      fill={atteinte ? "var(--varga-ok)" : "var(--varga-ink)"}
    />
  );
}

/** La forme d'une case : le rond d'un agent, la silhouette d'une personne, le carré de la plateforme, la gélule du train. */
/** Sans ticket, la couleur dit qui porte l'étape : turquoise l'agent, noir la personne, gris la plateforme, vert le train. */
const COULEUR_DU_GENRE: Record<Etape["genre"], string> = {
  agent: "var(--varga-accent-strong)",
  human: "var(--varga-ink)",
  platform: "var(--varga-ink-muted)",
  train: "var(--varga-ok)",
};

function Case({
  etape,
  etat,
  point,
  choisie,
  statique = false,
}: {
  etape: Etape;
  etat: EtatEtape;
  point: Point;
  choisie: boolean;
  statique?: boolean;
}) {
  const couleur = statique ? COULEUR_DU_GENRE[etape.genre] : COULEUR[etat.statut];
  const vivante = etat.statut === "en_cours" || etat.statut === "attend";
  const plein = statique || etat.statut !== "a_venir";
  const fond = etat.statut === "en_cours" ? "var(--varga-accent-soft)" : "var(--varga-surface)";
  const { x, y } = point;
  return (
    <g data-etape={etape.id} data-statut={etat.statut}>
      {vivante && (
        <circle
          cx={x}
          cy={y}
          r={RAYON + 4}
          fill="none"
          stroke={couleur}
          strokeWidth={2}
          className={etat.statut === "attend" ? "parcours-pouls-lent" : "parcours-pouls"}
        />
      )}
      {choisie && <circle cx={x} cy={y} r={RAYON + 7} fill="none" stroke="var(--varga-focus)" strokeWidth={2} />}
      {etape.genre === "platform" ? (
        <rect
          x={x - RAYON + 1}
          y={y - RAYON + 1}
          width={2 * RAYON - 2}
          height={2 * RAYON - 2}
          rx={5}
          fill={fond}
          stroke={couleur}
          strokeWidth={3}
          className="parcours-forme"
        />
      ) : etape.genre === "train" ? (
        <rect
          x={x - RAYON - 7}
          y={y - RAYON + 2}
          width={2 * RAYON + 14}
          height={2 * RAYON - 4}
          rx={RAYON - 2}
          fill={fond}
          stroke={couleur}
          strokeWidth={3}
          className="parcours-forme"
        />
      ) : (
        <circle cx={x} cy={y} r={RAYON} fill={fond} stroke={couleur} strokeWidth={3} className="parcours-forme" />
      )}
      {etape.genre === "human" ? (
        <g fill={plein ? couleur : "var(--varga-ink-muted)"}>
          <circle cx={x} cy={y - 3.5} r={3.4} />
          <path d={`M ${x - 6.5} ${y + 7} Q ${x} ${y - 1.5} ${x + 6.5} ${y + 7} Z`} />
        </g>
      ) : etape.genre === "train" ? (
        <g fill={plein ? couleur : "var(--varga-ink-muted)"}>
          {[-7, 0, 7].map((dx) => (
            <circle key={dx} cx={x + dx} cy={y} r={2.2} />
          ))}
        </g>
      ) : (
        plein && <circle cx={x} cy={y} r={5} fill={couleur} />
      )}
    </g>
  );
}

function Libelle({
  point,
  largeur,
  titre,
  sous,
  decalage,
}: {
  point: Point;
  largeur: number;
  titre: string;
  sous: string;
  decalage: number;
}) {
  return (
    <div
      className="pointer-events-none absolute text-center"
      style={{
        left: point.x - largeur / 2,
        top: point.y + decalage,
        width: largeur,
      }}
    >
      <p className="mx-auto line-clamp-2 w-fit max-w-full break-words bg-surface px-1 text-[13px] leading-4 font-semibold text-ink">
        {titre}
      </p>
      <p className="mx-auto mt-0.5 w-fit max-w-full truncate bg-surface px-1 text-xs text-ink-muted">{sous}</p>
    </div>
  );
}

/** Ce qu'une case dit sous elle : la tentative en cours, le verdict, les preuves, qui a décidé. */
function detailDe(etape: Etape, etat: EtatEtape): string | null {
  const derniere = etat.tentatives.at(-1);
  if (etat.verdict) return etat.verdict === "approve" ? "✓ approved" : etat.verdict.replace(/_/g, " ");
  const preuves = (derniere?.evidence ?? {}) as Record<string, unknown>;
  if (typeof preuves.tests_run === "number") {
    const echecs = Number(preuves.tests_failed ?? 0);
    return echecs > 0 ? `✗ ${echecs} of ${preuves.tests_run} tests fail` : `✓ ${preuves.tests_run} tests pass`;
  }
  if (etape.genre === "human") {
    if (etat.statut === "attend" && derniere?.due_at) return `due ${relative(derniere.due_at)}`;
    if (derniere?.decided_by) return `${derniere.status} by ${derniere.decided_by.split("@")[0]}`;
  }
  if (etape.genre === "train" && derniere)
    return derniere.decided_by ? `approved by ${derniere.decided_by.split("@")[0]}` : derniere.status;
  if (etape.genre === "platform" && etape.gates.length) {
    const coche = etat.statut === "fait" ? "☑" : "☐";
    return etape.gates.map((g) => `${coche} ${g.replace(/_/g, " ")}`).join("  ");
  }
  return null;
}

function tourDe(etape: Etape, etat: EtatEtape): string | null {
  if (etat.tours < 2) return null;
  const sur = etape.maxTours ? ` of ${etape.maxTours}` : "";
  if (etat.statut === "fait")
    return `${etat.verdict === "approve" || etape.genre === "human" ? "approved" : "passed"} in round ${etat.tours}`;
  return `round ${etat.tours}${sur}`;
}

function BoutonDEtape({
  etape,
  etat,
  point,
  largeur,
  choisie,
  onChoisir,
  statique = false,
}: {
  etape: Etape;
  etat: EtatEtape;
  point: Point;
  largeur: number;
  choisie: boolean;
  onChoisir: () => void;
  statique?: boolean;
}) {
  const detail = detailDe(etape, etat);
  const tour = tourDe(etape, etat);
  const genre = {
    agent: "agent",
    human: "person",
    platform: "platform",
    train: "release train",
  }[etape.genre];
  const etiquette = [
    `${etape.libelle}, ${genre}, ${etape.action}`,
    statique ? null : LIBELLE_DU_STATUT[etat.statut],
    tour,
    detail,
    `${etat.tentatives.length} attempt${etat.tentatives.length === 1 ? "" : "s"}`,
  ]
    .filter(Boolean)
    .join(" — ");
  return (
    <button
      type="button"
      data-testid={`etape-${etape.id}`}
      data-statut={etat.statut}
      aria-label={etiquette}
      aria-pressed={choisie}
      title={etape.phrase?.replaceAll("`", "") ?? undefined}
      onClick={onChoisir}
      className="group absolute flex flex-col items-center rounded text-center focus-visible:outline-2 focus-visible:outline-offset-2"
      style={{ left: point.x - (largeur - 8) / 2, top: point.y - RAYON - 4, width: largeur - 8 }}
    >
      <span aria-hidden className="block" style={{ height: 2 * RAYON + 8 }} />
      <span className="mx-auto line-clamp-2 block w-fit max-w-full break-words bg-surface px-1 text-[13px] leading-4 font-semibold text-ink group-hover:underline">
        {etape.libelle}
      </span>
      <span className="mx-auto mt-0.5 line-clamp-2 block w-fit max-w-full break-words bg-surface px-1 text-xs leading-4 text-ink-muted">
        {etape.action}
      </span>
      {(tour || detail) && (
        <span className="mx-auto mt-0.5 flex w-fit max-w-full flex-wrap items-center justify-center gap-1 bg-surface px-1 text-xs leading-4">
          {tour && (
            <span
              data-testid={`tour-${etape.id}`}
              className={cn(
                "whitespace-nowrap rounded-full border px-1.5 text-[11px] font-medium",
                etat.statut === "fait" ? "border-ok text-ok" : "border-warn text-warn",
              )}
            >
              {tour}
            </span>
          )}
          {detail && (
            <span
              className={cn(
                "min-w-0 truncate",
                etat.statut === "renvoye" || etat.statut === "echoue" || /fail|changes/.test(detail)
                  ? "text-warn"
                  : etat.statut === "fait"
                    ? "text-ok"
                    : "text-ink-muted",
              )}
            >
              {detail}
            </span>
          )}
        </span>
      )}
    </button>
  );
}

function Exceptions({ modele, instant }: { modele: Modele; instant: Instant }) {
  if (modele.exceptions.length === 0) return null;
  return (
    <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-ink-muted" data-testid="parcours-exceptions">
      <span>when something goes wrong:</span>
      {modele.exceptions.map((exception) => {
        const ici = instant.etat === exception.id;
        const passe = instant.visites.has(exception.id);
        return (
          <span
            key={exception.id}
            data-statut={ici ? "actif" : passe ? "passe" : "a_venir"}
            className={cn(
              "inline-flex items-center gap-1.5 rounded border px-2 py-0.5",
              ici ? "border-ink font-medium text-ink" : passe ? "border-warn text-warn" : "border-line text-ink-muted",
            )}
          >
            {ici && <span aria-hidden className="parcours-pouls-point inline-block size-1.5 rounded-full bg-current" />}
            {exception.display}
            {ici ? " — here now" : passe ? " — went through" : ""}
          </span>
        );
      })}
    </div>
  );
}

function Phases({
  journey,
  modele,
  instant,
  images,
  onAller,
}: {
  journey: WorkItemJourney;
  modele: Modele;
  instant: Instant;
  images: number[];
  onAller: (index: number) => void;
}) {
  return (
    <nav aria-label="stages of the journey" className="flex flex-wrap items-center gap-2 border-t border-line pt-3">
      <ol className="flex flex-wrap gap-2">
        {modele.phases.map((phase, i) => {
          const statuts = phase.etapes.map((id) => instant.etapes.get(id)?.statut);
          const faite = statuts.every((s) => s === "fait");
          const courante = instant.phase === i && !faite;
          const debut = debutDePhase(journey, modele, phase);
          const index = debut === null ? -1 : images.findIndex((t) => t >= debut);
          return (
            <li key={phase.cle}>
              <button
                type="button"
                disabled={index < 0}
                aria-current={courante ? "step" : undefined}
                onClick={() => onAller(index)}
                data-testid={`phase-${i}`}
                className={cn(
                  "inline-flex items-center gap-2 rounded-full border px-2.5 py-1 text-xs transition-colors disabled:cursor-not-allowed disabled:opacity-50",
                  courante ? "border-accent-strong text-ink" : "border-line-strong text-ink-muted hover:text-ink",
                )}
              >
                <span
                  aria-hidden
                  className={cn(
                    "inline-flex size-5 items-center justify-center rounded-full font-mono text-[11px]",
                    faite
                      ? "bg-ok text-surface"
                      : courante
                        ? "bg-accent-strong text-surface"
                        : "bg-surface-sunken text-ink-muted",
                  )}
                >
                  {i + 1}
                </span>
                {phase.libelle}
                <span className="sr-only">{faite ? " (done)" : courante ? " (current)" : ""}</span>
              </button>
            </li>
          );
        })}
      </ol>
      <span className="font-mono text-xs text-ink-muted">click a stage to jump to it</span>
    </nav>
  );
}
