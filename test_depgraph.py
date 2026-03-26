#!/usr/bin/env python3
"""Comprehensive tests for depgraph using mock project structures."""

import json
import os
import shutil
import sys
import tempfile
import textwrap
import unittest

# Add parent dir so we can import depgraph
sys.path.insert(0, os.path.dirname(__file__))

from depgraph import (
    build_graph,
    discover_modules,
    find_cycles,
    find_orphans,
    get_reachable,
    output_dot,
    output_json,
    output_summary,
    parse_imports,
    resolve_relative_import,
)


class MockProject:
    """Context manager that creates a temporary mock project structure."""

    def __init__(self, files: dict):
        self.files = files
        self.tmpdir = None

    def __enter__(self):
        self.tmpdir = tempfile.mkdtemp()
        for path, content in self.files.items():
            full_path = os.path.join(self.tmpdir, path)
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, "w") as f:
                f.write(textwrap.dedent(content))
        return self.tmpdir

    def __exit__(self, *args):
        if self.tmpdir:
            shutil.rmtree(self.tmpdir)


class TestDiscoverModules(unittest.TestCase):
    def test_simple_modules(self):
        with MockProject({"main.py": "", "utils.py": ""}) as root:
            modules = discover_modules(root)
            self.assertIn("main", modules)
            self.assertIn("utils", modules)

    def test_packages_with_init(self):
        with MockProject({
            "pkg/__init__.py": "",
            "pkg/core.py": "",
            "pkg/sub/__init__.py": "",
            "pkg/sub/helpers.py": "",
        }) as root:
            modules = discover_modules(root)
            self.assertIn("pkg", modules)
            self.assertIn("pkg.core", modules)
            self.assertIn("pkg.sub", modules)
            self.assertIn("pkg.sub.helpers", modules)

    def test_no_python_files(self):
        with MockProject({"readme.txt": "hello"}) as root:
            modules = discover_modules(root)
            self.assertEqual(len(modules), 0)


class TestResolveRelativeImport(unittest.TestCase):
    def test_single_level(self):
        result = resolve_relative_import("pkg.sub.module", 1, "other")
        self.assertEqual(result, "pkg.sub.other")

    def test_double_level(self):
        result = resolve_relative_import("pkg.sub.module", 2, "top")
        self.assertEqual(result, "pkg.top")

    def test_level_exceeds_depth(self):
        result = resolve_relative_import("module", 5, "other")
        self.assertIsNone(result)

    def test_no_target(self):
        result = resolve_relative_import("pkg.sub.module", 1, None)
        self.assertEqual(result, "pkg.sub")


class TestParseImports(unittest.TestCase):
    def test_basic_import(self):
        with MockProject({"mod.py": "import os\nimport json\n"}) as root:
            imports, stars = parse_imports(os.path.join(root, "mod.py"), "mod")
            self.assertIn("os", imports)
            self.assertIn("json", imports)

    def test_from_import(self):
        with MockProject({"mod.py": "from os.path import join\n"}) as root:
            imports, stars = parse_imports(os.path.join(root, "mod.py"), "mod")
            self.assertIn("os.path", imports)

    def test_relative_import(self):
        with MockProject({"pkg/mod.py": "from . import utils\n"}) as root:
            imports, stars = parse_imports(os.path.join(root, "pkg/mod.py"), "pkg.mod")
            self.assertIn("pkg.utils", imports)

    def test_star_import(self):
        with MockProject({"mod.py": "from utils import *\n"}) as root:
            imports, stars = parse_imports(os.path.join(root, "mod.py"), "mod")
            self.assertIn("utils", stars)

    def test_conditional_import_in_try(self):
        with MockProject({
            "mod.py": """\
                try:
                    import fast_module
                except ImportError:
                    import slow_module
            """
        }) as root:
            imports, stars = parse_imports(os.path.join(root, "mod.py"), "mod")
            self.assertIn("fast_module", imports)
            self.assertIn("slow_module", imports)

    def test_syntax_error_file(self):
        with MockProject({"bad.py": "def broken(\n"}) as root:
            imports, stars = parse_imports(os.path.join(root, "bad.py"), "bad")
            self.assertEqual(imports, [])
            self.assertEqual(stars, [])


class TestBuildGraph(unittest.TestCase):
    def test_simple_graph(self):
        with MockProject({
            "main.py": "import utils\n",
            "utils.py": "",
        }) as root:
            graph, modules, warnings = build_graph(root)
            self.assertIn("utils", graph["main"])

    def test_no_external_deps(self):
        with MockProject({
            "main.py": "import os\nimport json\nimport utils\n",
            "utils.py": "",
        }) as root:
            graph, modules, warnings = build_graph(root)
            # os and json are external, should not appear
            self.assertEqual(graph["main"], {"utils"})

    def test_relative_imports_resolved(self):
        with MockProject({
            "pkg/__init__.py": "",
            "pkg/core.py": "from . import helpers\n",
            "pkg/helpers.py": "",
        }) as root:
            graph, modules, warnings = build_graph(root)
            self.assertIn("pkg.helpers", graph["pkg.core"])

    def test_star_import_warning(self):
        with MockProject({
            "main.py": "from utils import *\n",
            "utils.py": "",
        }) as root:
            graph, modules, warnings = build_graph(root)
            self.assertTrue(any("Star import" in w for w in warnings))


