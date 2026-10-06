// SPDX-License-Identifier: Apache-2.0
"use client";

import {
  Background,
  Controls,
  MarkerType,
  MiniMap,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useViewport,
  type BuiltInEdge,
  type Edge,
  type Node,
  type ReactFlowInstance,
} from "@xyflow/react";
import { useMemo, useRef, useState, type CSSProperties, type FocusEvent, type KeyboardEvent } from "react";
import "@xyflow/react/dist/style.css";

type GraphNode = {
  id: string;
  display: string;
  lane?: string;
  terminal?: boolean;
  tracker_status?: string | null;
};

type GraphEdge = {
  id?: string;
  from: string;
  to: string;
  kind?: string;
  label?: string;
  actor?: string | null;
  gates?: string[];
};

/** Un couloir par type d'acteur : on lit le workflow de haut en bas, comme un plan de voie. */
const LANES = ["agent", "human", "system", "train", "wait", "terminal"] as const;

/**
 * Les couleurs des couloirs, prises dans les jetons du design system de la fondation. La carte les
 * écrivait `rgb(var(--agent))` : des variables que la console ne définit plus depuis qu'elle porte
 * ce design system — couleurs invalides, donc transparentes, et une carte sans bords ni flèches
 * (relevé sur le dev le 06/10).
 */
const LANE_COLOR: Record<string, string> = {
  agent: "var(--varga-accent-strong)",
  human: "var(--varga-ink)",
  system: "var(--varga-ink-muted)",
  train: "var(--varga-ok)",
  wait: "var(--varga-warn)",
  terminal: "var(--varga-ink-subtle)",
};

/** Largeur d'un état, et pas d'une colonne à la suivante : la place d'une étiquette entre les deux. */
const NODE_WIDTH = 176;
const NODE_HEIGHT = 40;
const COLUMN_WIDTH = 288;
/** Deux états du même couloir et de la même colonne s'empilent, à cet intervalle. */
const ROW_HEIGHT = 62;
const LANE_PADDING = 22;
/** La marge où se lit le nom des couloirs, à gauche de la première colonne. */
const GUTTER = 120;
/** Au-delà, une étiquette d'arête nominale donne le nombre de garanties, pas leurs noms. */
const ETIQUETTE_MAX = 16;
/** En dessous, le texte d'un état ne se lit plus : la carte s'ouvre à cette taille, sur son début. */
const ZOOM_LISIBLE = 0.8;

/** Un couloir dessiné derrière les états : son nom, sa couleur, et la bande qu'il occupe. */
export type Couloir = { lane: string; color: string; top: number; height: number };

/** React Flow habillé aux jetons du design system : clair ou sombre, comme le reste de la console. */
const THEME = {
  "--xy-background-color": "transparent",
  "--xy-background-pattern-color": "var(--varga-line)",
  "--xy-edge-stroke": "var(--varga-line-strong)",
  "--xy-edge-label-background-color": "var(--varga-surface)",
  "--xy-edge-label-color": "var(--varga-ink-muted)",
  "--xy-controls-button-background-color": "var(--varga-surface-muted)",
  "--xy-controls-button-background-color-hover": "var(--varga-line)",
  "--xy-controls-button-color": "var(--varga-ink)",
  "--xy-controls-button-color-hover": "var(--varga-ink)",
  "--xy-controls-button-border-color": "var(--varga-line)",
  "--xy-controls-box-shadow": "none",
  // La vue d'ensemble : le cadre dessiné sur le fond de la console, le reste voilé.
  "--xy-minimap-background-color": "var(--varga-surface)",
  "--xy-minimap-mask-background-color": "color-mix(in srgb, var(--varga-surface-muted) 70%, transparent)",
  "--xy-minimap-mask-stroke-color": "var(--varga-line-strong)",
  "--xy-minimap-mask-stroke-width": "1",
  "--xy-minimap-node-background-color": "var(--varga-line-strong)",
} as CSSProperties;

