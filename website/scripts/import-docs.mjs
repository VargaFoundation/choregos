#!/usr/bin/env node
// Copy the public documentation of Tranibor (`docs/site/`) into `website/docs/`,
// the folder Docusaurus builds (ADR 0185). The repository is the source: nothing is imported from elsewhere.
//
//   node scripts/import-docs.mjs --source <repository checkout> --ref <tag or commit> [--dest docs]
//                                [--missing-pages fail|text]
//
// What it does:
// - copies `docs/site/` to the destination, which it empties first; every `README.md` is left out
//   (it is the writers' guide, not a page);
// - keeps a link to another page of `docs/site/` as it is (Docusaurus resolves `.md` links);
// - rewrites a link to anything else of the repository (a reference page, an ADR, a template) to its
//   GitHub address at the imported ref, and an image outside `docs/site/` to its raw address;
// - fails on a link to a file that does not exist. With `--missing-pages text`, a link to a page of
//   `docs/site/` that does not exist yet becomes plain text instead (used for an import made before
//   every section of the contract has landed);
// - writes `.imported-from` in the destination: the repository, the ref and the commit.
//
// Exit codes: 0 imported, 1 error, 3 the ref has no `docs/site/` (nothing to import).

import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

/** `owner/name` of the repository, from the publishing identity (ADR 0156) of the checkout being copied. */
export function repositoryOf(sourceRoot) {
  const file = path.join(sourceRoot, "packages", "core", "src", "choregos_core", "publishing.json");
  const url = new URL(JSON.parse(fs.readFileSync(file, "utf8")).source_repository);
  return url.pathname.replace(/^\/|\/$/g, "");
}
const SITE = "docs/site";

/** `[text](target)`, `![alt](target)` and `[label]: target` outside code. */
const INLINE_LINK = /(!?)\[((?:[^\]\n]|\\\])*)\]\(\s*(<?)([^)\s>]+)(>?)((?:\s+"[^"\n]*")?\s*)\)/g;
const DEFINITION = /^(\s{0,3}\[[^\]\n]+\]:\s*)(\S+)(.*)$/gm;

export class ImportError extends Error {}

/** Split a page into code (left untouched) and prose (rewritten). */
export function splitCode(text) {
  const parts = [];
  const fence = /^(```|~~~)[^\n]*\n[\s\S]*?^\1[^\n]*$/gm;
  let last = 0;
  for (const match of text.matchAll(fence)) {
    parts.push({ code: false, text: text.slice(last, match.index) });
    parts.push({ code: true, text: match[0] });
    last = match.index + match[0].length;
  }
  parts.push({ code: false, text: text.slice(last) });
  // Inline code inside prose. A whole link stays prose, even when its text is inline code
  // ([`file`](../../../file)): splitting the code out of it would hide the link from the rewrite.
  return parts.flatMap((part) => {
    if (part.code) return [part];
    const out = [];
    let at = 0;
    const token = /!?\[(?:[^\]\n]|\\\])*\]\([^)\n]*\)|`+[^`\n]*?`+/g;
    for (const match of part.text.matchAll(token)) {
      if (!match[0].startsWith("`")) continue;
      out.push({ code: false, text: part.text.slice(at, match.index) });
      out.push({ code: true, text: match[0] });
      at = match.index + match[0].length;
    }
    out.push({ code: false, text: part.text.slice(at) });
    return out;
  });
}

/**
 * What a link of the page `pageInSite` (a path relative to `docs/site/`) becomes.
 * Returns `{target}` (a new or the same target) or `{text: true}` (drop the link, keep its text).
 */
export function rewriteTarget(target, { pageInSite, sourceRoot, ref, image, missingPages }) {
  if (/^[a-z][a-z0-9+.-]*:/i.test(target) || target.startsWith("#") || target.startsWith("/")) {
    return { target };
  }
  const hash = target.indexOf("#");
  const filePart = hash >= 0 ? target.slice(0, hash) : target;
  const anchor = hash >= 0 ? target.slice(hash) : "";
  const fromRepo = path.posix.normalize(path.posix.join(SITE, path.posix.dirname(pageInSite), decodeURI(filePart)));
  if (fromRepo.startsWith("..")) {
    throw new ImportError(`${pageInSite}: \`${target}\` leaves the repository`);
  }
  const onDisk = path.join(sourceRoot, fromRepo);
  const insideSite = fromRepo === SITE || fromRepo.startsWith(`${SITE}/`);
  if (insideSite) {
    if (fs.existsSync(onDisk)) return { target };
    if (missingPages === "text" && /\.mdx?$/.test(fromRepo)) return { text: true };
    throw new ImportError(`${pageInSite}: \`${target}\` points to ${fromRepo}, which does not exist`);
  }
  if (!fs.existsSync(onDisk)) {
    throw new ImportError(`${pageInSite}: \`${target}\` points to ${fromRepo}, which does not exist at ${ref}`);
  }
  const encoded = fromRepo.split("/").map(encodeURIComponent).join("/");
  const REPOSITORY = repositoryOf(sourceRoot);
  if (image) return { target: `https://raw.githubusercontent.com/${REPOSITORY}/${ref}/${encoded}` };
  const kind = fs.statSync(onDisk).isDirectory() ? "tree" : "blob";
  return { target: `https://github.com/${REPOSITORY}/${kind}/${ref}/${encoded}${anchor}` };
}

