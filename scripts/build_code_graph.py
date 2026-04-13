#!/usr/bin/env python3
"""
build_code_graph.py — Lightweight code-review-graph for Claude Code
Scans Python files, builds an import/dependency graph, and writes:
  graphify-out/GRAPH_REPORT.md   — god nodes, communities, edge list
  graphify-out/wiki/index.md     — per-module wiki stubs

Usage:
  python3 scripts/build_code_graph.py [repo_root]
  # repo_root defaults to the directory containing this script's parent
"""

import ast
import sys
import os
import re
from pathlib import Path
from collections import defaultdict, Counter
from datetime import datetime


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
EXCLUDE_DIRS = {
    "__pycache__", ".git", "node_modules", "venv", ".venv",
    "dist", "build", ".next", "graphify-out",
}
EXCLUDE_FILES = {"conftest.py"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def repo_root(script_path: Path) -> Path:
    if len(sys.argv) > 1:
        return Path(sys.argv[1]).resolve()
    return script_path.parent.parent


def collect_py_files(root: Path) -> list[Path]:
    files = []
    for p in root.rglob("*.py"):
        parts = set(p.relative_to(root).parts)
        if parts & EXCLUDE_DIRS:
            continue
        if p.name in EXCLUDE_FILES:
            continue
        files.append(p)
    return sorted(files)


def module_name(path: Path, root: Path) -> str:
    rel = path.relative_to(root)
    parts = list(rel.parts)
    if parts[-1] == "__init__.py":
        parts = parts[:-1]
    else:
        parts[-1] = parts[-1][:-3]  # strip .py
    return ".".join(parts)


def parse_imports(path: Path, root: Path) -> list[str]:
    """Return list of internal module names imported by `path`."""
    try:
        source = path.read_text(encoding="utf-8", errors="ignore")
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return []

    own_pkg = module_name(path, root).split(".")[0]
    imports: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top == own_pkg or (root / top).is_dir() or (root / (top + ".py")).exists():
                    imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module is None:
                continue
            top = node.module.split(".")[0]
            if node.level > 0:  # relative import
                imports.append(node.module)
            elif top == own_pkg or (root / top).is_dir() or (root / (top + ".py")).exists():
                imports.append(node.module)

    return imports


def count_lines(path: Path) -> int:
    try:
        return sum(1 for _ in path.open(encoding="utf-8", errors="ignore"))
    except OSError:
        return 0


def extract_functions(path: Path) -> list[str]:
    try:
        source = path.read_text(encoding="utf-8", errors="ignore")
        tree = ast.parse(source)
        return [n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    except SyntaxError:
        return []


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------

def build_graph(root: Path):
    files = collect_py_files(root)
    if not files:
        return {}, {}, {}

    # module → set of modules it imports
    edges: dict[str, set[str]] = defaultdict(set)
    meta: dict[str, dict] = {}

    for f in files:
        mod = module_name(f, root)
        loc = count_lines(f)
        fns = extract_functions(f)
        meta[mod] = {"path": str(f.relative_to(root)), "loc": loc, "functions": fns}
        for imp in parse_imports(f, root):
            if imp != mod:
                edges[mod].add(imp)

    # Ensure every referenced module is in edges (even if it imports nothing)
    all_mods = set(meta.keys())
    for deps in list(edges.values()):
        all_mods |= deps
    for m in all_mods:
        if m not in edges:
            edges[m] = set()

    # inbound degree
    in_degree: Counter = Counter()
    for deps in edges.values():
        for d in deps:
            in_degree[d] += 1

    return edges, in_degree, meta


# ---------------------------------------------------------------------------
# Community detection (simple: connected components on undirected projection)
# ---------------------------------------------------------------------------

def find_communities(edges: dict[str, set[str]]) -> dict[str, int]:
    parent: dict[str, str] = {}

    def find(x):
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for mod, deps in edges.items():
        for d in deps:
            union(mod, d)

    communities: Counter = Counter()
    mapping: dict[str, int] = {}
    id_map: dict[str, int] = {}
    for mod in edges:
        root_node = find(mod)
        if root_node not in id_map:
            id_map[root_node] = len(id_map)
        mapping[mod] = id_map[root_node]

    return mapping


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def god_node_threshold(in_degree: Counter, edges: dict[str, set[str]]) -> int:
    """Nodes with in_degree + out_degree above this are 'god nodes'."""
    total_degrees = [in_degree.get(m, 0) + len(edges.get(m, set())) for m in edges]
    if not total_degrees:
        return 3
    avg = sum(total_degrees) / len(total_degrees)
    return max(3, int(avg * 1.5))


def write_graph_report(
    root: Path,
    edges: dict[str, set[str]],
    in_degree: Counter,
    meta: dict[str, dict],
    communities: dict[str, int],
    out_dir: Path,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    threshold = god_node_threshold(in_degree, edges)

    god_nodes = sorted(
        [m for m in edges if in_degree.get(m, 0) + len(edges.get(m, set())) >= threshold],
        key=lambda m: -(in_degree.get(m, 0) + len(edges.get(m, set()))),
    )

    # Group communities
    community_groups: dict[int, list[str]] = defaultdict(list)
    for mod, cid in communities.items():
        community_groups[cid].append(mod)
    community_groups = {
        cid: sorted(mods)
        for cid, mods in sorted(community_groups.items(), key=lambda x: -len(x[1]))
    }

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        f"# Code Graph Report",
        f"",
        f"_Generated: {now}_  |  _Root: {root}_",
        f"",
        f"## Summary",
        f"",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Python modules | {len(edges)} |",
        f"| Internal edges | {sum(len(d) for d in edges.values())} |",
        f"| God-node threshold (degree) | ≥ {threshold} |",
        f"| God nodes | {len(god_nodes)} |",
        f"| Communities | {len(community_groups)} |",
        f"",
    ]

    # God nodes section
    lines += ["## God Nodes", "", "Modules with the highest combined in+out degree — highest blast radius for changes.", ""]
    if god_nodes:
        lines += ["| Module | In | Out | LoC | Path |", "|--------|-----|-----|-----|------|"]
        for m in god_nodes[:20]:
            info = meta.get(m, {})
            lines.append(
                f"| `{m}` | {in_degree.get(m,0)} | {len(edges.get(m,set()))} "
                f"| {info.get('loc','?')} | `{info.get('path','—')}` |"
            )
    else:
        lines.append("_No god nodes detected — graph is well-distributed._")
    lines.append("")

    # Communities section
    lines += ["## Communities", "", "Clusters of tightly coupled modules (union-find on import graph).", ""]
    for cid, mods in list(community_groups.items())[:10]:
        lines.append(f"### Community {cid} ({len(mods)} modules)")
        lines.append("")
        for m in mods[:15]:
            info = meta.get(m, {})
            lines.append(f"- `{m}` ({info.get('loc','?')} LoC, {len(edges.get(m, set()))} deps)")
        if len(mods) > 15:
            lines.append(f"- … and {len(mods)-15} more")
        lines.append("")

    # Full edge list (compact)
    lines += ["## Edge List (internal imports)", ""]
    lines += ["```"]
    for mod in sorted(edges):
        for dep in sorted(edges[mod]):
            lines.append(f"{mod} → {dep}")
    lines += ["```", ""]

    report_path = out_dir / "GRAPH_REPORT.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"[code-review-graph] Wrote {report_path}")


def write_wiki_index(
    root: Path,
    edges: dict[str, set[str]],
    in_degree: Counter,
    meta: dict[str, dict],
    out_dir: Path,
) -> None:
    wiki_dir = out_dir / "wiki"
    wiki_dir.mkdir(parents=True, exist_ok=True)

    index_lines = ["# Module Wiki", "", "Auto-generated index of internal modules.", ""]
    for mod in sorted(meta):
        info = meta[mod]
        fns = info.get("functions", [])
        deps = sorted(edges.get(mod, set()))
        index_lines += [
            f"## `{mod}`",
            f"",
            f"- **Path**: `{info.get('path','—')}`",
            f"- **LoC**: {info.get('loc', '?')}",
            f"- **In-degree**: {in_degree.get(mod, 0)}",
            f"- **Imports**: {', '.join(f'`{d}`' for d in deps) if deps else '_none_'}",
            f"- **Functions/methods**: {', '.join(f'`{f}`' for f in fns[:10]) if fns else '_none_'}",
            f"",
        ]

    (wiki_dir / "index.md").write_text("\n".join(index_lines), encoding="utf-8")
    print(f"[code-review-graph] Wrote {wiki_dir / 'index.md'}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    script_path = Path(__file__).resolve()
    root = repo_root(script_path)
    out_dir = root / "graphify-out"

    print(f"[code-review-graph] Scanning {root} …")
    edges, in_degree, meta = build_graph(root)

    if not edges:
        print("[code-review-graph] No Python files found — nothing to graph.")
        sys.exit(0)

    communities = find_communities(edges)
    write_graph_report(root, edges, in_degree, meta, communities, out_dir)
    write_wiki_index(root, edges, in_degree, meta, out_dir)
    print(f"[code-review-graph] Done. {len(edges)} modules, {len(communities)} communities.")


if __name__ == "__main__":
    main()
