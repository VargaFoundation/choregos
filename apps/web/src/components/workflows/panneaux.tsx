// SPDX-License-Identifier: Apache-2.0
"use client";

import { useState } from "react";
import { Button, Card } from "@/components/ui";
import { useBrouillon } from "@/components/workflows/brouillon";
import { GARANTIES_COURANTES, operations } from "@/components/workflows/operations";

type Noeud = { id: string; display: string; terminal?: boolean; kind?: string };
type Arete = {
  id?: string;
  from: string;
  to: string;
  kind?: string;
  actor?: string | null;
  gates?: string[];
  timeout_hours?: number | null;
};

const champ = "w-full rounded border border-line bg-surface px-2 py-1 text-sm";

/**
 * La cible « un nouvel état » : l'état et la transition qui y mène partent ensemble, et s'annulent
 * ensemble. Les parenthèses n'entrent dans aucun nom d'état : la valeur ne peut pas en masquer un.
 */
const NOUVEL_ETAT = "(new)";

/** Un état de la carte : son libellé, son nom, son genre ; le retirer ; partir de lui vers un autre. */
export function PanneauDEtat({ noeud, etats, acteurs }: { noeud: Noeud; etats: Noeud[]; acteurs: string[] }) {
  const brouillon = useBrouillon();
  const [libelle, setLibelle] = useState(noeud.display);
  const [nom, setNom] = useState(noeud.id);
  return (
    <Card title={`state ${noeud.id}`}>
      <div className="space-y-3 text-sm" data-testid="panneau-etat">
        <form
          className="flex items-end gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            void brouillon.appliquer(operations.libelle(noeud.id, libelle));
          }}
        >
          <label className="flex-1 space-y-1">
            <span className="text-xs text-ink-muted">label</span>
            <input className={champ} value={libelle} onChange={(event) => setLibelle(event.target.value)} />
          </label>
          <Button size="sm" type="submit" disabled={!libelle || libelle === noeud.display}>
            set label
          </Button>
        </form>
        <form
          className="flex items-end gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            void brouillon.appliquer(operations.renommer(noeud.id, nom));
          }}
        >
          <label className="flex-1 space-y-1">
            <span className="text-xs text-ink-muted">name (every reference follows)</span>
            <input className={champ} value={nom} onChange={(event) => setNom(event.target.value)} />
          </label>
          <Button size="sm" type="submit" disabled={!nom || nom === noeud.id}>
            rename
          </Button>
        </form>
        {!noeud.terminal && <FormulaireDEtape depart={noeud.id} etats={etats} acteurs={acteurs} />}
        <Button size="sm" tone="danger" onClick={() => void brouillon.appliquer(operations.retirerLEtat(noeud.id))}>
          remove this state
        </Button>
      </div>
    </Card>
  );
}

/**
 * Une étape de plus : d'un état, vers un état existant ou nouveau, portée par un acteur. L'état et la
 * transition partent ensemble et s'annulent ensemble. Sans `depart`, on choisit d'où l'on part.
 */
export function FormulaireDEtape({ depart, etats, acteurs }: { depart?: string; etats: Noeud[]; acteurs: string[] }) {
  const brouillon = useBrouillon();
  const [de, setDe] = useState(depart ?? "");
  const [vers, setVers] = useState("");
  const [nouveau, setNouveau] = useState("");
  const [par, setPar] = useState(acteurs[0] ?? "");
  const origine = depart ?? de;
  const cible = vers === NOUVEL_ETAT ? nouveau.trim() : vers;
  return (
    <form
      className="flex flex-wrap items-end gap-2"
      aria-label={depart ? `new transition from ${depart}` : "new step"}
      onSubmit={(event) => {
        event.preventDefault();
        const transition = operations.ajouterUneTransition(origine, cible, par);
        const gestes = vers === NOUVEL_ETAT ? [operations.ajouterUnEtat(cible, cible), transition] : [transition];
        void brouillon.appliquer(...gestes).then((ok) => {
          if (!ok) return;
          setVers("");
          setNouveau("");
        });
      }}
    >
      {!depart && (
        <label className="flex-1 space-y-1">
          <span className="text-xs text-ink-muted">from</span>
          <select className={champ} value={de} onChange={(event) => setDe(event.target.value)}>
            <option value="">—</option>
            {etats
              .filter((etat) => !etat.terminal)
              .map((etat) => (
                <option key={etat.id} value={etat.id}>
                  {etat.display}
                </option>
              ))}
          </select>
        </label>
      )}
      <label className="flex-1 space-y-1">
        <span className="text-xs text-ink-muted">{depart ? "new transition to" : "to"}</span>
        <select className={champ} value={vers} onChange={(event) => setVers(event.target.value)}>
          <option value="">—</option>
          {etats
            .filter((etat) => etat.id !== origine)
            .map((etat) => (
              <option key={etat.id} value={etat.id}>
                {etat.display}
              </option>
            ))}
          <option value={NOUVEL_ETAT}>a new state…</option>
        </select>
      </label>
      {vers === NOUVEL_ETAT && (
        <label className="flex-1 space-y-1">
          <span className="text-xs text-ink-muted">name of the new state</span>
          <input className={champ} value={nouveau} onChange={(event) => setNouveau(event.target.value)} />
        </label>
      )}
      <label className="flex-1 space-y-1">
        <span className="text-xs text-ink-muted">by</span>
        <select className={champ} value={par} onChange={(event) => setPar(event.target.value)}>
          {acteurs.map((acteur) => (
            <option key={acteur} value={acteur}>
              {acteur}
            </option>
          ))}
        </select>
      </label>
      <Button size="sm" type="submit" disabled={!origine || !cible || !par || brouillon.occupe}>
        add
      </Button>
    </form>
  );
}

