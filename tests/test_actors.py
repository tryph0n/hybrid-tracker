"""The threat-group lookup, on a recorded cut of the MISP galaxy cluster."""

import hashlib
import io
import json
import urllib.request

import pandas as pd
import pytest
from conftest import FIXTURES, ROOT

from tracker import actors
from tracker.errors import UserError

pytestmark = pytest.mark.disable_socket

CLUSTER = (FIXTURES / "misp_threat_actor.json").read_bytes()
CURATION = ROOT / "curation" / "actors.csv"


@pytest.fixture(scope="module")
def table() -> pd.DataFrame:
    return actors.reduce(CLUSTER, actors.read_curation(CURATION))


@pytest.fixture(scope="module")
def lookup(table) -> actors.Lookup:
    return actors.Lookup(table)


def cluster(*entries: dict) -> bytes:
    return json.dumps({"values": list(entries)}).encode()


def entry(name: str, **meta) -> dict:
    return {"value": name, "uuid": f"uuid-{name}", "meta": meta}


@pytest.mark.parametrize(
    ("name", "group", "state"),
    [
        ("APT28", "APT28", "RU"),
        ("Fancy Bear", "APT28", "RU"),
        ("Lazarus Group", "Lazarus Group", "KP"),
        ("Hidden Cobra", "Lazarus Group", "KP"),
        ("APT41", "APT41", "CN"),
        ("WICKED PANDA", "APT41", "CN"),
    ],
)
def test_a_group_and_its_aliases_resolve_to_one_group_and_its_state(
    lookup, name, group, state
):
    assert lookup.resolve(name) == group
    assert lookup.state(name) == state


def test_the_match_ignores_case_and_extra_spaces(lookup):
    assert lookup.resolve("fancy bear") == "APT28"
    assert lookup.resolve("FANCY BEAR") == "APT28"
    assert lookup.resolve("  Fancy   Bear ") == "APT28"
    assert lookup.resolve("hidden cobra") == "Lazarus Group"


def test_a_shared_alias_resolves_to_nothing(lookup):
    assert lookup.groups("iridium") == {"IRIDIUM", "Sandworm"}
    assert lookup.resolve("iridium") is None
    assert lookup.resolve("IRIDIUM") is None
    assert lookup.state("iridium") is None
    assert lookup.resolve("Sandworm") == "Sandworm"
    assert lookup.state("Sandworm") == "RU"


def test_an_unknown_name_resolves_to_nothing(lookup):
    assert lookup.resolve("Not A Group") is None
    assert lookup.state("Not A Group") is None


def test_a_shared_alias_stays_unresolved_when_the_two_groups_differ_in_case():
    shared = actors.reduce(
        cluster(
            entry("Alpha", country="RU", synonyms=["Twin"]),
            entry("Beta", country="IR", synonyms=["TWIN"]),
        ),
        [],
    )

    assert actors.Lookup(shared).resolve("twin") is None


def test_the_table_has_no_attribution_column(table):
    assert list(table.columns) == actors.COLUMNS
    assert "attribution" not in table.columns


def test_the_aliases_leave_out_the_group_name_and_repeats():
    table = actors.reduce(
        cluster(entry("Alpha", synonyms=["ALPHA", "Beta", "beta", " ", "Gamma"])), []
    )

    assert list(table["aliases"].iloc[0]) == ["Beta", "Gamma"]


def test_the_reduction_makes_no_network_call(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("network call")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)

    table = actors.reduce(CLUSTER, actors.read_curation(CURATION))

    assert len(table) == 7
    assert actors.Lookup(table).state("Fancy Bear") == "RU"


def test_the_table_is_sorted_and_the_same_for_the_same_file(table):
    again = actors.reduce(CLUSTER, actors.read_curation(CURATION))

    assert list(table["actor"]) == sorted(table["actor"])
    pd.testing.assert_frame_equal(table, again)


def test_an_empty_country_is_no_country():
    table = actors.reduce(cluster(entry("Alpha", country="")), [])

    assert table["country"].isna().all()
    assert table["country_source"].isna().all()