/** Rewrite every link of one page. */
export function rewritePage(text, options) {
  return splitCode(text)
    .map((part) => {
      if (part.code) return part.text;
      let prose = part.text.replace(INLINE_LINK, (whole, bang, label, open, target, close, title) => {
        const result = rewriteTarget(target, { ...options, image: bang === "!" });
        if (result.text) return label;
        return `${bang}[${label}](${open}${result.target}${close}${title})`;
      });
      prose = prose.replace(DEFINITION, (whole, head, target, tail) => {
        const result = rewriteTarget(target, { ...options, image: false });
        if (result.text) return "";
        return `${head}${result.target}${tail}`;
      });
      return prose;
    })
    .join("");
}

function walk(dir) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = path.join(dir, entry.name);
    return entry.isDirectory() ? walk(full) : [full];
  });
}

/** Import `docs/site/` of `sourceRoot` into `dest`. Returns the number of pages written. */
export function importSite({ sourceRoot, ref, dest, missingPages = "fail", commit = null }) {
  const site = path.join(sourceRoot, SITE);
  if (!fs.existsSync(site)) {
    const error = new ImportError(`${ref} has no ${SITE}/: nothing to import`);
    error.nothingToImport = true;
    throw error;
  }
  // Rewrite everything in memory first: a broken link leaves the destination untouched.
  const files = walk(site)
    .map((full) => path.relative(site, full).split(path.sep).join("/"))
    .filter((relative) => path.posix.basename(relative) !== "README.md")
    .sort()
    .map((relative) => {
      const full = path.join(site, relative);
      if (/\.mdx?$/.test(relative)) {
        const text = fs.readFileSync(full, "utf8");
        return { relative, data: rewritePage(text, { pageInSite: relative, sourceRoot, ref, missingPages }) };
      }
      return { relative, data: fs.readFileSync(full) };
    });
  fs.rmSync(dest, { recursive: true, force: true });
  for (const file of files) {
    const target = path.join(dest, ...file.relative.split("/"));
    fs.mkdirSync(path.dirname(target), { recursive: true });
    fs.writeFileSync(target, file.data);
  }
  const origin = [`${repositoryOf(sourceRoot)}@${ref}`, commit, `${SITE}/`].filter(Boolean).join(" ");
  fs.writeFileSync(path.join(dest, ".imported-from"), `${origin}\n`);
  return files.filter((file) => /\.mdx?$/.test(file.relative)).length;
}

function parseArgs(argv) {
  const args = { dest: "docs", missingPages: "fail" };
  for (let i = 0; i < argv.length; i += 1) {
    const [key, value] = [argv[i], argv[i + 1]];
    if (key === "--source") args.sourceRoot = value;
    else if (key === "--ref") args.ref = value;
    else if (key === "--dest") args.dest = value;
    else if (key === "--missing-pages") args.missingPages = value;
    else throw new ImportError(`unknown argument ${key}`);
    i += 1;
  }
  if (!args.sourceRoot || !args.ref) throw new ImportError("--source and --ref are required");
  if (!["fail", "text"].includes(args.missingPages)) throw new ImportError("--missing-pages is fail or text");
  if (!/^[A-Za-z0-9][A-Za-z0-9._\/-]*$/.test(args.ref)) throw new ImportError(`not a ref: ${args.ref}`);
  return args;
}

function main() {
  try {
    const args = parseArgs(process.argv.slice(2));
    let commit = null;
    try {
      commit = execFileSync("git", ["-C", args.sourceRoot, "rev-parse", "HEAD"], { encoding: "utf8" }).trim();
    } catch {
      commit = null;
    }
    const pages = importSite({ ...args, commit });
    console.log(`imported ${pages} pages of ${repositoryOf(args.sourceRoot)}@${args.ref} into ${args.dest}`);
  } catch (error) {
    console.error(error instanceof ImportError ? error.message : error);
    process.exit(error?.nothingToImport ? 3 : 1);
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) main();