/** Une transition : qui la porte, ses garanties, son délai ; la retirer. */
export function PanneauDeTransition({ arete, acteurs }: { arete: Arete; acteurs: string[] }) {
  const brouillon = useBrouillon();
  const [par, setPar] = useState(arete.actor ?? "");
  const [garantie, setGarantie] = useState("");
  const [heures, setHeures] = useState(arete.timeout_hours ? String(arete.timeout_hours) : "");
  if (!arete.id || (arete.kind && arete.kind !== "nominal")) {
    return (
      <Card title="transition">
        <p className="text-sm text-ink-muted">this arrow is not a transition with an id: edit it in the YAML.</p>
      </Card>
    );
  }
  const id = arete.id;
  return (
    <Card title={`transition ${id}`}>
      <div className="space-y-3 text-sm" data-testid="panneau-transition">
        <form
          className="flex items-end gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            void brouillon.appliquer(operations.acteur(id, par));
          }}
        >
          <label className="flex-1 space-y-1">
            <span className="text-xs text-ink-muted">who moves it</span>
            <select className={champ} value={par} onChange={(event) => setPar(event.target.value)}>
              {acteurs.map((acteur) => (
                <option key={acteur} value={acteur}>
                  {acteur}
                </option>
              ))}
            </select>
          </label>
          <Button size="sm" type="submit" disabled={!par || par === arete.actor}>
            set
          </Button>
        </form>
        <div className="space-y-1">
          <p className="text-xs text-ink-muted">only if</p>
          <ul className="flex flex-wrap gap-2">
            {(arete.gates ?? []).map((nom) => (
              <li key={nom} className="inline-flex items-center gap-1 rounded border border-line px-2 py-0.5 text-xs">
                <code>{nom}</code>
                <button
                  type="button"
                  aria-label={`remove the guarantee ${nom}`}
                  className="text-ink-muted hover:text-danger"
                  onClick={() => void brouillon.appliquer(operations.retirerUneGarantie(id, nom))}
                >
                  ×
                </button>
              </li>
            ))}
          </ul>
          <form
            className="flex items-end gap-2"
            onSubmit={(event) => {
              event.preventDefault();
              void brouillon.appliquer(operations.ajouterUneGarantie(id, garantie)).then((ok) => ok && setGarantie(""));
            }}
          >
            <input
              aria-label="guarantee to add"
              list="garanties-courantes"
              className={champ}
              value={garantie}
              onChange={(event) => setGarantie(event.target.value)}
            />
            <datalist id="garanties-courantes">
              {GARANTIES_COURANTES.map((nom) => (
                <option key={nom} value={nom} />
              ))}
            </datalist>
            <Button size="sm" type="submit" disabled={!garantie}>
              add
            </Button>
          </form>
        </div>
        <form
          className="flex items-end gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            void brouillon.appliquer(operations.delai(id, heures === "" ? null : Number(heures)));
          }}
        >
          <label className="flex-1 space-y-1">
            <span className="text-xs text-ink-muted">at most (hours, empty: no limit)</span>
            <input type="number" min={1} className={champ} value={heures} onChange={(event) => setHeures(event.target.value)} />
          </label>
          <Button size="sm" type="submit">
            set
          </Button>
        </form>
        <Button size="sm" tone="danger" onClick={() => void brouillon.appliquer(operations.retirerLaTransition(id))}>
          remove this transition
        </Button>
      </div>
    </Card>
  );
}
