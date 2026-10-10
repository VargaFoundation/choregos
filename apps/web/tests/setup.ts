import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";

// Chaque test est un onglet neuf : un brouillon de workflow gardé par un test (S23-06) ne doit pas
// apparaître dans le suivant. Les tests en environnement node n'ont pas de fenêtre.
afterEach(() => {
  if (typeof window === "undefined") return;
  window.sessionStorage.clear();
  // Le thème choisi et la barre repliée (ADR 0043) ne passent pas d'un test à l'autre non plus.
  window.localStorage.removeItem("choregos.theme");
  window.localStorage.removeItem("choregos.sidebar");
  delete document.documentElement.dataset.theme;
  delete document.documentElement.dataset.sidebar;
});
