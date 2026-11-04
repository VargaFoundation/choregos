import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";

// Each test is a fresh tab: a workflow draft kept by one test (S23-06) must not show up in the
// next. Tests in the node environment have no window.
afterEach(() => {
  if (typeof window === "undefined") return;
  window.sessionStorage.clear();
  // The chosen theme and the collapsed sidebar (ADR 0043) do not carry over from one test to the next either.
  window.localStorage.removeItem("choregos.theme");
  window.localStorage.removeItem("choregos.sidebar");
  delete document.documentElement.dataset.theme;
  delete document.documentElement.dataset.sidebar;
});
