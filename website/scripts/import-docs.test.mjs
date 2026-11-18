// Tests of the Choregos documentation import: `node --test scripts/`.
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";

import { importSite, ImportError, rewritePage } from "./import-docs.mjs";

function checkout(files) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "choregos-"));
  for (const [name, content] of Object.entries(files)) {
    fs.mkdirSync(path.dirname(path.join(root, name)), { recursive: true });
    fs.writeFileSync(path.join(root, name), content);
  }
  return root;
}

const BASE = {
  "packages/core/src/tranibor_core/publishing.json": '{"source_repository": "https://github.com/acme/product"}',
  "docs/site/index.md": "# Choregos\n\n[Concepts](concepts.md) · [Usage](../usage.md#3-write-the-workflow)\n",
  "docs/site/concepts.md": "# Concepts\n\n![diagram](img/map.svg) ![logo](../../apps/web/public/logo.svg)\n",
  "docs/site/img/map.svg": "<svg/>",
  "docs/site/README.md": "# For writers\n",
  "docs/site/use/_category_.json": '{"label": "Use", "position": 4}',
  "docs/site/use/work-items.md": "See [the template](../../../templates/dev/) and [concepts](../concepts.md#gate).\n",
  "docs/usage.md": "# Usage\n",
  "apps/web/public/logo.svg": "<svg/>",
  "templates/dev/README.md": "# dev\n",
};

test("a page of the site stays relative, the rest of the repository becomes GitHub at the ref", () => {
  const root = checkout(BASE);
  const dest = fs.mkdtempSync(path.join(os.tmpdir(), "site-"));
  const pages = importSite({ sourceRoot: root, ref: "v1.2.3", dest });
  assert.equal(pages, 3);
  const index = fs.readFileSync(path.join(dest, "index.md"), "utf8");
  assert.match(index, /\[Concepts\]\(concepts\.md\)/);
  assert.match(
    index,
    /\[Usage\]\(https:\/\/github\.com\/acme\/product\/blob\/v1\.2\.3\/docs\/usage\.md#3-write-the-workflow\)/,
  );
  const concepts = fs.readFileSync(path.join(dest, "concepts.md"), "utf8");
  assert.match(concepts, /!\[diagram\]\(img\/map\.svg\)/);
  assert.match(
    concepts,
    /!\[logo\]\(https:\/\/raw\.githubusercontent\.com\/acme\/product\/v1\.2\.3\/apps\/web\/public\/logo\.svg\)/,
  );
  const items = fs.readFileSync(path.join(dest, "use", "work-items.md"), "utf8");
  assert.match(items, /https:\/\/github\.com\/acme\/product\/tree\/v1\.2\.3\/templates\/dev\//);
  assert.match(items, /\[concepts\]\(\.\.\/concepts\.md#gate\)/);
  assert.ok(fs.existsSync(path.join(dest, "img", "map.svg")));
  assert.ok(fs.existsSync(path.join(dest, "use", "_category_.json")));
  assert.ok(!fs.existsSync(path.join(dest, "README.md")), "the writers' guide is not published");
  assert.match(fs.readFileSync(path.join(dest, ".imported-from"), "utf8"), /acme\/product@v1\.2\.3/);
});

test("code is never rewritten", () => {
  const root = checkout(BASE);
  const text = "```md\n[x](../usage.md)\n```\n\n`[y](../usage.md)` and [z](../usage.md)\n";
  const out = rewritePage(text, { pageInSite: "index.md", sourceRoot: root, ref: "v1", missingPages: "fail" });
  assert.match(out, /```md\n\[x\]\(\.\.\/usage\.md\)\n```/);
  assert.match(out, /`\[y\]\(\.\.\/usage\.md\)`/);
  assert.match(out, /\[z\]\(https:\/\/github\.com\/acme\/product\/blob\/v1\/docs\/usage\.md\)/);
});

test("a link whose text is inline code is rewritten, the code of its text kept", () => {
  const root = checkout(BASE);
  const text = "See [`templates/dev`](../../../templates/dev/) and `../usage.md` alone.\n";
  const out = rewritePage(text, { pageInSite: "use/work-items.md", sourceRoot: root, ref: "v1", missingPages: "fail" });
  assert.match(out, /\[`templates\/dev`\]\(https:\/\/github\.com\/acme\/product\/tree\/v1\/templates\/dev\/?\)/);
  assert.match(out, / `\.\.\/usage\.md` alone/);
});

test("a link to a file that does not exist fails, and leaves the destination untouched", () => {
  const root = checkout({ ...BASE, "docs/site/index.md": "[gone](../gone.md)\n" });
  const dest = fs.mkdtempSync(path.join(os.tmpdir(), "site-"));
  fs.writeFileSync(path.join(dest, "kept.md"), "kept");
  assert.throws(() => importSite({ sourceRoot: root, ref: "v1", dest }), ImportError);
  assert.ok(fs.existsSync(path.join(dest, "kept.md")));
});

test("a missing page of the site fails, or becomes text when asked", () => {
  const root = checkout({ ...BASE, "docs/site/index.md": "[Skills](build/skills.md), [CLI](cli/install.md)\n" });
  const dest = fs.mkdtempSync(path.join(os.tmpdir(), "site-"));
  assert.throws(() => importSite({ sourceRoot: root, ref: "v1", dest }), /build\/skills\.md, which does not exist/);
  importSite({ sourceRoot: root, ref: "v1", dest, missingPages: "text" });
  assert.equal(fs.readFileSync(path.join(dest, "index.md"), "utf8"), "Skills, CLI\n");
});

test("a link that leaves the repository fails", () => {
  const root = checkout({ ...BASE, "docs/site/index.md": "[out](../../../etc/passwd)\n" });
  assert.throws(
    () => importSite({ sourceRoot: root, ref: "v1", dest: fs.mkdtempSync(path.join(os.tmpdir(), "s-")) }),
    /leaves the repository/,
  );
});

test("a ref without docs/site has nothing to import", () => {
  const root = checkout({ "docs/usage.md": "# Usage\n" });
  assert.throws(
    () => importSite({ sourceRoot: root, ref: "v0.25.3", dest: fs.mkdtempSync(path.join(os.tmpdir(), "s-")) }),
    (error) => error.nothingToImport === true,
  );
});
