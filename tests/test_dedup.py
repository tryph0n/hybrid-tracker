"""Clusters of rows that two sources record for one incident (record 0004).

The sample tests read `tests/fixtures/dedup_events.csv`, the rows that the
labelled sample names, and the real curation files. They do not read the
published data: the daily data run runs the tests, and an upstream change must
not block it. The build checks the curation files against the data it reads.
"""

from pathlib import Path

import pandas as pd
import pytest
from conftest import FIXTURES, ROOT

from tracker import dedup
from tracker.errors import UserError
from tracker.schema import read_csv
from tracker.sources import SOURCES

ORDER = list(SOURCES)

RULE_PRECISION_FLOOR = 19 / 30
RULE_RECALL_FLOOR = 19 / 22
PRECISION_TARGET = 0.95
RECALL_TARGET = 0.80

DUBLIN = ("eodi:EODI-2025-00077", "leiden:3757f59ed926")

EXPECTED_PARTNER = {
    "leiden:018349739d34": "eodi:EODI-2025-00335",
    "leiden:22b266ae6b3e": None,
    "leiden:2b17621a2c51": "eodi:EODI-2025-00131",
    "leiden:367c14c8ce25": "eodi:EODI-2025-00223",
    "leiden:3757f59ed926": "eodi:EODI-2025-00077",
    "leiden:43ab1de44e6f": None,
    "leiden:55e3134992fd": "eodi:EODI-2025-00112",
    "leiden:56e86190493e": None,
    "leiden:5f5430ef469b": None,
    "leiden:640a53d827f2": None,
    "leiden:90d31393ed34": None,
    "leiden:959eb429143e": None,
    "leiden:9a23b38abc09": None,
    "leiden:a19ba52314ec": None,
    "leiden:ad2cf8c2ff2a": "eodi:EODI-2025-00287",
    "leiden:ad62f71f9a25": None,
    "leiden:bb599689ffeb": "eodi:EODI-2025-00083",
    "leiden:be7d76769768": "eodi:EODI-2025-00459",
    "leiden:c4c174c79ad3": None,
    "leiden:c8e2a1d1d2c5": "eodi:EODI-2025-00069",
    "leiden:d8677c266eca": "eodi:EODI-2025-00314",
    "leiden:eddd659ee167": "eodi:EODI-2025-00015",
    "leiden:ff5a043c53d7": None,
}


@pytest.fixture(scope="module")
def sample_events() -> pd.DataFrame:
    return read_csv(FIXTURES / "dedup_events.csv")


@pytest.fixture(scope="module")
def sample_pairs() -> pd.DataFrame:
    return read_csv(FIXTURES / "dedup_pairs.csv")


@pytest.fixture(scope="module")
def merges() -> list[dedup.Pair]:
    return dedup.read_curation(ROOT / "curation" / "merges.csv")


@pytest.fixture(scope="module")
def splits() -> list[dedup.Pair]:
    return dedup.read_curation(ROOT / "curation" / "splits.csv")


def labels_of(sample_pairs: pd.DataFrame) -> dict[dedup.Pair, str]:
    return {
        (row.event_id_a, row.event_id_b): row.label for row in sample_pairs.itertuples()
    }


def partners(clustered: pd.DataFrame, leiden_ids: list[str]) -> dict[str, str | None]:
    """The EODI row that the cluster of each Leiden row holds, or None."""
    found: dict[str, str | None] = {}
    for leiden_id in leiden_ids:
        cluster = clustered.loc[clustered["event_id"] == leiden_id, "cluster_id"].iloc[
            0
        ]
        rows = clustered[
            (clustered["cluster_id"] == cluster) & (clustered["source"] == "eodi")
        ]
        found[leiden_id] = rows["event_id"].iloc[0] if len(rows) else None
    return found


def leiden_measures(
    partner: dict[str, str | None], labels: dict[dedup.Pair, str]
) -> tuple[float, float]:
    """Precision and recall by Leiden rows, as record 0004 defines them."""
    joined = {leiden: eodi for leiden, eodi in partner.items() if eodi}
    right = [
        pair for pair in joined.items() if labels.get((pair[1], pair[0])) == "match"
    ]
    with_match = {pair[1] for pair, label in labels.items() if label == "match"}
    return len(right) / len(joined), len(right) / len(with_match)


