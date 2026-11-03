// SPDX-License-Identifier: Apache-2.0
// lang: fr-ok-file alias:internal — former path of ./panels, removed by S45-29
"use client";

import type { ComponentProps } from "react";
import { StatePanel, StepForm, TransitionPanel } from "@/components/workflows/panels";

export { actorLabel, type GateWords } from "@/components/workflows/panels";

type StateProps = ComponentProps<typeof StatePanel>;
type FormProps = ComponentProps<typeof StepForm>;
type TransitionProps = ComponentProps<typeof TransitionPanel>;

export function PanneauDEtat({ noeud, etats, acteurs }: { noeud: StateProps["node"]; etats: StateProps["states"]; acteurs: StateProps["actors"] }) {
  return <StatePanel node={noeud} states={etats} actors={acteurs} />;
}

export function FormulaireDEtape({
  depart,
  etats,
  acteurs,
  ...rest
}: Omit<FormProps, "from" | "states" | "actors"> & { depart?: string; etats: FormProps["states"]; acteurs: FormProps["actors"] }) {
  return <StepForm {...rest} from={depart} states={etats} actors={acteurs} />;
}

export function PanneauDeTransition({
  arete,
  acteurs,
  ...rest
}: Omit<TransitionProps, "edge" | "actors"> & { arete: TransitionProps["edge"]; acteurs: TransitionProps["actors"] }) {
  return <TransitionPanel {...rest} edge={arete} actors={acteurs} />;
}