/**
 * Graphe du workflow en couloirs. La disposition est **déterministe** : deux validations
 * du même YAML donnent la même carte, sinon relire un diff de workflow serait un jeu de
 * piste.
 *
 * Le chemin nominal se lit SEUL : les arêtes secondaires (rejet, reprise, escalade) — treize
 * pointillés sur le gabarit par défaut, qui croisaient toute la carte — ne se montrent que pour
 * l'état survolé ou parcouru au clavier, ou toutes, sur demande. Un workflow qui ne tient pas
 * lisible dans le cadre s'ouvre sur son début, avec une vue d'ensemble : ce qui dépasse se voit.
 *
 * Le graphe se parcourt **au clavier** : Tab atteint les états dans l'ordre de lecture
 * (colonne par colonne), ← et → suivent les transitions, ↑ et ↓ passent d'un état à
 * l'autre, Début/Fin sautent aux extrémités. L'état sous le curseur est décrit sous la
 * carte, avec ses transitions sortantes — c'est cette phrase qu'un lecteur d'écran lit.
 */
export function WorkflowGraph({
  graph,
  onSelect,
}: {
  graph: { nodes: unknown[]; edges: unknown[] };
  /** Un état ou une transition choisis (clic, ou Entrée sur un état) : la carte devient éditable. */
  onSelect?: (kind: "node" | "edge", id: string) => void;
}) {
  const { nodes, edges, lanes, largeur, hauteur } = useMemo(() => layout(graph), [graph]);
  const nav = useMemo(() => navigation(graph), [graph]);
  const [focused, setFocused] = useState<string | null>(null);
  const [survol, setSurvol] = useState<string | null>(null);
  const [toutes, setToutes] = useState(false);
  const [apercu, setApercu] = useState(false);
  const affichees = useMemo(() => aretesAffichees(edges, toutes, [survol, focused]), [edges, toutes, survol, focused]);
  const secondaires = edges.filter((edge) => edge.data?.secondaire).length;
  const container = useRef<HTMLDivElement>(null);

  function focusNode(id: string) {
    const element = container.current?.querySelector<HTMLElement>(
      `.react-flow__node[data-id="${CSS.escape(id)}"]`,
    );
    element?.focus();
  }

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    const current = (event.target as HTMLElement).closest<HTMLElement>(".react-flow__node")?.dataset.id;
    if (!current) return;
    if (event.key === "Enter" && onSelect) {
      event.preventDefault();
      onSelect("node", current);
      return;
    }
    const target = nav.move(current, event.key);
    if (target === undefined) return;
    event.preventDefault();
    event.stopPropagation();
    if (target !== null) focusNode(target);
  }

  /**
   * Le cadrage d'ouverture. Un workflow qui tient lisible dans le cadre s'y ajuste ; un workflow
   * plus long (le gabarit par défaut a huit colonnes) s'ouvrait réduit à moitié, son texte
   * illisible : il s'ouvre alors à une taille lisible, sur son début, et se parcourt en glissant —
   * le bouton d'ajustement le montre encore en entier.
   */
  function cadrer(instance: ReactFlowInstance) {
    const vue = ouverture(container.current?.getBoundingClientRect(), largeur, hauteur);
    if (vue === "ajuster") void instance.fitView({ padding: 0.12, maxZoom: 1 });
    else void instance.setViewport(vue);
    setApercu(vue !== "ajuster");
  }

  function onFocus(event: FocusEvent<HTMLDivElement>) {
    const id = (event.target as HTMLElement).closest<HTMLElement>(".react-flow__node")?.dataset.id;
    setFocused(id ?? null);
  }

  /** Le clavier quitte la carte : ses reprises se replient, l'aide revient sous la carte. */
  function onBlur(event: FocusEvent<HTMLDivElement>) {
    const suivant = event.relatedTarget;
    if (!(suivant instanceof HTMLElement) || !container.current?.contains(suivant)) setFocused(null);
  }

  return (
    <div className="space-y-2">
      {secondaires > 0 && (
        <label className="flex items-center gap-2 text-xs text-ink-muted">
          <input type="checkbox" checked={toutes} onChange={(event) => setToutes(event.target.checked)} />
          show every retry, rejection and escalation ({secondaires}) — otherwise, hover or focus a state for its own
        </label>
      )}
      <div
        ref={container}
        className="carte-workflow relative h-[30rem] w-full overflow-hidden rounded border border-line bg-surface"
        style={THEME}
        data-testid="workflow-graph"
        onKeyDownCapture={onKeyDown}
        onFocusCapture={onFocus}
        onBlurCapture={onBlur}
      >
        <ReactFlowProvider>
          <Couloirs couloirs={lanes} largeur={largeur} />
          <ReactFlow
            nodes={nodes}
            edges={affichees}
            onInit={cadrer}
            // Un petit workflow ne se grossit pas : 1 au plus, la taille du texte de la console.
            fitViewOptions={{ padding: 0.12, maxZoom: 1 }}
            minZoom={0.3}
            proOptions={{ hideAttribution: true }}
            nodesDraggable={false}
            nodesConnectable={false}
            edgesFocusable={false}
            onNodeClick={onSelect ? (_, node) => onSelect("node", node.id) : undefined}
            onEdgeClick={onSelect ? (_, edge) => onSelect("edge", edge.id) : undefined}
            onNodeMouseEnter={(_, node) => setSurvol(node.id)}
            onNodeMouseLeave={() => setSurvol(null)}
          >
            <Background gap={24} size={1} />
            <Controls showInteractive={false} position="bottom-right" />
            {apercu && (
              <MiniMap
                position="top-right"
                pannable
                zoomable
                ariaLabel="overview of the whole workflow"
                nodeColor={(node) => String(node.data?.couleur ?? "var(--varga-ink-muted)")}
                nodeBorderRadius={2}
                style={{ width: 168, height: 84 }}
              />
            )}
          </ReactFlow>
        </ReactFlowProvider>
      </div>
      <p className="text-xs text-ink-muted" aria-live="polite" data-testid="workflow-graph-focus">
        {focused
          ? nav.describe(focused)
          : `Drag to move, scroll to zoom, ⛶ fits the whole map. Tab reaches the states; ← → follow transitions, ↑ ↓ change state, Home/End jump to the ends${onSelect ? "; Enter edits the state" : ""}.`}
      </p>
    </div>
  );
}

