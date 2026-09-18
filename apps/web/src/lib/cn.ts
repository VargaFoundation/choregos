import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/** Fusionne des classes Tailwind sans conflit (utilisé par tous les composants). */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
