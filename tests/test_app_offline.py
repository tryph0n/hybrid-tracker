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


def test_the_app_starts_on_the_snapshot_and_shows_the_notice(snapshot):
    at = AppTest.from_file(str(APP), default_timeout=30)
    at.run()
    assert not at.exception, [error.value for error in at.exception]
    footer = at.caption[-1].value
    assert "could not be reached" in footer
    assert "snapshot" in footer
    assert f"revision {SNAPSHOT_REVISION[:7]} of 2026-11-20" in footer
    shown = snapshot[snapshot["event_status"] != "dismissed"]
    assert {metric.label: metric.value for metric in at.metric}["Events"] == (
        f"{len(shown):,}"
    )
