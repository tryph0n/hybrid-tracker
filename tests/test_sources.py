"""The licence record: every registry entry has one, and docs/sources.md lists it."""

from dataclasses import replace

import pytest
from conftest import ROOT

from tracker.sources import FAMILIES, REFERENCES, SOURCES

ENTRIES = {f"source {k}": v for k, v in SOURCES.items()} | {
    f"reference {k}": v for k, v in REFERENCES.items()
}
FIELDS = ("licence", "family", "licence_page", "citation")


def problems(entry) -> list[str]:
    """The licence fields of a registry entry that are empty or invalid."""
    found = [field for field in FIELDS if not getattr(entry, field).strip()]
    if entry.family not in FAMILIES:
        found.append("family")
    return found


def usable_group() -> str:
    """The text of the "Use now" section of docs/sources.md."""
    text = (ROOT / "docs" / "sources.md").read_text(encoding="utf-8")
    return text.split("## Use now\n", 1)[1].split("\n## ", 1)[0]


@pytest.mark.parametrize("entry", ENTRIES.values(), ids=ENTRIES.keys())
def test_entry_records_its_licence(entry):
    assert problems(entry) == []


@pytest.mark.parametrize("field", FIELDS)
def test_empty_field_is_a_problem(field):
    entry = replace(SOURCES["eodi"], **{field: " "})
    assert field in problems(entry)


def test_unknown_family_is_a_problem():
    assert problems(replace(SOURCES["eodi"], family="cc0")) == ["family"]


@pytest.mark.parametrize("entry", ENTRIES.values(), ids=ENTRIES.keys())
def test_entry_is_listed_in_the_use_now_group(entry):
    assert entry.licence_page in usable_group()
