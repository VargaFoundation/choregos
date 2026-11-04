import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ThemePicker } from "@/components/theme-picker";
import { SIDEBAR_KEY, THEME_KEY, readSidebar, SCRIPT_DES_PREFERENCES, defaultTheme, themeResolu } from "@/lib/preferences";
import { ThemeProvider } from "@/lib/theme";

type Ecouteur = (evenement: { matches: boolean }) => void;

/** A `matchMedia` that answers "light" or not, and that can be made to change. */
function systeme(enClair: boolean) {
  const ecouteurs = new Set<Ecouteur>();
  const request = {
    matches: enClair,
    media: "(prefers-color-scheme: light)",
    addEventListener: (_: string, f: Ecouteur) => ecouteurs.add(f),
    removeEventListener: (_: string, f: Ecouteur) => ecouteurs.delete(f),
  };
  vi.stubGlobal("matchMedia", () => request);
  window.matchMedia = (() => request) as unknown as typeof window.matchMedia;
  return {
    basculer(clair: boolean) {
      request.matches = clair;
      for (const f of ecouteurs) f({ matches: clair });
    },
  };
}

function runScript() {
  delete document.documentElement.dataset.theme;
  new Function(SCRIPT_DES_PREFERENCES)();
  return document.documentElement.dataset.theme;
}

afterEach(() => {
  window.localStorage.clear();
  delete document.documentElement.dataset.theme;
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("the header script sets the theme before the paint (ADR 0043)", () => {
  it.each([
    [null, false, "dark"],
    ["dark", true, "dark"],
    ["light", false, "light"],
    ["system", true, "light"],
    ["system", false, "dark"],
    ["anything", true, "dark"],
  ])("preference %s, system light: %s -> %s, like themeResolu", (preference, clair, attendu) => {
    systeme(clair);
    if (preference !== null) window.localStorage.setItem(THEME_KEY, preference);
    expect(runScript()).toBe(attendu);
    expect(themeResolu(preference, clair)).toBe(attendu);
  });

  it.each([
    [null, true, "light"],
    ["anything else", false, "light"],
    ["dark", true, "dark"],
    ["light", false, "light"],
    ["system", false, "dark"],
    ["system", true, "light"],
  ])("with data-default-theme=light, preference %s (system light: %s) → %s, as themeResolu", (preference, clair, attendu) => {
    systeme(clair);
    document.documentElement.dataset.defaultTheme = "light";
    if (preference !== null) window.localStorage.setItem(THEME_KEY, preference);
    expect(runScript()).toBe(attendu);
    expect(themeResolu(preference, clair, defaultTheme())).toBe(attendu);
    delete document.documentElement.dataset.defaultTheme;
  });

  it("a refused storage gives the served default mode", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("SecurityError");
    });
    document.documentElement.dataset.defaultTheme = "light";
    expect(runScript()).toBe("light");
    delete document.documentElement.dataset.defaultTheme;
  });

  it("a refused storage gives dark, without an error", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("SecurityError");
    });
    expect(runScript()).toBe("dark");
  });
});

describe("the header script also folds the sidebar before the paint (S24-03)", () => {
  it.each([
    [null, undefined],
    ["rail", "rail"],
    ["full", undefined],
    ["anything", undefined],
  ])("kept bar %s -> data-sidebar %s, like readSidebar", (gardee, attendu) => {
    systeme(false);
    if (gardee !== null) window.localStorage.setItem(SIDEBAR_KEY, gardee);
    delete document.documentElement.dataset.sidebar;
    new Function(SCRIPT_DES_PREFERENCES)();
    expect(document.documentElement.dataset.sidebar).toBe(attendu);
    expect(readSidebar()).toBe(attendu ?? "full");
  });

  it("a refused storage leaves the bar full, and the theme dark", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("SecurityError");
    });
    delete document.documentElement.dataset.sidebar;
    expect(runScript()).toBe("dark");
    expect(document.documentElement.dataset.sidebar).toBeUndefined();
    expect(readSidebar()).toBe("full");
  });
});

describe("the theme choice", () => {
  it("choosing \"light\" applies it at once and keeps it", () => {
    systeme(false);
    render(
      <ThemeProvider>
        <ThemePicker />
      </ThemeProvider>,
    );
    expect(screen.getByRole("radio", { name: "Dark" })).toBeChecked();
    fireEvent.click(screen.getByRole("radio", { name: "Light" }));
    expect(document.documentElement.dataset.theme).toBe("light");
    expect(window.localStorage.getItem(THEME_KEY)).toBe("light");
    expect(screen.getByRole("radio", { name: "Light" })).toBeChecked();
  });

  it("\"system\" follows the system when it changes mode", () => {
    const os = systeme(false);
    render(
      <ThemeProvider>
        <ThemePicker />
      </ThemeProvider>,
    );
    fireEvent.click(screen.getByRole("radio", { name: "System" }));
    expect(document.documentElement.dataset.theme).toBe("dark");
    act(() => os.basculer(true));
    expect(document.documentElement.dataset.theme).toBe("light");
  });

  it("a choice made in another tab applies here too", () => {
    systeme(false);
    render(
      <ThemeProvider>
        <ThemePicker />
      </ThemeProvider>,
    );
    act(() => {
      window.localStorage.setItem(THEME_KEY, "light");
      window.dispatchEvent(new StorageEvent("storage", { key: THEME_KEY }));
    });
    expect(document.documentElement.dataset.theme).toBe("light");
    expect(screen.getByRole("radio", { name: "Light" })).toBeChecked();
  });
});
