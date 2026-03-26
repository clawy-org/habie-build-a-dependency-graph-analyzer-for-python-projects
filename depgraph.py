#!/usr/bin/env python3
"""
depgraph - Python project dependency graph analyzer.

Analyzes internal module imports and builds a directed dependency graph.
Uses only stdlib (ast module for parsing).
"""

import argparse
import ast
import json
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple


def discover_modules(project_root: str) -> Dict[str, str]:
    """Discover all Python modules in the project. Returns {module_dotted_name: file_path}."""
    modules = {}
    root = Path(project_root).resolve()

    for py_file in root.rglob("*.py"):
        rel = py_file.relative_to(root)
        parts = list(rel.parts)

        if parts[-1] == "__init__.py":
            parts = parts[:-1]
            if not parts:
                # Root __init__.py
                module_name = root.name
            else:
                module_name = ".".join(parts)
        else:
            parts[-1] = parts[-1].replace(".py", "")
            module_name = ".".join(parts)

        modules[module_name] = str(py_file)

    return modules


def resolve_relative_import(current_module: str, level: int, target: Optional[str]) -> Optional[str]:
    """Resolve a relative import to an absolute module name.
    
    Level 1: from . import X → same package
    Level 2: from .. import X → parent package
    
    current_module is the dotted module name of the file containing the import.
    For a file in a package (e.g., pkg/mod.py → "pkg.mod"), level=1 means
    the package itself ("pkg"), so "from . import utils" → "pkg.utils".
    """
    parts = current_module.split(".")

    # Go up 'level' levels from current module
    # level=1 means current package (drop the module name)
    if level > len(parts):
        return None

    base_parts = parts[:-level] if level <= len(parts) else []

    if target:
        # target may be dotted (e.g., "sub.module")
        base_parts.extend(target.split("."))

    return ".".join(base_parts) if base_parts else None


class ImportVisitor(ast.NodeVisitor):
    """AST visitor that extracts import statements."""

    def __init__(self, current_module: str):
        self.current_module = current_module
        self.imports: List[str] = []
        self.star_imports: List[str] = []

    def visit_Import(self, node: ast.Import):
        for alias in node.names:
            self.imports.append(alias.name)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        if node.level > 0:
            # Relative import
            if node.module:
                # e.g., "from .sub import thing" → resolve ".sub"
                resolved = resolve_relative_import(
                    self.current_module, node.level, node.module
                )
                if resolved:
                    if node.names and any(a.name == "*" for a in node.names):
                        self.star_imports.append(resolved)
                    self.imports.append(resolved)
            else:
                # e.g., "from . import helpers, utils" → each name is a sibling module
                base = resolve_relative_import(
                    self.current_module, node.level, None
                )
                if base:
                    for alias in node.names:
                        if alias.name == "*":
                            self.star_imports.append(base)
                            self.imports.append(base)
                        else:
                            candidate = f"{base}.{alias.name}"
                            self.imports.append(candidate)
        else:
            if node.module:
                # Check for star import
                if node.names and any(a.name == "*" for a in node.names):
                    self.star_imports.append(node.module)
                self.imports.append(node.module)
        self.generic_visit(node)

    def visit_Try(self, node: ast.Try):
        """Also visit inside try/except blocks for conditional imports."""
        self.generic_visit(node)


def parse_imports(file_path: str, module_name: str) -> Tuple[List[str], List[str]]:
    """Parse a Python file and return (imports, star_imports)."""
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            source = f.read()
        tree = ast.parse(source, filename=file_path)
        visitor = ImportVisitor(module_name)
        visitor.visit(tree)
        return visitor.imports, visitor.star_imports
    except (SyntaxError, ValueError):
        return [], []


