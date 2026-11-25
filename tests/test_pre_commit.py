import tomllib

import yaml
from conftest import ROOT

RUFF_HOOKS = "https://github.com/astral-sh/ruff-pre-commit"


def ruff_repo():
    config = yaml.safe_load((ROOT / ".pre-commit-config.yaml").read_text("utf-8"))
    return next(repo for repo in config["repos"] if repo["repo"] == RUFF_HOOKS)


def test_hook_ruff_matches_lockfile():
    lock = tomllib.loads((ROOT / "uv.lock").read_text("utf-8"))
    locked = next(pkg["version"] for pkg in lock["package"] if pkg["name"] == "ruff")
    assert ruff_repo()["rev"] == f"v{locked}"


def test_ruff_check_runs_before_format():
    ids = [hook["id"] for hook in ruff_repo()["hooks"]]
    assert ids.index("ruff-check") < ids.index("ruff-format")
