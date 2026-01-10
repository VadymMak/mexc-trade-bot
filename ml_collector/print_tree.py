import os
import sys
import argparse

DEFAULT_IGNORES = {
    ".git", ".venv", "venv", "__pycache__", ".mypy_cache", ".ruff_cache",
    ".pytest_cache", ".vscode", ".idea", "node_modules",
    "dist", "build", "site-packages", "Lib", "Scripts"
}

def print_tree(root: str, max_depth: int, ignores: set[str], prefix: str = "", level: int = 0):
    try:
        entries = sorted(os.listdir(root))
    except Exception:
        return

    # Filter ignored names
    entries = [e for e in entries if e not in ignores]

    # Limit number of entries if needed (optional – comment out if you want all)
    # entries = entries[:200]

    for i, name in enumerate(entries):
        path = os.path.join(root, name)
        connector = "└── " if i == len(entries) - 1 else "├── "
        is_dir = os.path.isdir(path)
        suffix = "/" if is_dir else ""
        print(prefix + connector + name + suffix)

        if is_dir and (max_depth is None or level + 1 < max_depth):
            extension = "    " if i == len(entries) - 1 else "│   "
            print_tree(path, max_depth, ignores, prefix + extension, level + 1)

def main():
    ap = argparse.ArgumentParser(description="Pretty-print a filtered folder tree.")
    ap.add_argument("target", nargs="?", default=".", help="Root folder to print (default: .)")
    ap.add_argument("--max-depth", type=int, default=3, help="How many levels to show (default: 3). Use 0 for only root, or omit for full.")
    ap.add_argument("--ignore", action="append", default=[], help="Add names to ignore (can be used multiple times).")
    args = ap.parse_args()

    # Normalize path
    target = os.path.normpath(args.target)
    ignores = DEFAULT_IGNORES.union(set(args.ignore))

    # Print header
    print(os.path.abspath(target) + "/")
    print_tree(target, args.max_depth, ignores)

if __name__ == "__main__":
    main()
