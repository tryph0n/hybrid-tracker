"""The dashboard with no network: it starts on the bundled snapshot."""

import json

import pytest
import streamlit as st
from conftest import APP
from streamlit.testing.v1 import AppTest

from tracker import hub
from tracker.sources import read_all_events

pytestmark = [
    pytest.mark.allow_hosts(["127.0.0.1"]),
]


class RevisionNotFound(Exception):
    """Stands for the Hub answer "Invalid rev id"."""


def refuse_connection(revision):
    raise ConnectionError("no network")


def refuse_revision(revision):
    raise RevisionNotFound("Invalid rev id: " + revision)


SNAPSHOT_REVISION = "64e176b0000000000000000000000000000000aa"


@pytest.fixture
def snapshot(tmp_path, monkeypatch):
    """A snapshot folder of the stored events, and the app set to read it."""
    folder = tmp_path / "snapshot"
    folder.mkdir()
    events = read_all_events()
    events.to_parquet(folder / hub.EVENTS_FILE)
    (folder / hub.SNAPSHOT_INFO).write_text(
        json.dumps({"revision": SNAPSHOT_REVISION, "date": "2026-11-20"}),
        encoding="utf-8",
    )
    monkeypatch.setenv(hub.SNAPSHOT_VARIABLE, str(folder))
    st.cache_data.clear()
    return events


def test_the_app_starts_on_the_snapshot_and_shows_the_notice(snapshot, monkeypatch):
    monkeypatch.setattr(hub, "fetch", refuse_connection)
    at = AppTest.from_file(str(APP), default_timeout=30)
    at.run()
    assert not at.exception, [error.value for error in at.exception]
    footer = at.caption[-1].value
    assert "The Hub could not be reached" in footer
    assert "could not be read" not in footer
    assert "snapshot" in footer
    assert f"revision {SNAPSHOT_REVISION[:7]} of 2026-11-20" in footer
    shown = snapshot[snapshot["event_status"] != "dismissed"]
    assert {metric.label: metric.value for metric in at.metric}["Events"] == (
        f"{len(shown):,}"
    )


def test_an_unknown_revision_says_the_data_could_not_be_read(snapshot, monkeypatch):
    monkeypatch.setenv(hub.REVISION_VARIABLE, "nosuchtag")
    monkeypatch.setattr(hub, "fetch", refuse_revision)
    at = AppTest.from_file(str(APP), default_timeout=30)
    at.run()
    assert not at.exception, [error.value for error in at.exception]
    footer = at.caption[-1].value
    assert "The data could not be read at revision nosuchtag" in footer
    assert "could not be reached" not in footer
    assert "snapshot bundled with the app" in footer
    assert f"revision {SNAPSHOT_REVISION[:7]} of 2026-11-20" in footer