/**
 * Les arêtes `default` partent de CHAQUE état d'agent vers le même état (une question, un budget
 * dépassé) : dessinées, elles noyaient la carte. Elles placent encore les états d'escalade, mais
 * ne se dessinent plus — la légende les dit une fois (`defaults`).
 */
const isDefault = (edge: GraphEdge) => edge.kind === "default";

/** L'identifiant d'une flèche dessinée : celui de la transition, ou sa place parmi les flèches. */
const idDessine = (edge: GraphEdge, index: number) => edge.id ?? `${edge.from}->${edge.to}-${index}`;

/** La transition derrière une flèche de la carte : les défauts ne se dessinent pas (voir `layout`). */
export function areteDessinee(graph: { nodes: unknown[]; edges: unknown[] }, id: string): GraphEdge | undefined {
  return (graph.edges as GraphEdge[]).filter((edge) => !isDefault(edge)).find((edge, index) => idDessine(edge, index) === id);
}

/**
 * Les arêtes que la carte montre. Le chemin nominal, toujours ; une reprise, un rejet ou une escalade
 * seulement s'ils partent ou arrivent à un état d'`autour` (survolé, parcouru au clavier) — ou tous,
 * sur demande. Une arête cachée garde son identifiant : la choisir, une fois montrée, ouvre la bonne
 * transition.
 */
export function aretesAffichees(edges: Edge[], toutes: boolean, autour: ReadonlyArray<string | null>): Edge[] {
  const pres = new Set(autour.filter((id): id is string => Boolean(id)));
  return edges.map((edge) => {
    if (!edge.data?.secondaire) return edge;
    const visible = toutes || pres.has(edge.source) || pres.has(edge.target);
    return { ...edge, hidden: !visible };
  });
}

/**
 * Comment la carte s'ouvre dans un cadre : ajustée si elle y tient lisible, sinon à `ZOOM_LISIBLE`,
 * sur son début (à gauche), centrée en hauteur. Un cadre sans taille connue (rendu côté serveur,
 * onglet caché) : ajustée.
 */