def test_the_state_sponsor_is_an_alpha2_code(table):
    rows = table.set_index("actor")

    assert rows.loc["APT41", "state_sponsor"] == "CN"
    assert rows.loc["Lazarus Group", "state_sponsor"] == "KP"
    assert rows.loc["APT28", "state_sponsor"] == "RU"
    assert pd.isna(rows.loc["IRIDIUM", "state_sponsor"])


def test_an_unknown_sponsor_spelling_stops_the_reduction():
    data = cluster(entry("Alpha", **{"cfr-suspected-state-sponsor": "Ruritania"}))

    with pytest.raises(UserError, match="Ruritania"):
        actors.reduce(data, [])


def test_an_unknown_sponsor_is_no_sponsor():
    table = actors.reduce(
        cluster(entry("Alpha", **{"cfr-suspected-state-sponsor": "Unknown"})), []
    )

    assert table["state_sponsor"].isna().all()


def test_a_group_with_no_country_proposes_its_sponsor():
    table = actors.reduce(
        cluster(
            entry("Alpha", **{"cfr-suspected-state-sponsor": "Russian Federation"})
        ),
        [],
    )

    assert actors.Lookup(table).state("alpha") == "RU"


def test_a_country_that_is_not_alpha2_stops_the_reduction():
    with pytest.raises(UserError, match="alpha-2"):
        actors.reduce(cluster(entry("Alpha", country="Russia")), [])


def test_the_cluster_must_have_unique_group_names():
    with pytest.raises(UserError, match="unique"):
        actors.reduce(cluster(entry("Alpha"), entry("Alpha")), [])


HEADER = ",".join(actors.CURATION_COLUMNS)
FULL = (
    "RU,https://example.org/a,Alpha News,based in Russia,"
    "https://example.org/b,Beta Press,pro-Russian group"
)


def example_line(**changes):
    fields = {
        "actor": "Example Front",
        "country": "RU",
        "country_url": "https://example.org/a",
        "country_publisher": "Alpha News",
        "country_quote": "based in Russia",
        "stance_url": "https://example.org/b",
        "stance_publisher": "Beta Press",
        "stance_quote": "pro-Russian group",
    }
    return actors.Curation(**{**fields, **changes})


def test_the_curation_file_gives_two_sources_and_the_exact_words_to_each_country():
    lines = {line.actor: line for line in actors.read_curation(CURATION)}

    assert set(lines) == {"NoName057(16)", "Killnet"}
    for line in lines.values():
        assert line.country == "RU"
        assert all(line.sources().values())
        assert line.country_url.startswith("https://")
        assert line.stance_url.startswith("https://")
    assert lines["Killnet"].country_quote == "likely based in Russia"
    assert lines["Killnet"].country_publisher == "Flashpoint"
    assert lines["Killnet"].stance_quote == "pro-Russia hacktivist collective"
    assert lines["Killnet"].stance_publisher == "Mandiant"
    assert lines["NoName057(16)"].country_quote == (
        "including against the main instigators living in the Russian Federation"
    )
    assert lines["NoName057(16)"].country_publisher == "Eurojust"
    assert lines["NoName057(16)"].stance_quote == "pro-Russian cybercrime network"
    assert lines["NoName057(16)"].stance_publisher == "Europol"


def test_a_curated_row_has_its_sources_and_a_cluster_row_has_none(table, lookup):
    rows = table.set_index("actor")
    source_columns = [
        name for name in actors.COLUMNS if name.startswith(("country_", "stance_"))
    ]

    for name in ("NoName057(16)", "Killnet"):
        assert rows.loc[name, "country"] == "RU"
        assert rows.loc[name, "country_source"] == actors.FROM_CURATION
        assert rows.loc[name, source_columns].notna().all()
    assert rows.loc["Killnet", "country_quote"] == "likely based in Russia"
    cluster = rows[rows["country_source"] == actors.FROM_CLUSTER]
    assert len(cluster) > 0
    assert (
        cluster[[c for c in source_columns if c != "country_source"]].isna().all().all()
    )
    assert lookup.state("NoName05716") == "RU"
    assert lookup.state("killnet") == "RU"


def test_the_curation_adds_a_group_that_the_cluster_lacks():
    table = actors.reduce(cluster(entry("Alpha", country="IR")), [example_line()])

    rows = table.set_index("actor")
    assert rows.loc["Example Front", "country"] == "RU"
    assert rows.loc["Example Front", "country_quote"] == "based in Russia"
    assert list(rows.loc["Example Front", "aliases"]) == []
    assert rows.loc["Alpha", "country"] == "IR"


