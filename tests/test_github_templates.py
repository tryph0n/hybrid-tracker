from pathlib import Path

import pytest
import yaml

TEMPLATES = Path(__file__).resolve().parents[1] / ".github"
FORMS = sorted((TEMPLATES / "ISSUE_TEMPLATE").glob("*.yml"))
FORMS = [path for path in FORMS if path.name != "config.yml"]
REQUIRED_FIELDS = {
    "data-error.yml": {"source", "event_id", "problem", "evidence"},
    "source-request.yml": {"url", "licence", "access"},
}


def load(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_three_forms():
    assert {path.name for path in FORMS} == {
        "data-error.yml",
        "source-request.yml",
        "bug.yml",
    }


def test_blank_issues_allowed():
    config = load(TEMPLATES / "ISSUE_TEMPLATE" / "config.yml")
    assert config["blank_issues_enabled"] is True


@pytest.mark.parametrize("path", FORMS, ids=lambda path: path.name)
def test_form_schema(path):
    form = load(path)
    assert form["name"]
    assert form["description"]
    ids = [item["id"] for item in form["body"]]
    assert len(ids) == len(set(ids))
    for item in form["body"]:
        assert item["type"] in {"input", "textarea"}
        assert item["attributes"]["label"]


@pytest.mark.parametrize("name", sorted(REQUIRED_FIELDS))
def test_required_fields(name):
    form = load(TEMPLATES / "ISSUE_TEMPLATE" / name)
    required = {
        item["id"]
        for item in form["body"]
        if item.get("validations", {}).get("required") is True
    }
    assert required == REQUIRED_FIELDS[name]


def test_labels():
    labels = {path.name: load(path).get("labels") for path in FORMS}
    assert labels["data-error.yml"] == ["data", "bug"]
    assert labels["bug.yml"] == ["bug"]


def test_pull_request_template_has_checklist():
    text = (TEMPLATES / "pull_request_template.md").read_text(encoding="utf-8")
    assert text.count("- [ ] ") == 7