class TestFindCycles(unittest.TestCase):
    def test_no_cycles(self):
        graph = {"a": {"b"}, "b": {"c"}, "c": set()}
        cycles = find_cycles(graph)
        self.assertEqual(len(cycles), 0)

    def test_simple_cycle(self):
        graph = {"a": {"b"}, "b": {"a"}}
        cycles = find_cycles(graph)
        self.assertEqual(len(cycles), 1)

    def test_triangle_cycle(self):
        graph = {"a": {"b"}, "b": {"c"}, "c": {"a"}}
        cycles = find_cycles(graph)
        self.assertEqual(len(cycles), 1)
        # Cycle should contain all three
        self.assertEqual(len(cycles[0]), 4)  # a -> b -> c -> a

    def test_self_cycle(self):
        graph = {"a": {"a"}}
        cycles = find_cycles(graph)
        self.assertEqual(len(cycles), 1)


class TestFindOrphans(unittest.TestCase):
    def test_orphan_detection(self):
        graph = {"main": {"utils"}, "utils": set(), "orphan": set()}
        orphans = find_orphans(graph)
        self.assertIn("orphan", orphans)
        self.assertIn("main", orphans)  # main is also never imported
        self.assertNotIn("utils", orphans)

    def test_no_orphans(self):
        graph = {"a": {"b"}, "b": {"a"}}
        orphans = find_orphans(graph)
        self.assertEqual(orphans, [])


class TestGetReachable(unittest.TestCase):
    def test_depth_limit(self):
        graph = {"a": {"b"}, "b": {"c"}, "c": {"d"}, "d": set()}
        sub = get_reachable(graph, "a", 1)
        self.assertIn("a", sub)
        self.assertIn("b", sub)
        self.assertNotIn("c", sub)

    def test_full_depth(self):
        graph = {"a": {"b"}, "b": {"c"}, "c": set()}
        sub = get_reachable(graph, "a", 10)
        self.assertEqual(len(sub), 3)


class TestOutputDot(unittest.TestCase):
    def test_dot_format(self):
        graph = {"main": {"utils"}, "utils": set()}
        dot = output_dot(graph, [])
        self.assertIn("digraph dependencies", dot)
        self.assertIn('"main" -> "utils"', dot)

    def test_cycle_highlighted(self):
        graph = {"a": {"b"}, "b": {"a"}}
        cycles = [["a", "b", "a"]]
        dot = output_dot(graph, cycles)
        self.assertIn("color=red", dot)


class TestOutputJson(unittest.TestCase):
    def test_valid_json(self):
        graph = {"main": {"utils", "helpers"}, "utils": set(), "helpers": set()}
        result = json.loads(output_json(graph))
        self.assertIn("main", result)
        self.assertIn("utils", result["main"])


class TestOutputSummary(unittest.TestCase):
    def test_summary_contents(self):
        graph = {"main": {"utils"}, "utils": set()}
        summary = output_summary(graph, [], [])
        self.assertIn("Total modules:", summary)
        self.assertIn("Total edges:", summary)
        self.assertIn("2", summary)  # 2 modules


class TestIntegration(unittest.TestCase):
    """Integration test with a realistic project structure."""

    def test_full_project(self):
        with MockProject({
            "myapp/__init__.py": "",
            "myapp/main.py": textwrap.dedent("""\
                from myapp.core import engine
                from myapp.utils import helpers
                import myapp.config
            """),
            "myapp/core/__init__.py": "",
            "myapp/core/engine.py": textwrap.dedent("""\
                from myapp.core import models
                from myapp.utils.helpers import format_output
            """),
            "myapp/core/models.py": textwrap.dedent("""\
                from myapp import config
            """),
            "myapp/utils/__init__.py": "",
            "myapp/utils/helpers.py": textwrap.dedent("""\
                from myapp.config import SETTINGS
            """),
            "myapp/config.py": "",
        }) as root:
            graph, modules, warnings = build_graph(root)

            # Check module discovery
            self.assertIn("myapp.main", modules)
            self.assertIn("myapp.core.engine", modules)
            self.assertIn("myapp.config", modules)

            # Check edges: "from myapp.core import engine" resolves to myapp.core
            self.assertIn("myapp.core", graph.get("myapp.main", set()))

            # No cycles expected
            cycles = find_cycles(graph)
            self.assertEqual(len(cycles), 0)

            # Orphans
            orphans = find_orphans(graph)
            self.assertIn("myapp.main", orphans)  # entry point, never imported

    def test_circular_dependency(self):
        with MockProject({
            "a.py": "import b\n",
            "b.py": "import a\n",
        }) as root:
            graph, modules, warnings = build_graph(root)
            cycles = find_cycles(graph)
            self.assertGreater(len(cycles), 0)


if __name__ == "__main__":
    unittest.main()