def build_graph(
    project_root: str,
) -> Tuple[Dict[str, Set[str]], Dict[str, str], List[str]]:
    """
    Build the dependency graph.
    Returns (adjacency_dict, modules, star_import_warnings).
    """
    modules = discover_modules(project_root)
    module_names = set(modules.keys())
    graph: Dict[str, Set[str]] = defaultdict(set)
    star_warnings: List[str] = []

    # Ensure all modules appear in the graph even if they import nothing
    for mod in module_names:
        if mod not in graph:
            graph[mod] = set()

    for module_name, file_path in modules.items():
        raw_imports, star_imports = parse_imports(file_path, module_name)

        for imp in raw_imports:
            # Check if it's an internal module or a prefix of one
            if imp in module_names:
                graph[module_name].add(imp)
            else:
                # Check if importing a submodule (e.g., "pkg.sub" when "pkg" exists)
                parts = imp.split(".")
                for i in range(len(parts), 0, -1):
                    candidate = ".".join(parts[:i])
                    if candidate in module_names:
                        graph[module_name].add(candidate)
                        break

        for star in star_imports:
            if star in module_names or any(
                m.startswith(star + ".") for m in module_names
            ):
                star_warnings.append(
                    f"Star import: 'from {star} import *' in {module_name}"
                )

    return dict(graph), modules, star_warnings


def find_cycles(graph: Dict[str, Set[str]]) -> List[List[str]]:
    """Find all circular dependencies using DFS."""
    cycles = []
    visited: Set[str] = set()
    rec_stack: Set[str] = set()
    path: List[str] = []

    def dfs(node: str):
        visited.add(node)
        rec_stack.add(node)
        path.append(node)

        for neighbor in sorted(graph.get(node, set())):
            if neighbor not in visited:
                dfs(neighbor)
            elif neighbor in rec_stack:
                # Found a cycle
                cycle_start = path.index(neighbor)
                cycle = path[cycle_start:] + [neighbor]
                # Normalize: start from the smallest element
                min_idx = cycle[:-1].index(min(cycle[:-1]))
                normalized = cycle[min_idx:-1] + cycle[:min_idx] + [cycle[min_idx]]
                if normalized not in cycles:
                    cycles.append(normalized)

        path.pop()
        rec_stack.discard(node)

    for node in sorted(graph.keys()):
        if node not in visited:
            dfs(node)

    return cycles


def find_orphans(graph: Dict[str, Set[str]]) -> List[str]:
    """Find modules that are never imported by anything."""
    all_modules = set(graph.keys())
    imported = set()
    for deps in graph.values():
        imported.update(deps)
    return sorted(all_modules - imported)


def get_reachable(graph: Dict[str, Set[str]], entry: str, max_depth: int) -> Dict[str, Set[str]]:
    """Get subgraph reachable from entry up to max_depth."""
    subgraph: Dict[str, Set[str]] = {}
    queue = [(entry, 0)]
    visited = set()

    while queue:
        node, depth = queue.pop(0)
        if node in visited or depth > max_depth:
            continue
        visited.add(node)
        deps = graph.get(node, set())
        subgraph[node] = deps
        if depth < max_depth:
            for dep in sorted(deps):
                queue.append((dep, depth + 1))

    return subgraph


def output_dot(graph: Dict[str, Set[str]], cycles: List[List[str]]) -> str:
    """Generate Graphviz DOT format output."""
    cycle_edges = set()
    for cycle in cycles:
        for i in range(len(cycle) - 1):
            cycle_edges.add((cycle[i], cycle[i + 1]))

    lines = ["digraph dependencies {", '    rankdir=LR;', '    node [shape=box, style=filled, fillcolor=lightblue];']

    for module in sorted(graph.keys()):
        for dep in sorted(graph[module]):
            attrs = ""
            if (module, dep) in cycle_edges:
                attrs = ' [color=red, penwidth=2, label="cycle"]'
            lines.append(f'    "{module}" -> "{dep}"{attrs};')

    lines.append("}")
    return "\n".join(lines)


