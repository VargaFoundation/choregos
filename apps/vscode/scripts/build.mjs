// One CommonJS bundle the editor loads; `vscode` is provided by the editor at run time.
import { build } from "esbuild";

const production = process.argv.includes("--production");

await build({
  entryPoints: ["src/extension.ts"],
  bundle: true,
  outfile: "dist/extension.js",
  format: "cjs",
  platform: "node",
  target: "node20",
  external: ["vscode"],
  sourcemap: !production,
  minify: production,
  legalComments: "none",
  logLevel: "info",
});
