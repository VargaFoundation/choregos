import { resolve } from "node:path";
import { defineConfig } from "vitest/config";

export default defineConfig({
  resolve: {
    alias: {
      // The editor's API exists only inside the editor: the tests run against a scriptable fake.
      vscode: resolve(import.meta.dirname, "./tests/fakes/vscode.ts"),
      "@choregos/contracts/api": resolve(import.meta.dirname, "../../packages/contracts/ts/api.ts"),
    },
  },
  test: {
    environment: "node",
    include: ["tests/**/*.test.ts"],
    coverage: {
      provider: "v8",
      include: ["src/**/*.ts"],
      thresholds: { lines: 90, branches: 85 },
    },
  },
});