export function ouverture(
  cadre: { width: number; height: number } | undefined,
  largeur: number,
  hauteur: number,
): "ajuster" | { x: number; y: number; zoom: number } {
  if (!cadre || cadre.width === 0 || cadre.height === 0) return "ajuster";
  const ajuste = Math.min(cadre.width / (largeur + 48), cadre.height / (hauteur + 48), 1);
  if (ajuste >= ZOOM_LISIBLE) return "ajuster";
  return { x: 8, y: Math.max(8, (cadre.height - hauteur * ZOOM_LISIBLE) / 2), zoom: ZOOM_LISIBLE };
}

/** Les couloirs présents, de haut en bas : ceux du DSL dans leur ordre, puis tout autre nom rencontré. */
function couloirsPresents(raw: GraphNode[]): string[] {
  const vus = new Set(raw.map((node) => node.lane ?? "system"));
  const connus = LANES.filter((lane) => vus.has(lane));
  const autres = [...vus].filter((lane) => !(LANES as readonly string[]).includes(lane)).sort();
  return [...connus, ...autres];
}

/** L'étiquette d'une arête nominale : qui la franchit, et ses garanties — leur nombre quand leurs
 * noms ne tiennent pas entre deux colonnes (la vue processus les dit toutes, en clair). */
function etiquette(edge: GraphEdge): string {
  const gates = edge.gates ?? [];
  const noms = [edge.actor, gates.join(", ")].filter(Boolean).join(" · ");
  if (noms.length <= ETIQUETTE_MAX || gates.length === 0) return noms;
  return [edge.actor, `${gates.length} gate${gates.length === 1 ? "" : "s"}`].filter(Boolean).join(" · ");
}

