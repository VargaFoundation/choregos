// Flat config: the recommended rules of ESLint and typescript-eslint, type-aware.
import js from "@eslint/js";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist/**", "coverage/**", "node_modules/**", "scripts/**", "*.config.*"] },
  js.configs.recommended,
  ...tseslint.configs.strictTypeChecked,
  {
    languageOptions: { parserOptions: { projectService: true, tsconfigRootDir: import.meta.dirname } },
    rules: {
      "@typescript-eslint/no-explicit-any": "error",
      "@typescript-eslint/no-unused-vars": ["error", { argsIgnorePattern: "^_" }],
      "@typescript-eslint/restrict-template-expressions": ["error", { allowNumber: true }],
      // ADR 0155 §1 and §8: the extension runs nothing locally and reports to nobody.
      "no-restricted-imports": [
        "error",
        {
          paths: [
            { name: "child_process", message: "ADR 0155 §1: the extension starts no local process" },
            { name: "node:child_process", message: "ADR 0155 §1: the extension starts no local process" },
          ],
          patterns: [
            { group: ["@vscode/extension-telemetry", "*telemetry*"], message: "ADR 0155 §8: no telemetry" },
          ],
        },
      ],
    },
  },
);
