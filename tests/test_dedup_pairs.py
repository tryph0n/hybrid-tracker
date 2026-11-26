"""Shape of the labelled de-duplication sample (docs/decisions/0004-deduplication.md).

The tests do not look the identifiers up in the published data: the daily data run
runs the tests, and an upstream change must not block it.
"""

import csv

from conftest import FIXTURES


def read_pairs() -> list[dict[str, str]]:
    with open(FIXTURES / "dedup_pairs.csv", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_sample_has_the_columns_and_at_least_100_pairs():
    with open(FIXTURES / "dedup_pairs.csv", newline="", encoding="utf-8") as f:
        header = next(csv.reader(f))
    assert header == ["event_id_a", "event_id_b", "label", "note"]
    assert len(read_pairs()) >= 100


def test_every_pair_is_labelled_and_explained():
    for row in read_pairs():
        assert row["label"] in {"match", "no_match", "unsure"}, row
        assert row["note"].strip(), row
        if row["label"] == "unsure":
            assert row["note"].startswith("Unsure"), row


def test_pairs_join_two_sources_once():
    pairs = [(row["event_id_a"], row["event_id_b"]) for row in read_pairs()]
    assert len(set(pairs)) == len(pairs)
    for a, b in pairs:
        assert a.startswith("eodi:") and b.startswith("leiden:"), (a, b)


def test_sample_holds_both_labels():
    labels = [row["label"] for row in read_pairs()]
    assert labels.count("match") and labels.count("no_match")
