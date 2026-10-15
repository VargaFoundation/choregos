// @vitest-environment node
import { describe, expect, it } from "vitest";
import { DEFAULT_SERVED_THEME, safeAsset, sanitizeTheme, themeCss, type ServedTheme } from "@/lib/served-theme";
import { SAMPLE_THEME } from "@/mocks/theme-sample";

const theme = (over: Partial<ServedTheme>): ServedTheme => ({ ...DEFAULT_SERVED_THEME, ...over });

describe("the CSS of a served theme (S39-03b)", () => {
  it("is empty for the default theme: the console looks as it always did", () => {
    expect(themeCss(DEFAULT_SERVED_THEME)).toBe("");
  });

  it("puts dark overrides under :not([data-theme=light]) and light ones under [data-theme=light]", () => {
    const css = themeCss(theme({ tokens: { dark: { accent: "#112233" }, light: { accent: "#445566" } } }));
    expect(css).toContain(':root:root:not([data-theme="light"]){--choregos-accent:#112233}');
    expect(css).toContain(':root:root[data-theme="light"]{--choregos-accent:#445566}');
    // A dark override can never sit in the light rule, nor the reverse.
    const [darkRule, lightRule] = css.split("\n");
    expect(darkRule).not.toContain("#445566");
    expect(lightRule).not.toContain("#112233");
  });

  it("drops values that are not #rrggbb and role names that are not names", () => {
    const css = themeCss(
      theme({
        tokens: {
          dark: {
            accent: "red",
            "accent-hover": "var(--x)",
            "accent-soft": "#fff",
            ink: "#123456;}body{display:none",
            "ok);}body{x:": "#123456",
            warn: "#abcdef",
          } as ServedTheme["tokens"]["dark"],
          light: {},
        },
      }),
    );
    expect(css).toBe(':root:root:not([data-theme="light"]){--choregos-warn:#abcdef}');
  });

  it("emits @font-face with font-display: swap and wires the title role", () => {
    const css = themeCss(SAMPLE_THEME);
    expect(css).toContain(
      '@font-face{font-family:"Sample Serif";src:url("/api/v1/mock-theme-assets/sample-serif.woff2") format("woff2");font-weight:300;font-style:normal;font-display:swap}',
    );
    expect(css).toContain('--choregos-font-title:"Sample Serif",var(--choregos-font-sans)');
    expect(css).toContain("--choregos-font-title-weight:300");
  });

  it("drops a family that could close the rule, and a face from another origin", () => {
    const css = themeCss(
      theme({
        fonts: {
          title: {
            family: 'Geist";}body{',
            weight: 300,
            faces: [{ src: "/api/v1/a.woff2", weight: 300, style: "normal" }],
          },
          sans: {
            family: "Fine",
            weight: 400,
            faces: [
              { src: "https://evil.example/x.woff2", weight: 400, style: "normal" },
              { src: "/api/v1/../x.woff2", weight: 400, style: "normal" },
            ],
          },
        },
      }),
    );
    expect(css).toBe("");
  });

  it("emits the radii as the console's variables, bounded", () => {
    const css = themeCss(theme({ radius: { control: 0, surface: 0 } }));
    expect(css).toContain("--radius-sm:0px");
    expect(css).toContain("--radius-md:0px");
    expect(css).toContain("--radius-4xl:0px");
    expect(themeCss(theme({ radius: { control: 99, surface: -1 } }))).toBe("");
  });

  it("sets the sans font in front of Geist, which stays as the fallback", () => {
    const css = themeCss(
      theme({
        fonts: { sans: { family: "Fine", weight: 400, faces: [{ src: "/api/v1/f.woff2", weight: 400, style: "normal" }] } },
      }),
    );
    expect(css).toContain('--choregos-font-sans:"Fine",var(--font-geist-sans)');
  });
});

describe("what the console accepts from the API at all", () => {
  it("only same-origin /api/v1 assets", () => {
    expect(safeAsset("/api/v1/theme/x.svg")).toBe("/api/v1/theme/x.svg");
    for (const bad of ["https://x/y", "//x/y", "/api/v1/../y", "/other/y", "/api/v1/a b", 3, undefined]) {
      expect(safeAsset(bad)).toBeUndefined();
    }
  });

  it("falls back to the default theme for anything that is not a theme", () => {
    for (const bad of [null, "x", 3, {}, { id: "x" }, { id: "x", product_name: " ", tokens: {} }]) {
      expect(sanitizeTheme(bad)).toEqual(DEFAULT_SERVED_THEME);
    }
  });

  it("keeps the name, bounded, the mode, the footer and safe assets only", () => {
    const clean = sanitizeTheme({
      ...SAMPLE_THEME,
      product_name: "x".repeat(60),
      logo: "https://evil/x.svg",
      footer: { text: "y".repeat(300) },
    });
    expect(clean.product_name).toHaveLength(40);
    expect(clean.logo).toBeUndefined();
    expect(clean.favicon).toBe(SAMPLE_THEME.favicon);
    expect(clean.footer?.text).toHaveLength(200);
    expect(clean.default_mode).toBe("light");
  });
});
