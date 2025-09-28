# File: print_tree_frontend.py
# Usage examples:
#   python print_tree_frontend.py
#   python print_tree_frontend.py --max-depth 3
#   python print_tree_frontend.py --only .ts,.tsx,.css --max-files 200
#   python print_tree_frontend.py --show-sizes --output frontend_tree.txt
#   python print_tree_frontend.py --ascii

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Iterable, List, Set, Tuple

DEFAULT_EXCLUDE_DIRS = {
    ".git", ".hg", ".svn", ".idea", ".vscode",
    "node_modules", "dist", "build", "coverage", ".next", ".turbo",
    ".vercel", ".cache", ".parcel-cache", ".rollup.cache", ".swc",
    ".svelte-kit", ".nuxt", ".expo", ".expo-shared", ".expo-cache",
    ".pytest_cache", ".DS_Store",
}
DEFAULT_EXCLUDE_FILES = {
    ".DS_Store", "Thumbs.db",
}
DEFAULT_EXCLUDE_EXTS = {
    ".map", ".log",
}

BORDER = {
    "tee": "├── ",
    "ell": "└── ",
    "bar": "│   ",
    "spc": "    ",
}

ASCII_BORDER = {
    "tee": "|-- ",
    "ell": "`-- ",
    "bar": "|   ",
    "spc": "    ",
}


def human_size(num: int) -> str:
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if num < 1024:
            return f"{num}{unit}"
        num //= 1024
    return f"{num}PB"


def scan_dir(
    root: Path,
    max_depth: int,
    only_exts: Set[str],
    exclude_dirs: Set[str],
    exclude_files: Set[str],
    exclude_exts: Set[str],
    max_files: int | None,
) -> Tuple[List[str], int, int]:
    """
    Returns (lines, file_count, dir_count).
    """
    border = BORDER
    lines: List[str] = []

    def should_keep_file(p: Path) -> bool:
        if p.name in exclude_files:
            return False
        ext = p.suffix.lower()
        if ext in exclude_exts:
            return False
        if only_exts and ext not in only_exts:
            return False
        return True

    def iter_tree(dir_path: Path, prefix_stack: List[bool], depth: int) -> Tuple[int, int]:
        files_count = 0
        dirs_count = 0

        try:
            entries = list(dir_path.iterdir())
        except PermissionError:
            # Skip directories we can't read
            return 0, 0

        # Split into dirs/files and filter
        dirs = [e for e in entries if e.is_dir() and e.name not in exclude_dirs]
        files = [e for e in entries if e.is_file() and should_keep_file(e)]

        dirs.sort(key=lambda p: p.name.lower())
        files.sort(key=lambda p: p.name.lower())

        # If max_files set, cap the visible files (but still count them)
        visible_files = files
        truncated_note = None
        if max_files is not None and len(files) > max_files:
            head = max_files // 2
            tail = max_files - head
            visible_files = files[:head] + files[-tail:]
            truncated_note = f"... ({len(files) - len(visible_files)} more files hidden)"

        # Print directories first
        total_children = len(dirs) + len(visible_files) + (1 if truncated_note else 0)
        idx = 0

        def make_prefix(prefix_stack: List[bool], last: bool) -> str:
            border_chars = border
            pieces = []
            for is_last in prefix_stack[:-1]:
                pieces.append(border_chars["spc"] if is_last else border_chars["bar"])
            if prefix_stack:
                pieces.append(border_chars["ell"] if last else border_chars["tee"])
            return "".join(pieces)

        for d in dirs:
            idx += 1
            last = (idx == total_children)
            lines.append(f"{make_prefix(prefix_stack, last)}{d.name}/")
            dc, dd = 0, 0
            if depth < max_depth:
                dc, dd = iter_tree(d, prefix_stack + [last], depth + 1)
            files_count += dc
            dirs_count += dd + 1  # count this dir too

        # If truncated, show the note as a pseudo-file
        if truncated_note:
            idx += 1
            last = (idx == total_children)
            lines.append(f"{make_prefix(prefix_stack, last)}{truncated_note}")

        # Then files
        for f in visible_files:
            idx += 1
            last = (idx == total_children)
            lines.append(f"{make_prefix(prefix_stack, last)}{f.name}")
        files_count += len(files)

        return files_count, dirs_count

    # Root line
    lines.append(str(root.resolve()) + os.sep)
    fcnt, dcnt = iter_tree(root, [], 1)
    return lines, fcnt, dcnt


