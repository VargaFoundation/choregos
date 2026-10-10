import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";

// Chaque test est un onglet neuf : un brouillon de workflow gardé par un test (S23-06) ne doit pas
// apparaître dans le suivant. Les tests en environnement node n'ont pas de fenêtre.
afterEach(() => {
  if (typeof window === "undefined") return;
  window.sessionStorage.clear();
  // Le thème choisi (ADR 0043) ne passe pas d'un test à l'autre non plus.
  window.localStorage.removeItem("choregos.theme");
  delete document.documentElement.dataset.theme;
});
