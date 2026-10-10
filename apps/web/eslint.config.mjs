// Config plate native, sans `FlatCompat`.
//
// `eslint-config-next` 16 exporte directement des tableaux de config plate ; il n'a plus la
// forme eslintrc que `FlatCompat` sait charger, et l'essayer quand même échoue loin de la
// cause, sur un « Converting circular structure to JSON » au fond d'`@eslint/eslintrc`.
import coreWebVitals from "eslint-config-next/core-web-vitals";
import typescript from "eslint-config-next/typescript";
import jsxA11y from "eslint-plugin-jsx-a11y";

const MOCKS = {
  group: ["@/mocks/*", "**/mocks/*"],
  message: "fixtures du mode démo : les charger par import() derrière IS_MOCK, jamais statiquement (#179)",
};

const PRIMITIVES_DE_LA_CONSOLE = [
  "Badge",
  "Button",
  "buttonClasses",
  "Card",
  "Dot",
  "Empty",
  "Eyebrow",
  "Field",
  "Heading",
  "Input",
  "Label",
  "Lead",
  "Select",
  "Stat",
  "tabClasses",
  "Table",
  "TBody",
  "TD",
  "Textarea",
  "TH",
  "THead",
  "TR",
];

const config = [
  // `next-env.d.ts` est écrit par Next à chaque build et porte l'avertissement
  // « should not be edited » : le linter n'a rien à y dire.
  { ignores: [".next/**", "node_modules/**", "coverage/**", "playwright-report/**", "next-env.d.ts"] },
  ...coreWebVitals,
  ...typescript,
  {
    rules: {
      // L'accessibilité se vérifie au lint, pas à la relecture : treize `aria-*` sur trois
      // mille lignes, c'est ce qu'on trouvait sans règle (état des lieux du 2026-09-24).
      // Les RÈGLES seulement : `eslint-config-next` enregistre déjà le plugin, et le
      // redéclarer est une erreur de configuration (« Cannot redefine plugin »).
      ...jsxA11y.flatConfigs.strict.rules,
      "@typescript-eslint/no-explicit-any": "error",
      "@typescript-eslint/no-unused-vars": ["error", { argsIgnorePattern: "^_" }],
    },
  },
  {
    // Les fixtures du mode démo ne partent pas dans le bundle réel : on les CHARGE (`import()`), en
    // mode démo seulement. Un import statique les embarquait avec la page qui les cite (#179).
    files: ["src/**/*.ts", "src/**/*.tsx"],
    ignores: ["src/mocks/**"],
    rules: {
      "no-restricted-imports": [
        "error",
        {
          paths: [
            {
              // Les primitives du design system dont l'allure est codée en dur ont leur version de
              // console, sous les mêmes noms (ADR 0043) : les importer du design system ramènerait
              // le Space Mono gras, la puce carrée, l'aplat inversé ou le trait d'encre.
              name: "@varga/design-system",
              importNames: PRIMITIVES_DE_LA_CONSOLE,
              message: "ADR 0043 : importer depuis @/components/ui, qui en donne la version de console",
            },
          ],
          patterns: [MOCKS],
        },
      ],
    },
  },
  {
    // `components/ui.tsx` est l'endroit où la console habille le design system : il en importe la
    // carte. La règle des fixtures y vaut toujours.
    files: ["src/components/ui.tsx"],
    rules: { "no-restricted-imports": ["error", { patterns: [MOCKS] }] },
  },
];

export default config;