export function layout(graph: { nodes: unknown[]; edges: unknown[] }): {
  nodes: Node[];
  edges: Edge[];
  lanes: Couloir[];
  largeur: number;
  hauteur: number;
} {
  const raw = graph.nodes as GraphNode[];
  const allEdges = graph.edges as GraphEdge[];
  const rawEdges = allEdges.filter((edge) => !isDefault(edge));
  const columns = depths(raw, allEdges);

  // Deux états du même couloir et de la même colonne s'EMPILENT : ils tombaient au même point, et
  // leurs libellés se superposaient (« Intervention humaine » deux fois, illisible).
  const rangs = new Map<string, number>();
  const piles = new Map<string, number>();
  for (const node of raw) {
    const lane = node.lane ?? "system";
    const case_ = `${lane}|${columns.get(node.id) ?? 0}`;
    const rang = piles.get(case_) ?? 0;
    rangs.set(node.id, rang);
    piles.set(case_, rang + 1);
    piles.set(`#${lane}`, Math.max(piles.get(`#${lane}`) ?? 0, rang + 1));
  }

  const lanes: Couloir[] = [];
  const sommets = new Map<string, number>();
  let haut = 0;
  for (const lane of couloirsPresents(raw)) {
    const hauteur = 2 * LANE_PADDING + NODE_HEIGHT + ((piles.get(`#${lane}`) ?? 1) - 1) * ROW_HEIGHT;
    lanes.push({ lane, color: LANE_COLOR[lane] ?? LANE_COLOR.system ?? "var(--varga-ink-muted)", top: haut, height: hauteur });
    sommets.set(lane, haut);
    haut += hauteur;
  }
  const colonnes = Math.max(0, ...raw.map((node) => columns.get(node.id) ?? 0)) + 1;
  const largeur = GUTTER + colonnes * COLUMN_WIDTH;

  const nodes: Node[] = raw.map((node) => {
    const lane = node.lane ?? "system";
    const couleur = LANE_COLOR[lane] ?? LANE_COLOR.system;
    const outgoing = rawEdges.filter((edge) => edge.from === node.id).length;
    return {
      id: node.id,
      position: {
        x: GUTTER + (columns.get(node.id) ?? 0) * COLUMN_WIDTH,
        y: (sommets.get(lane) ?? 0) + LANE_PADDING + (rangs.get(node.id) ?? 0) * ROW_HEIGHT,
      },
      data: { label: node.display, couleur },
      // Les dimensions qu'on donne à React Flow, et non seulement au style : la vue d'ensemble les lit
      // sur l'objet passé — sans elles, elle ne dessinait aucun état (une carte contrôlée ne reçoit pas
      // les mesures en retour). `initial…` ne fixe pas la hauteur : un libellé sur deux lignes grandit.
      initialWidth: NODE_WIDTH,
      initialHeight: NODE_HEIGHT,
      ariaLabel: `${node.display}, ${lane} lane, ${outgoing} outgoing transition${outgoing === 1 ? "" : "s"}`,
      draggable: false,
      // De gauche à droite, comme la lecture : on entre par la gauche, on sort par la droite.
      targetPosition: Position.Left,
      sourcePosition: Position.Right,
      style: {
        width: NODE_WIDTH,
        minHeight: NODE_HEIGHT,
        borderRadius: node.terminal ? 999 : 8,
        border: `1px solid ${node.terminal ? couleur : "var(--varga-line-strong)"}`,
        borderLeft: node.terminal ? `1px solid ${couleur}` : `4px solid ${couleur}`,
        background: "var(--varga-surface-muted)",
        color: "var(--varga-ink)",
        fontSize: 13,
        lineHeight: 1.25,
        padding: "9px 12px",
        textAlign: node.terminal ? "center" : "left",
        fontWeight: node.terminal ? 600 : 500,
        boxShadow: "none",
      },
    } satisfies Node;
  });

  const edges: Edge[] = rawEdges.map((edge, index) => {
    const secondary = Boolean(edge.kind && edge.kind !== "nominal");
    const couleur = secondary ? "var(--varga-ink-muted)" : "var(--varga-accent-strong)";
    return {
      id: idDessine(edge, index),
      source: edge.from,
      target: edge.to,
      type: "smoothstep",
      pathOptions: { borderRadius: 10, offset: 16 },
      // Une arête secondaire (rejet, reprise, escalade) ne montre son libellé qu'au survol : la
      // légende dit ce que veulent dire les pointillés, et dix libellés « échecs épuisés » noyaient
      // le chemin nominal.
      className: secondary ? "arete-secondaire" : "arete-nominale",
      data: { secondaire: secondary },
      label: secondary ? edge.label : etiquette(edge),
      animated: false,
      markerEnd: { type: MarkerType.ArrowClosed, color: couleur, width: 14, height: 14 },
      style: {
        stroke: couleur,
        strokeWidth: secondary ? 1 : 1.5,
        strokeDasharray: secondary ? "4 3" : undefined,
      },
      labelStyle: { fontSize: 10, fill: secondary ? "var(--varga-ink-muted)" : "var(--varga-ink)" },
      labelBgStyle: { fill: "var(--varga-surface)" },
      labelBgPadding: [4, 2],
      labelBgBorderRadius: 3,
    } satisfies BuiltInEdge;
  });

  // Ordre de lecture = ordre du DOM = ordre de Tab : colonne par colonne, puis couloir
  // par couloir. Sans ce tri, Tab suivrait l'ordre du YAML, qui n'est pas celui de la carte.
  nodes.sort((a, b) => a.position.x - b.position.x || a.position.y - b.position.y);
  return { nodes, edges, lanes, largeur, hauteur: haut };
}

/**
 * Les couloirs, dessinés DERRIÈRE la carte et calés sur son viewport : une bande par type d'acteur,
 * son nom à gauche. Ce ne sont pas des nœuds de React Flow — un nœud se tabule, se compte, se lit :
 * un couloir n'est qu'un repère.
 */
