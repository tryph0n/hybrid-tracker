"""The `tracker` command: how it ends on a known problem and on a bug."""

import os
import subprocess
import sys

import pytest

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
    assert result.stderr.strip() == "eodi: no clean table, run `tracker ingest eodi`"


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