def test_the_curation_does_not_change_a_country_that_the_cluster_gives():
    with pytest.raises(UserError, match="already gives"):
        actors.reduce(
            cluster(entry("Alpha", country="IR")), [example_line(actor="Alpha")]
        )


@pytest.mark.parametrize(
    "field",
    [
        "country_url",
        "country_publisher",
        "country_quote",
        "stance_url",
        "stance_publisher",
        "stance_quote",
    ],
)
def test_a_curated_country_without_one_source_field_stops_the_reduction(field):
    with pytest.raises(UserError, match=field):
        actors.reduce(cluster(entry("Alpha")), [example_line(**{field: None})])


def test_a_curated_source_url_must_be_http():
    with pytest.raises(UserError, match="http"):
        actors.reduce(cluster(entry("Alpha")), [example_line(country_url="not a url")])


def test_a_source_field_without_a_country_stops_the_reduction():
    line = actors.Curation("Alpha", stance_quote="pro-Russian group")

    with pytest.raises(UserError, match="stance_quote"):
        actors.reduce(cluster(entry("Alpha")), [line])


@pytest.mark.parametrize(
    "row",
    [
        f",{FULL}",
        "Alpha,Russia,https://example.org/a,A,q,https://example.org/b,B,q",
        "Alpha,ru,https://example.org/a,A,q,https://example.org/b,B,q",
        "Alpha,RU,,A,q,https://example.org/b,B,q",
        "Alpha,RU,https://example.org/a,A,,https://example.org/b,B,q",
        "Alpha,RU,https://example.org/a,A,q,,B,q",
        "Alpha,RU,https://example.org/a,A,q,https://example.org/b,B,",
        "Alpha,,https://example.org/a,,,,,",
    ],
)
def test_a_curation_line_needs_an_actor_a_valid_country_and_its_sources(tmp_path, row):
    path = tmp_path / "actors.csv"
    path.write_text(f"{HEADER}\n{row}\n", encoding="utf-8")

    with pytest.raises(UserError):
        actors.read_curation(path)


def test_the_old_curation_columns_are_refused(tmp_path):
    path = tmp_path / "actors.csv"
    path.write_text("actor,country,source_url\n", encoding="utf-8")

    with pytest.raises(UserError, match="columns"):
        actors.read_curation(path)


def test_a_curation_line_without_country_keeps_the_group_with_no_country(tmp_path):
    path = tmp_path / "actors.csv"
    path.write_text(f"{HEADER}\nAlpha,,,,,,,\n", encoding="utf-8")

    lines = actors.read_curation(path)
    table = actors.reduce(cluster(entry("Beta")), lines)

    rows = table.set_index("actor")
    assert list(rows.index) == ["Alpha", "Beta"]
    assert pd.isna(rows.loc["Alpha", "country"])
    assert pd.isna(rows.loc["Alpha", "country_source"])


def test_a_group_listed_twice_in_the_curation_is_refused(tmp_path):
    path = tmp_path / "actors.csv"
    path.write_text(f"{HEADER}\nAlpha,{FULL}\nALPHA,{FULL}\n", encoding="utf-8")

    with pytest.raises(UserError, match="twice"):
        actors.read_curation(path)


class Response(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_the_download_is_the_pinned_url_and_its_digest_is_checked(monkeypatch):
    asked = []

    def serve(request, timeout):
        asked.append(request.full_url)
        return Response(b"changed")

    monkeypatch.setattr(urllib.request, "urlopen", serve)

    with pytest.raises(UserError, match="sha256"):
        actors.extract()
    assert asked == [actors.URL]
    assert actors.COMMIT in actors.URL


def test_a_download_with_the_pinned_digest_is_kept_as_served(monkeypatch):
    monkeypatch.setattr(actors, "SHA256", hashlib.sha256(CLUSTER).hexdigest())
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda request, timeout: Response(CLUSTER)
    )

    (payload,) = actors.extract()

    assert payload.content == CLUSTER
    assert (payload.source, payload.name) == (actors.SOURCE, actors.FILE_NAME)
