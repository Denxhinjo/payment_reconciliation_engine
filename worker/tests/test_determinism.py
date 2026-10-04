"""Determinism (design §6): the engine is a pure function of the three raw files and its version.

- A static check proves the engine and every module it uses never touch the clock, randomness,
  the environment, the locale, the network, threads or other processes, and import nothing
  outside the checked set. A test of the checker proves it can fail.
- A golden hash pins the demo month's result per ENGINE_VERSION, so a behaviour change without a
  version bump fails here.
- Fresh interpreters with different hash seeds, time zones and locales produce the same bytes.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from recon.engine import ENGINE_VERSION, reconcile, run_engine

WORKER = Path(__file__).resolve().parents[1]
DEMO = WORKER.parent / "demo-data" / "2026-09"
DEMO_FILES = tuple((DEMO / name).read_bytes() for name in (
    "synthetic_ledger_2026-09.csv", "synthetic_orrery_settlement_2026-09.csv",
    "synthetic_bank_camt053_2026-09.xml"))
GOLDEN = json.loads((Path(__file__).parent / "golden" / "results.json").read_text())

# The engine and everything it executes to produce a result.
ENGINE_MODULES = {
    "recon.engine": WORKER / "recon" / "engine.py",
    "recon.money": WORKER / "recon" / "money.py",
    "recon.camt053": WORKER / "recon" / "camt053.py",
    "recon.parse": WORKER / "recon" / "parse" / "__init__.py",
    "recon.parse.csvfiles": WORKER / "recon" / "parse" / "csvfiles.py",
    "recon.parse.bank": WORKER / "recon" / "parse" / "bank.py",
}

FORBIDDEN_MODULES = {
    "time", "random", "uuid", "secrets", "os", "locale", "socket", "subprocess", "threading",
    "multiprocessing", "asyncio", "urllib", "http", "requests", "psycopg", "sys", "platform",
    "getpass", "tempfile",
}
FORBIDDEN_ATTRIBUTES = {"now", "today", "utcnow", "fromtimestamp", "utcfromtimestamp",
                        "environ", "getenv", "getlocale", "setlocale"}
FORBIDDEN_BUILTINS = {"hash", "id", "open", "input", "eval", "exec", "__import__"}


def impurities(source: str) -> list[str]:
    """Every construct in ``source`` that could make output depend on more than the inputs."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in FORBIDDEN_MODULES:
                    found.append(f"line {node.lineno}: import {alias.name}")
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            if node.module.split(".")[0] in FORBIDDEN_MODULES:
                found.append(f"line {node.lineno}: from {node.module} import ...")
        elif isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_ATTRIBUTES:
            found.append(f"line {node.lineno}: .{node.attr}")
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
              and node.func.id in FORBIDDEN_BUILTINS):
            found.append(f"line {node.lineno}: {node.func.id}()")
    return found


def recon_imports(source: str) -> set[str]:
    modules = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("recon"):
            if node.module == "recon":
                # `from recon import camt053` imports the submodule recon.camt053.
                modules.update(f"recon.{a.name}" for a in node.names)
            else:
                modules.add(node.module)
        elif isinstance(node, ast.Import):
            modules.update(a.name for a in node.names if a.name.startswith("recon"))
    return modules


# --- static purity --------------------------------------------------------------------------------

@pytest.mark.parametrize("module", sorted(ENGINE_MODULES))
def test_engine_modules_have_no_clock_randomness_environment_or_io(module):
    assert impurities(ENGINE_MODULES[module].read_text(encoding="utf-8")) == []


@pytest.mark.parametrize("module", sorted(ENGINE_MODULES))
def test_engine_imports_nothing_outside_the_checked_modules(module):
    """Purity cannot leak in through an unchecked recon module (e.g. recon.runs or the importer)."""
    assert recon_imports(ENGINE_MODULES[module].read_text(encoding="utf-8")) <= set(ENGINE_MODULES)


@pytest.mark.parametrize("snippet", [
    "import time", "import random", "from random import choice", "import uuid", "import secrets",
    "import os", "from os import environ", "import locale", "import sys", "import psycopg",
    "from datetime import datetime\nx = datetime.now()", "from datetime import date\nx = date.today()",
    "import datetime\nx = datetime.datetime.utcnow()", "x = hash('a')", "x = id(object())",
    "x = open('f').read()", "x = d.fromtimestamp(0)",
])
def test_the_purity_checker_catches_each_forbidden_construct(snippet):
    assert impurities(snippet), snippet


def test_the_purity_checker_allows_what_the_engine_legitimately_uses():
    assert impurities("from datetime import date, timedelta\nimport hashlib, json\n"
                      "x = date(2026, 9, 30) + timedelta(days=3)") == []


# --- golden result hash ---------------------------------------------------------------------------

def test_this_engine_version_has_a_golden_entry():
    assert ENGINE_VERSION in GOLDEN, (
        f"no golden result for engine {ENGINE_VERSION}; a new version needs a new entry in "
        "tests/golden/results.json (never edit an existing one)")


def test_demo_month_result_equals_the_golden_hash_for_this_engine_version():
    (expected,) = GOLDEN[ENGINE_VERSION].values()
    actual = hashlib.sha256(reconcile(*DEMO_FILES)).hexdigest()
    assert actual == expected, (
        "the demo month's result changed: matching behaviour changed. Bump ENGINE_VERSION and add "
        "a golden entry for it; do not edit the existing entry.")


def test_the_result_contains_no_wall_clock_or_environment_fields():
    document = json.loads(reconcile(*DEMO_FILES))
    assert set(document) == {"format", "engine_version", "parser_version", "config", "inputs",
                             "statement", "matches", "exceptions", "summary"}
    text = json.dumps(document)
    for word in ("started", "finished", "created_at", "hostname", "git"):
        assert word not in text


# --- fresh interpreters with different hash seeds, time zones and locales -------------------------

SCRIPT = """
import hashlib, pathlib
from recon.engine import reconcile
d = pathlib.Path(r"{demo}")
files = [(d / n).read_bytes() for n in ("synthetic_ledger_2026-09.csv",
         "synthetic_orrery_settlement_2026-09.csv", "synthetic_bank_camt053_2026-09.xml")]
print(hashlib.sha256(reconcile(*files)).hexdigest())
"""


@pytest.mark.parametrize("environment", [
    {"PYTHONHASHSEED": "0"},
    {"PYTHONHASHSEED": "4242"},
    {"PYTHONHASHSEED": "random"},
    {"PYTHONHASHSEED": "1", "TZ": "Pacific/Kiritimati", "LC_ALL": "de_DE.UTF-8", "LANG": "de_DE.UTF-8"},
    {"PYTHONHASHSEED": "2", "TZ": "America/Adak", "LC_ALL": "C", "LANG": "C"},
], ids=["seed-0", "seed-4242", "seed-random", "kiritimati-de", "adak-c"])
def test_a_fresh_interpreter_reproduces_the_golden_bytes(environment):
    env = dict(os.environ, **environment)
    proc = subprocess.run([sys.executable, "-c", SCRIPT.format(demo=DEMO)], cwd=WORKER, env=env,
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    (expected,) = GOLDEN[ENGINE_VERSION].values()
    assert proc.stdout.strip() == expected


def test_in_process_repeat_is_byte_identical():
    first = run_engine(*DEMO_FILES).canonical()
    second = run_engine(*DEMO_FILES).canonical()
    assert first == second
