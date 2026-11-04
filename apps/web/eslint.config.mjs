// Native flat config, without `FlatCompat`.
//
// `eslint-config-next` 16 exports flat config arrays directly; it no longer has the eslintrc
// shape that `FlatCompat` can load, and trying anyway fails far from the cause, on a
// "Converting circular structure to JSON" deep inside `@eslint/eslintrc`.
import coreWebVitals from "eslint-config-next/core-web-vitals";
import typescript from "eslint-config-next/typescript";
import jsxA11y from "eslint-plugin-jsx-a11y";

const MOCKS = {
  group: ["@/mocks/*", "**/mocks/*"],
  message: "demo-mode fixtures: load them with import() behind IS_MOCK, never statically (#179)",
};

// The console owns its primitives (ADR 0048): nothing comes from the Varga Foundation design system
// any more — not a component, not a stylesheet. Its look (square corners, mono buttons, lowercase,
// the foundation's mark) would come back with it.
export const FOUNDATION_DESIGN_SYSTEM = {
  group: ["@varga/*", "@varga/design-system", "@varga/design-system/*"],
  message: "ADR 0048: the console owns its primitives — import them from @/components/ui",
};

const config = [
  // `next-env.d.ts` is written by Next on every build and carries the "should not be edited"
  // warning: the linter has nothing to say about it.
  { ignores: [".next/**", "node_modules/**", "coverage/**", "playwright-report/**", "next-env.d.ts"] },
  ...coreWebVitals,
  ...typescript,
  {
    rules: {
      // Accessibility is checked at lint time, not in review: thirteen `aria-*` over three
      // thousand lines is what we found without a rule (state of play of 2026-09-24).
      // The RULES only: `eslint-config-next` already registers the plugin, and declaring it
      // again is a configuration error ("Cannot redefine plugin").
      ...jsxA11y.flatConfigs.strict.rules,
      "@typescript-eslint/no-explicit-any": "error",
      "@typescript-eslint/no-unused-vars": ["error", { argsIgnorePattern: "^_" }],
    },
    // The console's fields (S24-04) render the native element of their name: the accessibility
    // rules see them as such — a `<label>` that wraps an `<Input>` is associated with it.
    settings: { "jsx-a11y": { components: { Input: "input", Select: "select", Textarea: "textarea" } } },
  },
  {
    // The demo-mode fixtures do not ship in the real bundle: they are LOADED (`import()`), in demo
    // mode only. A static import bundled them with the page that cites them (#179).
    files: ["src/**/*.ts", "src/**/*.tsx"],
    ignores: ["src/mocks/**"],
    rules: {
      "no-restricted-imports": [
        "error",
        {
          patterns: [MOCKS, FOUNDATION_DESIGN_SYSTEM],
        },
      ],
    },
  },
  {
    // The fixtures themselves may import each other; the foundation's design system stays refused.
    files: ["src/mocks/**", "tests/**", "e2e/**"],
    rules: { "no-restricted-imports": ["error", { patterns: [FOUNDATION_DESIGN_SYSTEM] }] },
  },
];

export default config;
