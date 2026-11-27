"""The `tracker` command: how it ends on a known problem and on a bug."""

import ast
import os
import pathlib
import subprocess
import sys

import pytest

import tracker
from tracker import build, cli

RUN = "import sys; from tracker.cli import main; main(sys.argv[1:])"


def run_command(args, env_extra):
    return subprocess.run(
        [sys.executable, "-c", RUN, *args],
        capture_output=True,
        text=True,
        env={**os.environ, **env_extra},
        check=False,
    )


def test_a_known_problem_prints_only_its_message(tmp_path):
    result = run_command(
        ["build", "--out", str(tmp_path / "out")],
        {"TRACKER_STORE": "local", "TRACKER_STORE_DIR": str(tmp_path / "store")},
    )

    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert result.stderr.strip().splitlines()[-1] == (
        "No source has a clean table, run `tracker ingest`"
    )


def test_a_wrong_store_setting_prints_only_its_message():
    result = run_command(["build"], {"TRACKER_STORE": "bogus"})

    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert "TRACKER_STORE must be 'local' or 'hf'" in result.stderr


def test_an_unexpected_error_keeps_its_traceback(tmp_path, monkeypatch):
    def fail(store, out):
        raise RuntimeError("a bug")

    monkeypatch.setenv("TRACKER_STORE", "local")
    monkeypatch.setenv("TRACKER_STORE_DIR", str(tmp_path / "store"))
    monkeypatch.setattr(build, "build", fail)
    with pytest.raises(RuntimeError, match="a bug"):
        cli.main(["build", "--out", str(tmp_path)])


def test_a_library_error_keeps_its_traceback(tmp_path):
    store = tmp_path / "store"
    manifest = store / "raw" / "eodi" / "2026" / "01" / "manifest.jsonl"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("{bad\n")

    result = run_command(
        ["rebuild", "eodi"],
        {"TRACKER_STORE": "local", "TRACKER_STORE_DIR": str(store)},
    )

    assert result.returncode == 1
    assert "Traceback" in result.stderr
    assert "JSONDecodeError" in result.stderr


INTERNAL_CHECKS = {
    # Fails only through a bug: every caller passes a native id or a key.
    ("schema.py", "make_event_id"),
    # Fails only through a bug: the family comes from the SOURCES registry.
    ("build.py", "load_clean"),
}


def bare_value_errors():
    source = pathlib.Path(tracker.__file__).parent
    found = set()
    for path in sorted(source.glob("*.py")):
        tree = ast.parse(path.read_text())
        for function in ast.walk(tree):
            if not isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for node in ast.walk(function):
                if isinstance(node, ast.Raise) and node.exc is not None:
                    call = node.exc
                    target = call.func if isinstance(call, ast.Call) else call
                    if isinstance(target, ast.Name) and target.id == "ValueError":
                        found.add((path.name, function.name))
    return found


def test_only_the_internal_checks_raise_a_bare_value_error():
    assert bare_value_errors() == INTERNAL_CHECKS


def test_publish_runs_one_family_with_the_build_folder(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        cli.publish, "run", lambda family, folder: calls.append((family, folder))
    )

    cli.main(["publish", "nc", "--build", str(tmp_path)])

    assert calls == [("nc", tmp_path)]
