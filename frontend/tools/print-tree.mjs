// # from frontend/ root
// mkdir -p tools

cat > tools/print-tree.mjs <<'EOF'
import fs from "node:fs";
import path from "node:path";

const ROOT = process.argv[2] ?? "src";
const MAX_DEPTH = Number(process.argv[3] ?? 10);
const IGNORE = new Set([
  "node_modules",".git",".next","dist","build",".turbo",".cache",
  ".vite",".vercel",".swc",".parcel-cache","coverage",".DS_Store"
]);

function isIgnored(name){ return IGNORE.has(name) || name.startsWith("."); }

function tree(dir, prefix="", depth=0){
  if (depth > MAX_DEPTH) return "";
  let entries = [];
  try { entries = fs.readdirSync(dir, { withFileTypes: true }); } catch { return ""; }
  entries = entries
    .filter(e => !isIgnored(e.name))
    .sort((a,b) => (a.isDirectory()===b.isDirectory() ? a.name.localeCompare(b.name) : (a.isDirectory()? -1 : 1)));
  const lines = [];
  entries.forEach((e,i) => {
    const last = i === entries.length-1;
    const glyph = last ? "└── " : "├── ";
    const next = prefix + (last ? "    " : "│   ");
    lines.push(prefix + glyph + e.name + (e.isDirectory()? "/" : ""));
    if (e.isDirectory()) lines.push(tree(path.join(dir, e.name), next, depth+1));
  });
  return lines.join("\n");
}

const abs = path.resolve(process.cwd(), process.argv[2] ?? "src");
console.log(path.basename(abs) + "/");
console.log(tree(abs));
EOF

// # print to screen (simplest)
// node tools/print-tree.mjs src 3