def test_the_sample_events_hold_every_row_that_the_sample_and_curation_name(
    sample_events, sample_pairs, merges, splits
):
    ids = set(sample_events["event_id"])
    for name, pairs in {
        "sample": list(zip(sample_pairs["event_id_a"], sample_pairs["event_id_b"])),
        "merges": merges,
        "splits": splits,
    }.items():
        dedup.check_ids(pairs, ids, name)


def test_a_stale_id_fails_the_check():
    ids = {"eodi:1", "leiden:a"}

    dedup.check_ids([("eodi:1", "leiden:a")], ids, "sample")
    with pytest.raises(UserError, match="sample: unknown event ids.*leiden:b"):
        dedup.check_ids([("eodi:1", "leiden:b")], ids, "sample")


@pytest.mark.parametrize("name", ["merges", "splits"])
def test_a_stale_id_in_a_curation_list_fails_the_clustering(sample_events, name):
    stale = [("eodi:EODI-2025-00077", "leiden:gone")]

    with pytest.raises(UserError, match="unknown event ids"):
        dedup.assign_clusters(sample_events, ORDER, **{name: stale})


def test_the_rule_alone_stays_above_the_floor(sample_events, sample_pairs):
    labels = labels_of(sample_pairs)
    matched = [
        (m.first, m.second)
        for m in dedup.matched_pairs(sample_events)
        if labels.get((m.first, m.second), "unsure") != "unsure"
    ]
    right = [pair for pair in matched if labels[pair] == "match"]
    total = sum(1 for label in labels.values() if label == "match")
    precision, recall = len(right) / len(matched), len(right) / total
    print(f"rule alone, by pairs: precision {precision:.2f}, recall {recall:.2f}")

    assert precision >= RULE_PRECISION_FLOOR - 1e-9
    assert recall >= RULE_RECALL_FLOOR - 1e-9


def test_after_the_curation_the_leiden_rows_reach_the_targets(
    sample_events, sample_pairs, merges, splits
):
    labels = labels_of(sample_pairs)
    clustered = dedup.assign_clusters(sample_events, ORDER, merges, splits)
    leiden_ids = sorted(set(sample_pairs["event_id_b"]))
    assert len(leiden_ids) == 23
    precision, recall = leiden_measures(partners(clustered, leiden_ids), labels)
    print(
        f"after the curation, by Leiden rows: precision {precision:.2f}, "
        f"recall {recall:.2f}"
    )

    assert precision >= PRECISION_TARGET
    assert recall >= RECALL_TARGET


def test_each_leiden_row_has_its_expected_partner(
    sample_events, sample_pairs, merges, splits
):
    clustered = dedup.assign_clusters(sample_events, ORDER, merges, splits)

    found = partners(clustered, sorted(EXPECTED_PARTNER))

    assert found == EXPECTED_PARTNER
    assert set(EXPECTED_PARTNER) == set(sample_pairs["event_id_b"])


def test_a_lost_merge_passes_the_targets_and_fails_the_partners(
    sample_events, sample_pairs, merges, splits
):
    labels = labels_of(sample_pairs)
    without = [pair for pair in merges if pair != DUBLIN]
    assert len(without) == len(merges) - 1
    clustered = dedup.assign_clusters(sample_events, ORDER, without, splits)

    found = partners(clustered, sorted(EXPECTED_PARTNER))
    precision, recall = leiden_measures(found, labels)

    assert precision >= PRECISION_TARGET and recall >= RECALL_TARGET
    assert found != EXPECTED_PARTNER
    assert found["leiden:3757f59ed926"] is None


def test_a_merge_takes_the_leiden_row_before_a_better_scoring_rule_pair(
    sample_events, merges, splits
):
    clustered = dedup.assign_clusters(sample_events, ORDER, merges, splits)
    row = clustered.set_index("event_id")

    assert row.loc["leiden:c8e2a1d1d2c5", "cluster_id"] == "leiden:c8e2a1d1d2c5"
    assert row.loc["eodi:EODI-2025-00069", "cluster_id"] == "leiden:c8e2a1d1d2c5"
    assert row.loc["eodi:EODI-2025-00094", "n_sources"] == 1
    assert row.loc["eodi:EODI-2025-00094", "cluster_id"] == "eodi:EODI-2025-00094"


