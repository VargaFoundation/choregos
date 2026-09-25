// Config plate native, sans `FlatCompat`.
//
// `eslint-config-next` 16 exporte directement des tableaux de config plate ; il n'a plus la
// forme eslintrc que `FlatCompat` sait charger, et l'essayer quand même échoue loin de la
// cause, sur un « Converting circular structure to JSON » au fond d'`@eslint/eslintrc`.
import coreWebVitals from "eslint-config-next/core-web-vitals";
import typescript from "eslint-config-next/typescript";
import jsxA11y from "eslint-plugin-jsx-a11y";

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
];

export default config;