def main():
    parser = argparse.ArgumentParser(
        description="Print a clean frontend directory tree (skipping node_modules, build artifacts, etc.)."
    )
    parser.add_argument(
        "path", nargs="?", default=".",
        help="Root path to scan (default: current directory)."
    )
    parser.add_argument(
        "--max-depth", type=int, default=10,
        help="Maximum depth to descend (default: 10)."
    )
    parser.add_argument(
        "--only", type=str, default="",
        help="Comma-separated list of file extensions to include (e.g., .ts,.tsx,.css). Others will be hidden."
    )
    parser.add_argument(
        "--exclude-dirs", type=str, default="",
        help="Comma-separated extra directories to exclude."
    )
    parser.add_argument(
        "--exclude-exts", type=str, default="",
        help="Comma-separated extra file extensions to exclude."
    )
    parser.add_argument(
        "--exclude-files", type=str, default="",
        help="Comma-separated extra file names to exclude."
    )
    parser.add_argument(
        "--max-files", type=int, default=400,
        help="Max files to show per directory (head+tail kept). Use a larger value or 0 for unlimited. Default: 400."
    )
    parser.add_argument(
        "--show-sizes", action="store_true",
        help="Print a final summary line with total files/dirs."
    )
    parser.add_argument(
        "--output", type=str, default="",
        help="Path to write the tree to a file (UTF-8). If omitted, prints to stdout."
    )
    parser.add_argument(
        "--ascii", action="store_true",
        help="Use ASCII borders instead of Unicode (useful on limited consoles)."
    )

    args = parser.parse_args()

    # Switch border set if requested
    global BORDER
    if args.ascii:
        BORDER = ASCII_BORDER

    root = Path(args.path).resolve()

    if not root.exists() or not root.is_dir():
        print(f"Error: path not found or not a directory: {root}", file=sys.stderr)
        sys.exit(1)

    only_exts: Set[str] = set()
    if args.only.strip():
        only_exts = {e.strip().lower() if e.strip().startswith(".") else "." + e.strip().lower()
                     for e in args.only.split(",") if e.strip()}

    exclude_dirs = set(DEFAULT_EXCLUDE_DIRS)
    if args.exclude_dirs.strip():
        exclude_dirs |= {d.strip() for d in args.exclude_dirs.split(",") if d.strip()}

    exclude_exts = set(DEFAULT_EXCLUDE_EXTS)
    if args.exclude_exts.strip():
        exclude_exts |= {e.strip().lower() if e.strip().startswith(".") else "." + e.strip().lower()
                         for e in args.exclude_exts.split(",") if e.strip()}

    exclude_files = set(DEFAULT_EXCLUDE_FILES)
    if args.exclude_files.strip():
        exclude_files |= {f.strip() for f in args.exclude_files.split(",") if f.strip()}

    max_files = None if args.max_files == 0 else max(1, args.max_files)

    lines, total_files, total_dirs = scan_dir(
        root=root,
        max_depth=max(1, args.max_depth),
        only_exts=only_exts,
        exclude_dirs=exclude_dirs,
        exclude_files=exclude_files,
        exclude_exts=exclude_exts,
        max_files=max_files,
    )

    if args.show_sizes:
        # Optional total size (can be slow on big repos; kept lightweight)
        try:
            size_bytes = 0
            for p in root.rglob("*"):
                if p.is_file():
                    try:
                        size_bytes += p.stat().st_size
                    except Exception:
                        pass
            lines.append("")
            lines.append(f"Summary: {total_dirs} dirs, {total_files} files, approx {human_size(size_bytes)} total.")
        except Exception:
            lines.append("")
            lines.append(f"Summary: {total_dirs} dirs, {total_files} files.")

    output_text = "\n".join(lines)

    if args.output:
        Path(args.output).write_text(output_text, encoding="utf-8")
        print(f"Wrote tree to {args.output}")
    else:
        # Ensure Windows terminals don’t garble unicode
        try:
            sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            pass
        print(output_text)


if __name__ == "__main__":
    main()