def output_json(graph: Dict[str, Set[str]]) -> str:
    """Generate JSON adjacency list output."""
    serializable = {k: sorted(v) for k, v in sorted(graph.items())}
    return json.dumps(serializable, indent=2)


def output_summary(
    graph: Dict[str, Set[str]],
    cycles: List[List[str]],
    star_warnings: List[str],
    orphans: Optional[List[str]] = None,
) -> str:
    """Generate summary statistics."""
    total_modules = len(graph)
    total_edges = sum(len(deps) for deps in graph.values())

    # Most imported module
    import_count: Dict[str, int] = defaultdict(int)
    for deps in graph.values():
        for dep in deps:
            import_count[dep] += 1

    most_imported = max(import_count.items(), key=lambda x: x[1]) if import_count else ("(none)", 0)

    # Most dependent module
    dep_counts = {mod: len(deps) for mod, deps in graph.items()}
    most_dependent = max(dep_counts.items(), key=lambda x: x[1]) if dep_counts else ("(none)", 0)

    lines = [
        "=== Dependency Graph Summary ===",
        f"Total modules:        {total_modules}",
        f"Total edges:          {total_edges}",
        f"Most imported:        {most_imported[0]} ({most_imported[1]} importers)",
        f"Most dependent:       {most_dependent[0]} ({most_dependent[1]} imports)",
        f"Circular deps found:  {len(cycles)}",
    ]

    if cycles:
        lines.append("\nCircular dependencies:")
        for cycle in cycles:
            lines.append(f"  {' -> '.join(cycle)}")

    if star_warnings:
        lines.append(f"\n⚠ Star import warnings ({len(star_warnings)}):")
        for w in star_warnings:
            lines.append(f"  {w}")

    if orphans is not None:
        lines.append(f"\nOrphan modules ({len(orphans)}):")
        for o in orphans:
            lines.append(f"  {o}")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Analyze Python project dependency graph",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Examples:\n"
        "  depgraph ./myproject --summary\n"
        "  depgraph ./myproject --dot > deps.dot\n"
        "  depgraph ./myproject --json\n"
        "  depgraph ./myproject --summary --orphans\n"
        "  depgraph ./myproject --dot --depth 2 --entry myproject.main\n",
    )
    parser.add_argument("project_root", help="Path to the Python project root directory")
    parser.add_argument("--dot", action="store_true", help="Output in Graphviz DOT format")
    parser.add_argument("--json", action="store_true", help="Output adjacency list as JSON")
    parser.add_argument("--summary", action="store_true", help="Print summary statistics")
    parser.add_argument("--orphans", action="store_true", help="List modules never imported by anything")
    parser.add_argument("--depth", type=int, default=None, help="Limit graph traversal depth from --entry")
    parser.add_argument("--entry", type=str, default=None, help="Entry point module for --depth")

    args = parser.parse_args()

    if not os.path.isdir(args.project_root):
        print(f"Error: '{args.project_root}' is not a directory", file=sys.stderr)
        sys.exit(1)

    if not any([args.dot, args.json, args.summary]):
        args.summary = True  # Default to summary

    graph, modules, star_warnings = build_graph(args.project_root)

    if not graph:
        print("No Python modules found.", file=sys.stderr)
        sys.exit(1)

    # Apply depth filter if specified
    if args.depth is not None:
        if not args.entry:
            print("Error: --depth requires --entry", file=sys.stderr)
            sys.exit(1)
        if args.entry not in graph:
            print(f"Error: entry module '{args.entry}' not found", file=sys.stderr)
            sys.exit(1)
        graph = get_reachable(graph, args.entry, args.depth)

    cycles = find_cycles(graph)
    orphans = find_orphans(graph) if args.orphans else None

    if args.dot:
        print(output_dot(graph, cycles))
    if args.json:
        print(output_json(graph))
    if args.summary:
        print(output_summary(graph, cycles, star_warnings, orphans))


if __name__ == "__main__":
    main()