function Couloirs({ couloirs, largeur }: { couloirs: Couloir[]; largeur: number }) {
  const { x, y, zoom } = useViewport();
  return (
    <div aria-hidden className="pointer-events-none absolute inset-0" data-testid="couloirs">
      <div
        style={{
          position: "absolute",
          left: 0,
          top: 0,
          transformOrigin: "0 0",
          transform: `translate(${x}px, ${y}px) scale(${zoom})`,
        }}
      >
        {couloirs.map((couloir, index) => (
          <div
            key={couloir.lane}
            style={{
              position: "absolute",
              // La bande déborde à gauche comme à droite : elle couvre le cadre quel que soit le cadrage.
              left: -largeur,
              top: couloir.top,
              width: 3 * largeur,
              height: couloir.height,
              borderTop: index === 0 ? undefined : "1px solid var(--varga-line)",
              background: index % 2 === 0 ? "var(--varga-surface-muted)" : "transparent",
              opacity: 0.85,
            }}
          />
        ))}
      </div>
      {/* Le nom de chaque couloir reste au bord gauche du CADRE, à la hauteur de sa bande : en
          coordonnées de la carte, il sortait du champ dès que l'ajustement centrait les états. */}
      {couloirs.map((couloir) => (
        <span
          key={couloir.lane}
          style={{
            position: "absolute",
            left: 10,
            top: y + couloir.top * zoom + 6,
            fontSize: 10,
            fontWeight: 600,
            letterSpacing: "0.08em",
            textTransform: "uppercase",
            color: couloir.color,
          }}
        >
          {couloir.lane}
        </span>
      ))}
    </div>
  );
}

/** Parcours au clavier : où mène chaque touche depuis un état, et comment le décrire. */
export function navigation(graph: { nodes: unknown[]; edges: unknown[] }): {
  order: string[];
  move: (from: string, key: string) => string | null | undefined;
  describe: (id: string) => string;
} {
  const raw = graph.nodes as GraphNode[];
  const rawEdges = (graph.edges as GraphEdge[]).filter((edge) => !isDefault(edge));
  const order = layout(graph).nodes.map((node) => node.id);
  const byId = new Map(raw.map((node) => [node.id, node]));

  // → suit d'abord une transition nominale ; à défaut, n'importe quelle arête sortante.
  // ← fait de même à rebours. Le premier candidat dans l'ordre du workflow gagne : le
  // parcours est déterministe, comme la carte.
  function follow(from: string, direction: "out" | "in"): string | null {
    const candidates = rawEdges.filter((edge) => (direction === "out" ? edge.from : edge.to) === from);
    const pick = candidates.find((edge) => !edge.kind || edge.kind === "nominal") ?? candidates[0];
    if (!pick) return null;
    return direction === "out" ? pick.to : pick.from;
  }

  function move(from: string, key: string): string | null | undefined {
    const index = order.indexOf(from);
    if (index < 0) return undefined;
    switch (key) {
      case "ArrowRight":
        return follow(from, "out");
      case "ArrowLeft":
        return follow(from, "in");
      case "ArrowDown":
        return order[Math.min(index + 1, order.length - 1)] ?? null;
      case "ArrowUp":
        return order[Math.max(index - 1, 0)] ?? null;
      case "Home":
        return order[0] ?? null;
      case "End":
        return order[order.length - 1] ?? null;
      default:
        return undefined;
    }
  }

  function describe(id: string): string {
    const node = byId.get(id);
    if (!node) return "";
    const outgoing = rawEdges.filter((edge) => edge.from === id);
    const head = `${node.display} (${node.lane ?? "system"} lane${node.terminal ? ", terminal" : ""})`;
    if (outgoing.length === 0) return `${head}: no outgoing transition.`;
    const steps = outgoing.map((edge) => {
      const target = byId.get(edge.to)?.display ?? edge.to;
      const secondary = Boolean(edge.kind && edge.kind !== "nominal");
      if (secondary) return `${target} on ${edge.label ?? edge.kind}`;
      const via = [edge.actor ? `by ${edge.actor}` : null, edge.gates?.length ? `gates ${edge.gates.join(", ")}` : null]
        .filter(Boolean)
        .join(", ");
      return via ? `${target} (${via})` : target;
    });
    return `${head}: → ${steps.join("; → ")}.`;
  }

  return { order, move, describe };
}

