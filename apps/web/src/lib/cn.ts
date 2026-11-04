// SPDX-License-Identifier: Apache-2.0
import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/** Merges Tailwind classes without conflict (used by every component). */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
