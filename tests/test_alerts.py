"""The alert issues: one per source, open while a cause remains."""

import json

import pandas as pd
import pytest
import yaml
from conftest import ROOT

from tracker import alerts, quality
from tracker.errors import UserError

NOW = pd.Timestamp("2026-11-27T04:17:00Z")
RUN = "https://example.org/run/1"
FRESH = NOW - pd.Timedelta(hours=2)
STALE = NOW - pd.Timedelta(days=4)
ROWS = pd.DataFrame({"id": [1]})


class FakeGh:
    """An in-memory issue tracker that answers the `gh` calls of `sync`."""

    def __init__(self):
        self.issues = {}
        self.comments = {}

    def __call__(self, arguments):
        arguments = list(arguments)
        kind = arguments[:2]
        if kind == ["issue", "list"]:
            listed = [
                {"number": number, "title": issue["title"]}
                for number, issue in self.issues.items()
                if issue["open"]
            ]
            return json.dumps(listed)
        if kind == ["issue", "create"]:
            number = len(self.issues) + 1
            title = arguments[arguments.index("--title") + 1]
            self.issues[number] = {"title": title, "open": True}
            self.comments[number] = []
        elif kind[0] == "issue":
            number = int(arguments[2])
            flag = "--body" if kind[1] == "comment" else "--comment"
            self.comments[number].append(arguments[arguments.index(flag) + 1])
            if kind[1] == "close":
                self.issues[number]["open"] = False
        return ""

    def open_titles(self):
        return [i["title"] for i in self.issues.values() if i["open"]]


def run_once(tmp_path, gh, found):
    path = tmp_path / "alerts.json"
    path.write_text(json.dumps(found), encoding="utf-8")
    return [action.kind for action in alerts.sync(path, RUN, gh)]


def eodi_causes(fetched, failure=None):
    found = quality.causes("eodi", ROWS, fetched, failure, NOW)
    return {"eodi": found} if found else {}


def test_full_cycle_of_one_cause(tmp_path):
    gh = FakeGh()
    stale = eodi_causes(STALE)
    assert "no successful fetch" in stale["eodi"][0]
    assert run_once(tmp_path, gh, stale) == ["open"]
    assert gh.open_titles() == ["Source alert: eodi"]
    assert run_once(tmp_path, gh, stale) == ["comment"]
    assert len(gh.issues) == 1
    assert len(gh.comments[1]) == 1
    assert run_once(tmp_path, gh, eodi_causes(FRESH)) == ["close"]
    assert gh.open_titles() == []


def test_two_causes_keep_the_alert_open_until_the_last_ends(tmp_path):
    gh = FakeGh()
    both = eodi_causes(STALE, {"error": "boom"})
    assert len(both["eodi"]) == 2
    assert run_once(tmp_path, gh, both) == ["open"]
    only_failure = eodi_causes(FRESH, {"error": "boom"})
    assert len(only_failure["eodi"]) == 1
    assert run_once(tmp_path, gh, only_failure) == ["comment"]
    assert "refresh failed: boom" in gh.comments[1][-1]
    assert gh.open_titles() == ["Source alert: eodi"]
    assert run_once(tmp_path, gh, eodi_causes(FRESH)) == ["close"]
    assert gh.open_titles() == []


def test_each_source_has_its_own_issue_and_closes_alone():
    open_issues = [
        {"number": 4, "title": "Source alert: eodi"},
        {"number": 5, "title": "Source alert: leiden"},
        {"number": 6, "title": "Another issue"},
    ]
    done = alerts.plan({"leiden": ["no row in the clean table"]}, open_issues)
    assert [(a.kind, a.source, a.number) for a in done] == [
        ("comment", "leiden", 5),
        ("close", "eodi", 4),
    ]


def test_the_alert_job_can_write_issues_and_close_failure_leaves_alerts_open():
    workflow = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "pipeline.yml").read_text(encoding="utf-8")
    )["jobs"]
    assert workflow["source-alerts"]["permissions"]["issues"] == "write"
    closing = yaml.dump(workflow["close-failure"]["steps"])
    assert "pipeline-failure" in closing
    assert alerts.LABEL not in closing


def test_every_alert_and_failure_job_waits_for_the_source_alerts_job():
    jobs = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "pipeline.yml").read_text(encoding="utf-8")
    )["jobs"]
    for name in ("report-failure", "close-failure", "heartbeat"):
        assert "source-alerts" in jobs[name]["needs"], name


def test_the_alert_job_runs_on_main_only():
    jobs = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "pipeline.yml").read_text(encoding="utf-8")
    )["jobs"]
    assert jobs["source-alerts"]["if"].startswith("github.ref == 'refs/heads/main' &&")
    assert jobs["source-alerts"]["if"] == jobs["publish"]["if"]


def test_a_missing_alerts_file_is_a_user_error_that_names_the_file(tmp_path):
    missing = tmp_path / "alerts.json"
    with pytest.raises(UserError, match="alerts.json"):
        alerts.sync(missing, "https://run", lambda args: "[]")
