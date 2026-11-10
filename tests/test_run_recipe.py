"""scripts/run_recipe.py: the justfile runner for machines without `just` (issue 98)."""
import contextlib
import importlib.util
import io
import pathlib
import shlex
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("run_recipe", ROOT / "scripts" / "run_recipe.py")
runner = importlib.util.module_from_spec(_spec)
sys.modules["run_recipe"] = runner   # dataclasses look the defining module up here
_spec.loader.exec_module(runner)

PY = shlex.quote(sys.executable)


def append_line(path, text):
    """A recipe body line that appends TEXT to PATH: shows which lines ran, and in what order."""
    return f"{PY} -c 'import sys; open(sys.argv[1], \"a\").write(sys.argv[2] + \"\\n\")' {shlex.quote(str(path))} {text}"


class RealJustfileTests(unittest.TestCase):
    def test_every_recipe_in_the_real_justfile_is_runnable(self):
        recipes = runner.parse_justfile((ROOT / "justfile").read_text())
        self.assertIn("preflight", recipes)
        self.assertEqual(recipes["preflight"].deps, ["test", "docs", "lint", "check", "self-check", "agent-log-check"])
        self.assertEqual(recipes["ship-land"].params, ["pr", "*args"])
        self.assertTrue(recipes["test"].description)

    def test_listing_names_every_recipe_but_default(self):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            self.assertEqual(runner.main([]), 0)
        for name in ("preflight", "self-check", "ci-wait", "ship-land pr *args"):
            self.assertIn(name, stdout.getvalue())
        self.assertNotIn("default", stdout.getvalue())

    def test_unknown_recipe_exits_2(self):
        proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "run_recipe.py"), "no-such-recipe"],
                              capture_output=True, text=True, env={"PATH": ""})
        self.assertEqual(proc.returncode, 2)
        self.assertIn("no recipe 'no-such-recipe'", proc.stderr)


class ParseTests(unittest.TestCase):
    def refused(self, text, fragment):
        with self.assertRaises(runner.JustfileError) as caught:
            runner.parse_justfile(text)
        self.assertIn(fragment, str(caught.exception))

    def test_description_params_and_deps(self):
        recipes = runner.parse_justfile("# say it\nhello who *rest: a\n    echo {{who}} {{rest}}\n\na:\n    true\n")
        self.assertEqual(recipes["hello"].description, "say it")
        self.assertEqual(recipes["hello"].params, ["who", "*rest"])
        self.assertEqual(recipes["hello"].deps, ["a"])
        self.assertEqual(recipes["hello"].lines, ["echo {{who}} {{rest}}"])

    def test_a_blank_line_ends_a_description(self):
        recipes = runner.parse_justfile("# about the file\n\nhello:\n    true\n")
        self.assertEqual(recipes["hello"].description, "")

    def test_shebang_recipe_is_refused(self):
        self.refused("x:\n    #!/usr/bin/env bash\n    true\n", "shebang")

    def test_settings_and_variables_are_refused(self):
        self.refused("set shell := ['bash', '-c']\n", "not a recipe header")
        self.refused('name := "x"\n', "not a recipe header")

    def test_expression_or_unknown_name_in_braces_is_refused(self):
        self.refused("x a:\n    echo {{b}}\n", "not a parameter")
        self.refused("x a:\n    echo {{a + 'y'}}\n", "not a parameter")

    def test_unknown_dependency_is_refused(self):
        self.refused("x: y\n    true\n", "unknown recipe 'y'")

    def test_dependency_with_parameters_is_refused(self):
        self.refused("x: y\n    true\n\ny a:\n    true\n", "takes parameters")

    def test_duplicate_recipe_is_refused(self):
        self.refused("x:\n    true\n\nx:\n    true\n", "declared twice")

    def test_variadic_not_last_is_refused(self):
        self.refused("x *a b:\n    true\n", "variadic")


class BindTests(unittest.TestCase):
    recipe = runner.Recipe("ship-land", ["pr", "*args"], [])

    def test_variadic_arguments_are_shell_quoted(self):
        bound = runner.bind_arguments(self.recipe, ["12", "--allow-base", "two words"])
        self.assertEqual(bound, {"pr": "12", "args": "--allow-base 'two words'"})
        self.assertEqual(runner.render_line("ship {{pr}} {{ args }}", bound), "ship 12 --allow-base 'two words'")

    def test_missing_required_argument_is_a_usage_error(self):
        with self.assertRaises(runner.JustfileError) as caught:
            runner.bind_arguments(self.recipe, [])
        self.assertIn("usage: run_recipe.py ship-land PR [ARGS ...]", str(caught.exception))

    def test_extra_argument_without_a_variadic_is_a_usage_error(self):
        with self.assertRaises(runner.JustfileError):
            runner.bind_arguments(runner.Recipe("test", [], []), ["extra"])


class RunTests(unittest.TestCase):
    def setUp(self):
        self.log = pathlib.Path(tempfile.mkdtemp(prefix="pm-recipe-")) / "ran.log"

    def ran(self):
        return self.log.read_text().split() if self.log.exists() else []

    def run_recipes(self, text, name, args=()):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            rc = runner.run(runner.parse_justfile(text), name, list(args))
        return rc, stderr.getvalue()

    def test_dependencies_run_first_and_once(self):
        text = (f"all: a b\n    {append_line(self.log, 'all')}\n\n"
                f"a: b\n    {append_line(self.log, 'a')}\n\n"
                f"b:\n    {append_line(self.log, 'b')}\n")
        rc, _ = self.run_recipes(text, "all")
        self.assertEqual(rc, 0)
        self.assertEqual(self.ran(), ["b", "a", "all"])

    def test_first_failing_line_stops_the_run_with_its_status(self):
        text = f"x:\n    {append_line(self.log, 'one')}\n    exit 3\n    {append_line(self.log, 'two')}\n"
        rc, stderr = self.run_recipes(text, "x")
        self.assertEqual(rc, 3)
        self.assertEqual(self.ran(), ["one"])
        self.assertIn("recipe `x` failed on line 2 with exit code 3", stderr)

    def test_failing_dependency_stops_before_the_recipe(self):
        text = f"x: y\n    {append_line(self.log, 'x')}\n\ny:\n    exit 1\n"
        rc, _ = self.run_recipes(text, "x")
        self.assertEqual(rc, 1)
        self.assertEqual(self.ran(), [])

    def test_lines_are_echoed_unless_quiet(self):
        rc, stderr = self.run_recipes("x:\n    true loud\n    @true quiet\n", "x")
        self.assertEqual(rc, 0)
        self.assertIn("true loud", stderr)
        self.assertNotIn("quiet", stderr)

    def test_arguments_reach_the_line_quoted(self):
        rc, _ = self.run_recipes(f"x *args:\n    {append_line(self.log, '{{args}}')}\n", "x", ["one;touch-nothing"])
        self.assertEqual(rc, 0)
        self.assertEqual(self.ran(), ["one;touch-nothing"])


if __name__ == "__main__":
    unittest.main()