/** Profondeur depuis l'état initial, en suivant les arêtes nominales : une colonne par étape. */
function depths(nodes: GraphNode[], edges: GraphEdge[]): Map<string, number> {
  const nominal = edges.filter((edge) => !edge.kind || edge.kind === "nominal");
  // Un état de départ n'est atteint par rien — pas même par une arête secondaire.
  const incoming = new Set(edges.map((edge) => edge.to));
  const roots = nodes.filter((node) => !incoming.has(node.id)).map((node) => node.id);
  const start = roots.length > 0 ? roots : nodes.slice(0, 1).map((node) => node.id);

  const depth = new Map<string, number>(start.map((id) => [id, 0]));
  // Parcours en largeur borné : un workflow cyclique ne doit pas figer la page.
  let frontier = [...start];
  for (let step = 0; step < nodes.length && frontier.length > 0; step += 1) {
    const next: string[] = [];
    for (const id of frontier) {
      for (const edge of nominal.filter((candidate) => candidate.from === id)) {
        const candidate = (depth.get(id) ?? 0) + 1;
        if (!depth.has(edge.to) || candidate > (depth.get(edge.to) ?? 0)) {
          depth.set(edge.to, candidate);
          next.push(edge.to);
        }
      }
    }
    frontier = next;
  }
  // Un état atteint seulement par une arête secondaire (question, rejet, escalade) se place
  // après l'état d'où l'on y tombe, pas en première colonne comme un orphelin.
  for (let step = 0; step < nodes.length; step += 1) {
    let placed = false;
    for (const edge of edges) {
      if (depth.has(edge.to) || !depth.has(edge.from)) continue;
      depth.set(edge.to, (depth.get(edge.from) ?? 0) + 1);
      placed = true;
    }
    if (!placed) break;
  }
  for (const node of nodes) if (!depth.has(node.id)) depth.set(node.id, 0);
  return depth;
}

/** Ce qui arrive depuis n'importe quel état d'agent, dit une fois : `question → Besoin d'un humain`. */
export function defaults(graph: { nodes: unknown[]; edges: unknown[] }): Array<{ label: string; to: string; from: string[] }> {
  const byId = new Map((graph.nodes as GraphNode[]).map((node) => [node.id, node]));
  const grouped = new Map<string, { label: string; to: string; from: string[] }>();
  for (const edge of (graph.edges as GraphEdge[]).filter(isDefault)) {
    const label = edge.label ?? "default";
    const key = `${label}->${edge.to}`;
    const entry = grouped.get(key) ?? { label, to: byId.get(edge.to)?.display ?? edge.to, from: [] };
    entry.from.push(byId.get(edge.from)?.display ?? edge.from);
    grouped.set(key, entry);
  }
  return [...grouped.values()];
}

/** La légende de la carte : les couloirs nommés, dans leur ordre de haut en bas, et les défauts. */
export function WorkflowLegend({ graph }: { graph: { nodes: unknown[]; edges: unknown[] } }) {
  const nodes = graph.nodes as GraphNode[];
  const lanes = LANES.filter((lane) => nodes.some((node) => (node.lane ?? "system") === lane));
  const fallbacks = defaults(graph);
  return (
    <div className="space-y-2 text-xs text-ink-muted" data-testid="workflow-legend">
      <ul className="flex flex-wrap gap-x-4 gap-y-1" aria-label="lanes, top to bottom">
        {lanes.map((lane) => (
          <li key={lane} className="inline-flex items-center gap-1.5">
            <span
              aria-hidden
              className="inline-block h-2.5 w-2.5 rounded-sm border"
              style={{ borderColor: LANE_COLOR[lane], background: LANE_COLOR[lane] }}
            />
            {lane} lane · {nodes.filter((node) => (node.lane ?? "system") === lane).length}
          </li>
        ))}
        <li>
          solid: the nominal path · dashed, on demand: rejection, retry, escalation — hover or focus a state for its
          own
        </li>
      </ul>
      {fallbacks.length > 0 && (
        <div>
          <p className="text-ink">from any agent state</p>
          <ul className="mt-1 space-y-0.5" data-testid="workflow-defaults">
            {fallbacks.map((fallback) => (
              <li key={`${fallback.label}->${fallback.to}`}>
                on {fallback.label} → {fallback.to}
                <span className="sr-only"> (from {fallback.from.join(", ")})</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
