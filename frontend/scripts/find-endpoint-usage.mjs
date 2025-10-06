#!/usr/bin/env node
import { promises as fs } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(process.cwd(), "src");
const endpointArg = process.argv[2];
if (!endpointArg) {
  console.error("Usage: node scripts/find-endpoint-usage.mjs <endpoint-fragment>");
  console.error("Example: node scripts/find-endpoint-usage.mjs /api/exec/positions");
  process.exit(1);
}

// Very broad patterns: fetch/axios/http.<method>('…'), template strings, joiners
const callRe = new RegExp(
  String.raw`(?:fetch|axios\.(?:get|post|put|patch|delete)|http\.(?:get|post|put|patch|delete))\s*\(\s*([^\)]*)\)`,
  "i"
);
const strContainRe = new RegExp(endpointArg.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "i");

// Walk all source files
async function walk(dir) {
  const out = [];
  const entries = await fs.readdir(dir, { withFileTypes: true });
  for (const e of entries) {
    if (e.name === "node_modules" || e.name.startsWith(".")) continue;
    const p = resolve(dir, e.name);
    if (e.isDirectory()) out.push(...(await walk(p)));
    else if (/\.(tsx?|jsx?)$/.test(e.name)) out.push(p);
  }
  return out;
}

function snippet(lines, i, radius = 2) {
  const start = Math.max(0, i - radius);
  const end = Math.min(lines.length, i + radius + 1);
  return lines.slice(start, end).map((l, idx) => {
    const ln = String(start + idx + 1).padStart(4, " ");
    const mark = start + idx === i ? ">" : " ";
    return `${mark}${ln} ${l}`.replace(/\t/g, "  ");
  }).join("\n");
}

let hits = 0;

const files = await walk(root);
for (const file of files) {
  const text = await fs.readFile(file, "utf8");
  if (!strContainRe.test(text)) continue; // quick filter

  const lines = text.split(/\r?\n/);
  let printedHeader = false;

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (!strContainRe.test(line)) continue;

    // Extra check: try to ensure it's within a call expression
    if (!callRe.test(line) && !lines.slice(Math.max(0, i - 2), i + 3).some(l => callRe.test(l))) {
      // still print, because URL joins can span multiple lines
    }

    if (!printedHeader) {
      console.log(`\n\x1b[36m${file}\x1b[0m`);
      printedHeader = true;
    }
    console.log(snippet(lines, i));
    console.log();
    hits++;
  }
}

if (hits === 0) {
  console.log(`No matches for "${endpointArg}" under ${root}`);
}
