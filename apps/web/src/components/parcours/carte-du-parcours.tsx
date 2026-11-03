// SPDX-License-Identifier: Apache-2.0
// lang: fr-ok-file alias:internal — former path of ../journey/journey-map, removed by S45-29
"use client";

import type { ComponentProps } from "react";
import { JourneyMap, SnakeMap } from "@/components/journey/journey-map";

export { STATUS_LABEL as LIBELLE_DU_STATUT } from "@/components/journey/journey-map";

type JourneyProps = ComponentProps<typeof JourneyMap>;
type SnakeProps = ComponentProps<typeof SnakeMap>;

export function CarteDuParcours({ largeurInitiale, ...rest }: Omit<JourneyProps, "initialWidth"> & { largeurInitiale?: number }) {
  return <JourneyMap {...rest} initialWidth={largeurInitiale} />;
}

export function CarteEnSerpentin({
  choix,
  onChoisir,
  largeurInitiale,
  ...rest
}: Omit<SnakeProps, "selected" | "onSelect" | "initialWidth"> & {
  choix?: string | null;
  onChoisir?: (transitionId: string) => void;
  largeurInitiale?: number;
}) {
  return <SnakeMap {...rest} selected={choix} onSelect={onChoisir} initialWidth={largeurInitiale} />;
}