def test_the_tie_goes_to_the_smaller_event_id(sample_events, merges, splits):
    matches = {(m.first, m.second): m for m in dedup.matched_pairs(sample_events)}
    low = matches[("eodi:EODI-2025-00131", "leiden:2b17621a2c51")]
    high = matches[("eodi:EODI-2025-00445", "leiden:2b17621a2c51")]
    assert low.score == high.score and low.gap == high.gap == 0

    row = dedup.assign_clusters(sample_events, ORDER, merges, splits).set_index(
        "event_id"
    )

    assert row.loc["eodi:EODI-2025-00131", "n_sources"] == 2
    assert row.loc["eodi:EODI-2025-00445", "n_sources"] == 1


def test_the_splits_of_the_record_remove_the_brussels_pairs(sample_events, splits):
    pairs = {(m.first, m.second) for m in dedup.matched_pairs(sample_events)}

    assert set(splits) <= pairs


def event(
    event_id: str,
    source: str,
    date: str = "2025-10-01",
    precision: str = "day",
    countries: str = "DE",
    title: str = "Drones over Kiel airport",
    place: str = "Kiel, Germany",
    lat: float | None = None,
    lon: float | None = None,
    end_date: str | None = None,
    geo_precision: str = "place",
) -> dict[str, object]:
    return {
        "event_id": event_id,
        "source": source,
        "date": date,
        "end_date": end_date,
        "date_precision": precision,
        "countries": countries,
        "place": place,
        "lat": lat,
        "lon": lon,
        "geo_precision": geo_precision,
        "title": title,
    }


