"""The README and the agent guide are contracts: keep them true to the code."""

import ast
import importlib
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

import sightglass
import sightglass.client
from sightglass.cli import guide, parser
from sightglass.plc import parse_variables, read_interface

from .conftest import needs_twincat_router

ROOT = Path(__file__).parent.parent
DOCS = {
    "README.md": ROOT / "README.md",
    "GUIDE.md": ROOT / "src" / "sightglass" / "GUIDE.md",
    "examples/README.md": ROOT / "examples" / "README.md",
    # The reference the README links to.
    **{f"docs/{p.name}": p for p in sorted((ROOT / "docs").glob("*.md"))},
}
REPO = "https://github.com/davidson-engineering/sightglass/blob/main/"
EXAMPLES = sorted((ROOT / "examples").rglob("*.py"))


def python_blocks(path: Path) -> list[str]:
    return re.findall(r"```python\n(.*?)```", path.read_text("utf-8"), re.S)


def check_imports(name: str, source: str) -> None:
    tree = ast.parse(source)  # raises on a syntax error
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module
            and (node.module.startswith("sightglass"))
        ):
            module = (
                sightglass
                if node.module == "sightglass"
                else importlib.import_module(node.module)
            )
            for alias in node.names:
                # __all__ also names the lazily imported classes without
                # importing them (AdsSource can't be on Windows without TwinCAT).
                exported = alias.name in getattr(module, "__all__", ())
                assert exported or hasattr(module, alias.name), f"{name}: {alias.name}"


@pytest.mark.parametrize("name", [n for n in DOCS if n != "examples/README.md"])
def test_python_snippets_compile_and_import_real_names(name):
    blocks = python_blocks(DOCS[name])
    for block in blocks:
        check_imports(name, block)


@pytest.mark.parametrize("name", DOCS)
def test_cli_flags_in_docs_exist(name):
    known = {option for action in parser()._actions for option in action.option_strings}
    text = DOCS[name].read_text("utf-8")
    # The command, followed by a space or nothing: not the package or
    # repository of the same name ("sightglass[web]", ".../sightglass/main/").
    mentioned = set(
        re.findall(r"(?<![\w-])sightglass(?=[ \t]|$)[^\n`\"|]*", text, re.M)
    )
    flags = {
        flag for line in mentioned for flag in re.findall(r"(--[a-z][a-z-]+)", line)
    }
    assert flags <= known, flags - known


def test_guide_command_prints_the_shipped_guide():
    result = subprocess.run(
        [sys.executable, "-m", "sightglass", "--guide"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout == guide() == DOCS["GUIDE.md"].read_text("utf-8")


def test_claude_md_imports_the_guide():
    assert "@src/sightglass/GUIDE.md" in (ROOT / "CLAUDE.md").read_text("utf-8")


@pytest.mark.parametrize(
    "path",
    [
        # sightglass.fake_plc imports pyads
        pytest.param(p, marks=needs_twincat_router)
        if "sightglass.fake_plc" in p.read_text("utf-8")
        else p
        for p in EXAMPLES
    ],
    ids=lambda p: p.name,
)
def test_examples_compile_and_import_real_names(path):
    check_imports(path.name, path.read_text("utf-8"))


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_self_contained_examples_declare_their_dependencies(path):
    """Single-file examples must carry PEP 723 metadata, so `uv run <url>`
    works; examples in a folder of their own, and fake_device.py, need files
    from the repository and run from a clone."""
    needs_repo = path.parent != ROOT / "examples" or path.name == "fake_device.py"
    has_metadata = path.read_text("utf-8").startswith("# /// script")
    assert has_metadata != needs_repo


def test_example_run_commands_point_at_files_that_exist():
    text = DOCS["examples/README.md"].read_text("utf-8")
    base = "https://raw.githubusercontent.com/davidson-engineering/sightglass/main/"
    linked = re.findall(re.escape(base) + r"([\w/.]+)", text)
    assert linked
    for relative in linked:
        assert (ROOT / relative).exists(), relative


def test_example_interface_file_is_valid():
    settings = read_interface(ROOT / "examples" / "plc.toml")
    writable = [v.name for v in parse_variables(settings["variables"]) if v.write]
    assert writable == ["GVL.nSetpoint"]


def anchor(heading: str) -> str:
    """GitHub's id for a Markdown heading."""
    text = re.sub(r"[^\w\- ]", "", heading.strip().lower())
    return text.replace(" ", "-")


@pytest.mark.parametrize("name", DOCS)
def test_links_to_the_docs_reach_a_file_and_heading_that_exist(name):
    """The README links with whole URLs (it is also the PyPI page); the other
    pages link relatively. Either way the file, and its heading, must exist."""
    path = DOCS[name]
    text = path.read_text("utf-8")
    links = re.findall(
        r"\]\((" + re.escape(REPO) + r"[^)\s]+|[^):\s]+\.md(?:#[^)\s]*)?)\)", text
    )
    for link in links:
        target, _, fragment = link.partition("#")
        if target.startswith(REPO):
            file = ROOT / target.removeprefix(REPO)
        else:
            file = (path.parent / target).resolve()
        assert file.exists(), f"{name}: {link}"
        if fragment and file.suffix == ".md":
            headings = re.findall(r"^#+ (.+)$", file.read_text("utf-8"), re.M)
            assert fragment in {anchor(h) for h in headings}, f"{name}: {link}"
    # Same-page anchors.
    headings = {anchor(h) for h in re.findall(r"^#+ (.+)$", text, re.M)}
    for fragment in re.findall(r"\]\(#([^)]+)\)", text):
        assert fragment in headings, f"{name}: #{fragment}"


def snippet_under(path: Path, heading: str) -> str:
    """The first Python block after ``heading``."""
    text = path.read_text("utf-8")
    after = text[text.index(f"\n{heading}\n") :]
    return re.search(r"```python\n(.*?)```", after, re.S).group(1)


# Runs a snippet as written, except that its dashboard takes any free port
# (8080 may be in use) and its sleeps are ten times shorter.
RUN_SNIPPET = """
import json, sys, time

import sightglass
from sightglass import WebDashboard

real_start, real_sleep = sightglass.start, time.sleep
sightglass.start = lambda **options: real_start(
    outputs=[WebDashboard(port=0, announce=False)], **options
)
time.sleep = lambda seconds: real_sleep(seconds / 10)
namespace = {}
exec(sys.stdin.read(), namespace)
monitor = namespace["monitor"]
real_sleep(0.2)  # the last values reach the monitor's thread
print(json.dumps(monitor.snapshot()))
monitor.stop()
"""


@pytest.mark.parametrize(
    ("name", "heading"),
    [
        ("README.md", "## Monitor your own Python app"),
        ("GUIDE.md", "### Monitoring a Python app itself"),
    ],
)
def test_the_monitor_your_own_app_recipe_runs(name, heading):
    result = subprocess.run(
        [sys.executable, "-c", RUN_SNIPPET],
        input=snippet_under(DOCS[name], heading),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    shown = json.loads(result.stdout)
    assert shown["process.rss"]["text"].endswith(" MB")
    assert shown["process.threads"]
    assert "WARNING job" in shown["log"]  # through LogHandler
    if name == "README.md":
        assert shown["jobs.left"] == "0"
        assert int(shown["jobs.done"]) + int(shown["jobs.failed"]) == 50
        assert "INFO all jobs run" in shown["log"]
    else:
        assert shown["jobs.left"] == "7"
