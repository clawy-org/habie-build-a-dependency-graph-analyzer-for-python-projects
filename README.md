# depgraph — Python Dependency Graph Analyzer

Analyze internal module imports in a Python project and build a directed dependency graph. Detect circular dependencies, orphan modules, and star imports.

**Zero external dependencies** — uses only Python stdlib (`ast`, `argparse`, `json`).

## Usage

```bash
# Summary (default)
python depgraph.py ./myproject

# Graphviz DOT output (pipe to dot for visualization)
python depgraph.py ./myproject --dot > deps.dot
dot -Tpng deps.dot -o deps.png

# JSON adjacency list
python depgraph.py ./myproject --json

# Summary with orphan detection
python depgraph.py ./myproject --summary --orphans

# Limit depth from a specific entry point
python depgraph.py ./myproject --dot --entry myproject.main --depth 2
```

## Output Formats

| Flag | Description |
|------|-------------|
| `--summary` | Stats: total modules/edges, most imported/dependent, circular deps (default) |
| `--dot` | Graphviz DOT format — cycle edges highlighted in red |
| `--json` | JSON adjacency list |
| `--orphans` | List modules never imported by anything |
| `--depth N` | Limit traversal depth from `--entry` module |

## Features

- Recursive `.py` file discovery
- Handles `import X`, `from X import Y`, relative imports (`from . import Z`)
- Detects imports inside `try/except` blocks
- Circular dependency detection with clear reporting
- Star import (`from X import *`) warnings
- Orphan module detection
- Depth-limited subgraph extraction

## Running Tests

```bash
python -m unittest test_depgraph -v
```

31 tests covering module discovery, import parsing, graph building, cycle detection, output formats, and integration scenarios.
# TODO: add support for circular dependency detection
# TODO: add export to DOT format