def frame(*rows: dict[str, object]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def clusters_of(result: pd.DataFrame) -> set[frozenset[str]]:
    return {frozenset(group["event_id"]) for _, group in result.groupby("cluster_id")}


def test_a_month_interval_covers_the_month_and_a_day_margin_on_each_side():
    month = event("b:1", "leiden", "2025-10-01", "month", geo_precision="country")
    day = event("a:1", "eodi", "2025-10-31")
    nearby = event("a:2", "eodi", "2025-11-01")
    far = event("a:3", "eodi", "2025-11-02")
    spans = {
        row["event_id"]: dedup.interval(pd.Series(row))
        for row in (month, day, nearby, far)
    }

    assert spans["b:1"][1].isoformat() == "2025-10-31"
    assert dedup.date_gap(spans["b:1"], spans["a:1"]) == 0
    assert dedup.date_gap(spans["b:1"], spans["a:2"]) == 1
    assert dedup.date_gap(spans["b:1"], spans["a:3"]) == 2


def test_an_end_date_ends_the_interval_with_the_same_precision():
    start, end = dedup.interval(
        pd.Series(event("a:1", "eodi", "2025-01-01", "month", end_date="2025-03-01"))
    )
    assert (start.isoformat(), end.isoformat()) == ("2025-01-01", "2025-03-31")
    start, end = dedup.interval(pd.Series(event("a:1", "eodi", "2024-01-01", "year")))
    assert (start.isoformat(), end.isoformat()) == ("2024-01-01", "2024-12-31")


def test_the_distance_rule_needs_two_coordinates_under_25_km():
    near = frame(
        event("a:1", "eodi", lat=54.32, lon=10.13, title="Alpha"),
        event("b:1", "leiden", lat=54.40, lon=10.20, title="Zulu", place="X"),
    )
    far = frame(
        event("a:1", "eodi", lat=54.32, lon=10.13, title="Alpha"),
        event("b:1", "leiden", lat=54.80, lon=10.20, title="Zulu", place="X"),
    )

    assert len(dedup.matched_pairs(near)) == 1
    assert dedup.matched_pairs(far) == []


def test_the_place_rule_accepts_a_spelling_variant_and_needs_a_similar_title():
    base = event("a:1", "eodi", place="Munchen, Germany", title="Drone over airport")
    same = event(
        "b:1",
        "leiden",
        place="Germany",
        geo_precision="country",
        title="Drone sightings at Munich airport",
    )
    other = event(
        "b:1",
        "leiden",
        place="Germany",
        geo_precision="country",
        title="Cyber attack on a hospital in Hamburg",
    )

    assert len(dedup.matched_pairs(frame(base, same))) == 1
    assert dedup.matched_pairs(frame(base, other)) == []


def test_a_place_of_country_names_only_is_never_similar():
    first = event(
        "a:1",
        "eodi",
        place="Germany",
        geo_precision="country",
        title="Drones over Germany",
    )
    second = event(
        "b:1",
        "leiden",
        place="Germany",
        geo_precision="country",
        title="Drones over Germany",
    )

    assert dedup.matched_pairs(frame(first, second)) == []


def test_two_rows_of_one_source_never_join():
    rows = frame(
        event("a:1", "eodi"),
        event("a:2", "eodi"),
        event("b:1", "leiden", title="Drones over Kiel airport"),
    )

    result = dedup.assign_clusters(rows, ["eodi", "leiden"])

    assert clusters_of(result) == {frozenset({"a:1", "b:1"}), frozenset({"a:2"})}
    assert result["is_canonical"].tolist() == [True, True, False]
    assert result["n_sources"].tolist() == [2, 1, 2]


def test_a_merge_joins_rows_that_the_rule_does_not_and_a_split_keeps_them_apart():
    rows = frame(
        event("a:1", "eodi", title="Alpha", place="Kiel, Germany"),
        event(
            "b:1", "leiden", title="Zulu zulu", place="Germany", geo_precision="country"
        ),
        event("b:2", "leiden", title="Drones over Kiel airport"),
        event("a:2", "eodi", title="Drones over Kiel airport"),
    )

    plain = dedup.assign_clusters(rows, ["eodi", "leiden"])
    merged = dedup.assign_clusters(rows, ["eodi", "leiden"], merges=[("a:1", "b:1")])
    split = dedup.assign_clusters(rows, ["eodi", "leiden"], splits=[("a:2", "b:2")])

    assert frozenset({"a:1", "b:1"}) not in clusters_of(plain)
    assert frozenset({"a:2", "b:2"}) in clusters_of(plain)
    assert frozenset({"a:1", "b:1"}) in clusters_of(merged)
    assert frozenset({"a:2", "b:2"}) not in clusters_of(split)


def test_a_pair_in_both_curation_lists_or_two_merges_on_one_source_fail():
    rows = frame(event("a:1", "eodi"), event("a:2", "eodi"), event("b:1", "leiden"))

    with pytest.raises(UserError, match="both merges and splits"):
        dedup.assign_clusters(
            rows, ["eodi", "leiden"], merges=[("a:1", "b:1")], splits=[("a:1", "b:1")]
        )
    with pytest.raises(UserError, match="two rows of one source"):
        dedup.assign_clusters(
            rows, ["eodi", "leiden"], merges=[("a:1", "b:1"), ("a:2", "b:1")]
        )


def test_a_split_breaks_a_chain_through_a_third_source():
    order = ["eodi", "leiden", "third"]
    rows = frame(
        event("a:1", "eodi"),
        event("b:1", "leiden"),
        event("c:1", "third"),
    )

    chain = dedup.assign_clusters(rows, order)
    broken = dedup.assign_clusters(rows, order, splits=[("a:1", "c:1")])

    assert clusters_of(chain) == {frozenset({"a:1", "b:1", "c:1"})}
    assert chain["n_sources"].tolist() == [3, 3, 3]
    assert len(clusters_of(broken)) == 2
    assert broken["is_canonical"].sum() == 2


def test_the_cluster_id_stays_when_a_source_higher_in_the_order_joins():
    leiden = event("b:1", "leiden")
    alone = dedup.assign_clusters(frame(leiden), ["eodi", "leiden"])
    joined = dedup.assign_clusters(
        frame(event("a:1", "eodi"), leiden), ["eodi", "leiden"]
    )

    assert alone["cluster_id"].tolist() == ["b:1"]
    assert set(joined["cluster_id"]) == {"b:1"}
    assert joined.set_index("event_id").loc["a:1", "is_canonical"]


def test_read_curation_needs_the_columns_and_a_reason(tmp_path: Path):
    bad_columns = tmp_path / "bad.csv"
    bad_columns.write_text("a,b\nx,y\n", encoding="utf-8")
    no_reason = tmp_path / "none.csv"
    no_reason.write_text("event_id_a,event_id_b,reason\nx,y,\n", encoding="utf-8")

    with pytest.raises(UserError, match="columns must be"):
        dedup.read_curation(bad_columns)
    with pytest.raises(UserError, match="no reason"):
        dedup.read_curation(no_reason)


def test_the_curation_files_hold_the_pairs_of_the_record(merges, splits):
    assert len(merges) == 4 and len(splits) == 2
    assert DUBLIN in merges
