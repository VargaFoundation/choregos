import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";

// Chaque test est un onglet neuf : un brouillon de workflow gardé par un test (S23-06) ne doit pas
// apparaître dans le suivant. Les tests en environnement node n'ont pas de fenêtre.
afterEach(() => {
  if (typeof window !== "undefined") window.sessionStorage.clear();
});
