import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ChoixDuTheme } from "@/components/choix-du-theme";
import { CLE_DE_LA_BARRE, CLE_DU_THEME, lireLaBarre, SCRIPT_DES_PREFERENCES, themeResolu } from "@/lib/preferences";
import { ThemeProvider } from "@/lib/theme";

type Ecouteur = (evenement: { matches: boolean }) => void;

/** Un `matchMedia` qui répond « clair » ou non, et qu'on peut faire changer. */
function systeme(enClair: boolean) {
  const ecouteurs = new Set<Ecouteur>();
  const requete = {
    matches: enClair,
    media: "(prefers-color-scheme: light)",
    addEventListener: (_: string, f: Ecouteur) => ecouteurs.add(f),
    removeEventListener: (_: string, f: Ecouteur) => ecouteurs.delete(f),
  };
  vi.stubGlobal("matchMedia", () => requete);
  window.matchMedia = (() => requete) as unknown as typeof window.matchMedia;
  return {
    basculer(clair: boolean) {
      requete.matches = clair;
      for (const f of ecouteurs) f({ matches: clair });
    },
  };
}

function executerLeScript() {
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

describe("le script d'en-tête pose le thème avant la peinture (ADR 0043)", () => {
  it.each([
    [null, false, "dark"],
    ["dark", true, "dark"],
    ["light", false, "light"],
    ["system", true, "light"],
    ["system", false, "dark"],
    ["n'importe quoi", true, "dark"],
  ])("préférence %s, système en clair : %s → %s, comme themeResolu", (preference, clair, attendu) => {
    systeme(clair);
    if (preference !== null) window.localStorage.setItem(CLE_DU_THEME, preference);
    expect(executerLeScript()).toBe(attendu);
    expect(themeResolu(preference, clair)).toBe(attendu);
  });

  it("un stockage refusé donne le sombre, sans erreur", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("SecurityError");
    });
    expect(executerLeScript()).toBe("dark");
  });
});

describe("le script d'en-tête replie aussi la barre latérale avant la peinture (S24-03)", () => {
  it.each([
    [null, undefined],
    ["rail", "rail"],
    ["full", undefined],
    ["n'importe quoi", undefined],
  ])("barre gardée %s → data-sidebar %s, comme lireLaBarre", (gardee, attendu) => {
    systeme(false);
    if (gardee !== null) window.localStorage.setItem(CLE_DE_LA_BARRE, gardee);
    delete document.documentElement.dataset.sidebar;
    new Function(SCRIPT_DES_PREFERENCES)();
    expect(document.documentElement.dataset.sidebar).toBe(attendu);
    expect(lireLaBarre()).toBe(attendu ?? "full");
  });

  it("un stockage refusé laisse la barre pleine, et le thème sombre", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("SecurityError");
    });
    delete document.documentElement.dataset.sidebar;
    expect(executerLeScript()).toBe("dark");
    expect(document.documentElement.dataset.sidebar).toBeUndefined();
    expect(lireLaBarre()).toBe("full");
  });
});

describe("le choix du thème", () => {
  it("choisir « light » l'applique tout de suite et le garde", () => {
    systeme(false);
    render(
      <ThemeProvider>
        <ChoixDuTheme />
      </ThemeProvider>,
    );
    expect(screen.getByRole("radio", { name: "dark" })).toBeChecked();
    fireEvent.click(screen.getByRole("radio", { name: "light" }));
    expect(document.documentElement.dataset.theme).toBe("light");
    expect(window.localStorage.getItem(CLE_DU_THEME)).toBe("light");
    expect(screen.getByRole("radio", { name: "light" })).toBeChecked();
  });

  it("« system » suit le système quand il change de mode", () => {
    const os = systeme(false);
    render(
      <ThemeProvider>
        <ChoixDuTheme />
      </ThemeProvider>,
    );
    fireEvent.click(screen.getByRole("radio", { name: "system" }));
    expect(document.documentElement.dataset.theme).toBe("dark");
    act(() => os.basculer(true));
    expect(document.documentElement.dataset.theme).toBe("light");
  });

  it("un choix fait dans un autre onglet vaut ici aussi", () => {
    systeme(false);
    render(
      <ThemeProvider>
        <ChoixDuTheme />
      </ThemeProvider>,
    );
    act(() => {
      window.localStorage.setItem(CLE_DU_THEME, "light");
      window.dispatchEvent(new StorageEvent("storage", { key: CLE_DU_THEME }));
    });
    expect(document.documentElement.dataset.theme).toBe("light");
    expect(screen.getByRole("radio", { name: "light" })).toBeChecked();
  });
});
