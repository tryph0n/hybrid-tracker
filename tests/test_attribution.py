"""CC BY duty: every source is credited where the data is distributed.

That is the README and the two generated dataset cards.
"""

from dataclasses import replace
from pathlib import Path

import pytest
import yaml
from conftest import ROOT

from tracker.build import family_sources
from tracker.errors import UserError
from tracker.publish import LICENCES, render_card, versions_path
from tracker.schema import SCHEMA_VERSION
from tracker.sources import FAMILIES, REFERENCES, SOURCES

FILES = {
    "events.parquet",
    "sources.parquet",
    "quality.parquet",
    "threat_actors.parquet",
}
NC_CITATION = "Example Institute, a NonCommercial register of incidents."


def one_line(path: Path) -> str:
    """The file text with every run of whitespace folded, so wrapped lines match."""
    return " ".join(path.read_text(encoding="utf-8").split())


def test_readme_credits_every_source():
    text = one_line(ROOT / "README.md")
    for source in SOURCES.values():
        assert source.citation in text
        assert source.homepage in text
        assert source.licence in text


def test_readme_credits_every_reference_dataset_with_its_licence_page():
    text = one_line(ROOT / "README.md")
    for reference in REFERENCES.values():
        assert reference.citation in text
        assert reference.homepage in text
        assert reference.licence in text
        assert reference.licence_url in text


@pytest.fixture
def nc_source(monkeypatch):
    """A registry with one extra source whose licence is NonCommercial."""
    source = replace(
        SOURCES["eodi"],
        title="Example NonCommercial register",
        homepage="https://example.org/nc-register",
        licence="CC BY-NC 4.0",
        family="nc",
        citation=NC_CITATION,
    )
    monkeypatch.setitem(SOURCES, "example_nc", source)
    return source


def split(card: str) -> tuple[dict, str]:
    """The front matter of a card as a dict, and its body on one line."""
    _, front_matter, body = card.split("---\n", 2)
    return yaml.safe_load(front_matter), " ".join(body.split())


def card_problems(family: str, card: str) -> list[str]:
    """What the card of `family` gets wrong about credits.

    A source that the family holds must be credited with its citation and its
    homepage. A source that the family does not hold must not appear.
    """
    _, text = split(card)
    held = family_sources(family)
    problems = []
    for name, source in SOURCES.items():
        credited = source.citation in text and source.homepage in text
        if name in held and not credited:
            problems.append(f"{name} is missing")
        if name not in held and (source.citation in text or source.homepage in text):
            problems.append(f"{name} must be absent")
    for reference in REFERENCES.values():
        if (family == "nc" or reference.family == family) and not (
            reference.citation in text and reference.homepage in text
        ):
            problems.append(f"{reference.title} is missing")
    return problems


@pytest.mark.parametrize("family", FAMILIES)
def test_each_card_credits_every_source_and_reference_dataset_it_must(family):
    card = render_card(family, FILES)

    assert card_problems(family, card) == []
    assert split(card)[0]["license"] == LICENCES[family]


def test_an_open_source_is_credited_in_both_cards(nc_source):
    for family in FAMILIES:
        text = split(render_card(family, FILES))[1]
        for source in SOURCES.values():
            if source.family == "open":
                assert source.citation in text
                assert source.homepage in text


def test_an_nc_source_is_in_the_nc_card_only(nc_source):
    open_card = render_card("open", FILES)
    nc_card = render_card("nc", FILES)

    assert NC_CITATION not in open_card
    assert nc_source.homepage not in open_card
    assert NC_CITATION in split(nc_card)[1]
    assert card_problems("open", open_card) == []
    assert card_problems("nc", nc_card) == []
    assert split(nc_card)[0]["license"] == "cc-by-nc-4.0"


def test_the_check_fails_when_a_source_is_missing_from_a_card_where_it_must_be(
    nc_source,
):
    card = render_card("nc", FILES)

    assert card_problems("nc", card.replace(NC_CITATION, "")) == [
        "example_nc is missing"
    ]
    first = SOURCES["eodi"].citation
    assert card_problems("nc", card.replace(first, "")) == ["eodi is missing"]
    assert card_problems("open", render_card("open", FILES).replace(first, "")) == [
        "eodi is missing"
    ]


def test_the_check_fails_when_an_nc_source_is_in_the_open_card(nc_source):
    card = render_card("nc", FILES)

    assert card_problems("open", card) == ["example_nc must be absent"]


@pytest.mark.parametrize("family", FAMILIES)
def test_each_generated_card_has_a_versions_section(family):
    card = render_card(family, FILES)

    assert "\n## Versions\n" in card


@pytest.mark.parametrize("family", FAMILIES)
def test_a_card_without_a_versions_section_is_refused(family):
    with pytest.raises(UserError, match="Versions"):
        render_card(family, FILES, versions="Some text with no heading.\n")


def test_the_open_versions_say_that_old_tags_hold_csv_files():
    text = one_line(versions_path("open"))

    assert "no `events.parquet`" in text
    assert "`v1.0.0`" in text
    assert "`v2.0.0`" in text


def test_the_nc_versions_list_no_v1():
    text = versions_path("nc").read_text(encoding="utf-8")

    assert "### 1.0.0" not in text
    assert "### 2.1.0" in text


def test_the_configs_declare_only_the_files_that_exist():
    front_matter, _ = split(render_card("open", {"events.parquet", "sources.parquet"}))

    configs = front_matter["configs"]
    assert [config["config_name"] for config in configs] == ["events", "sources"]
    assert [config.get("default") for config in configs] == [True, None]
    assert configs[0]["data_files"] == [{"split": "train", "path": "events.parquet"}]
    with_quality = split(render_card("open", FILES - {"threat_actors.parquet"}))[0][
        "configs"
    ]
    assert [config["config_name"] for config in with_quality] == [
        "events",
        "sources",
        "quality",
    ]


def test_the_open_card_declares_threat_actors_and_credits_the_misp_galaxy():
    front_matter, text = split(render_card("open", FILES))

    assert "threat_actors" in [c["config_name"] for c in front_matter["configs"]]
    assert "MISP galaxy (MISP Project / CIRCL)" in text
    assert REFERENCES["misp_galaxy"].citation in text
    assert "CC0 1.0" in text


def test_a_card_without_the_threat_actors_file_has_no_such_config():
    front_matter, _ = split(render_card("open", FILES - {"threat_actors.parquet"}))

    assert "threat_actors" not in [c["config_name"] for c in front_matter["configs"]]


def test_the_open_card_lists_the_curated_countries_with_their_reserve_and_links():
    _, text = split(render_card("open", FILES))

    assert "## Curated countries" in text
    assert "not from the MISP galaxy" in text
    assert (
        '| Killnet | RU | "likely based in Russia" | '
        "[Flashpoint](https://www.flashpoint.io/blog/killnet), "
        "[Mandiant](https://cloud.google.com/blog/topics/threat-intelligence/"
        "killnet-new-capabilities-older-tactics/) |"
    ) in text
    assert (
        '"including against the main instigators living in the Russian Federation"'
        in text
    )
    assert "[Eurojust](" in text
    assert "[Europol](" in text


def test_a_card_without_the_threat_actors_file_has_no_curated_countries():
    _, text = split(render_card("open", FILES - {"threat_actors.parquet"}))
    _, nc_text = split(render_card("nc", FILES - {"threat_actors.parquet"}))

    assert "Curated countries" not in text
    assert "Curated countries" not in nc_text


def newest_version(family):
    text = versions_path(family).read_text(encoding="utf-8")
    headings = [
        line[4:].strip() for line in text.splitlines() if line.startswith("### ")
    ]
    return headings[0]


def version_section(family, version):
    text = versions_path(family).read_text(encoding="utf-8")
    _, _, rest = text.partition(f"### {version}\n")
    return rest.split("\n### ")[0]


@pytest.mark.parametrize("family", FAMILIES)
def test_the_newest_versions_section_of_each_card_is_the_schema_version(family):
    assert newest_version(family) == SCHEMA_VERSION


@pytest.mark.parametrize("family", FAMILIES)
def test_the_version_section_of_each_card_names_the_new_quality_column(family):
    section = version_section(family, SCHEMA_VERSION)

    assert "`cause`" in section


@pytest.mark.parametrize("family", FAMILIES)
def test_each_card_keeps_the_entry_of_2_4_0_with_its_two_quality_columns(family):
    section = version_section(family, "2.4.0")

    assert "`downloaded_at`" in section
    assert "`updated_by_source`" in section


def test_the_open_card_keeps_the_threat_actors_entry_of_2_3_0():
    assert "`threat_actors`" in version_section("open", "2.3.0")


def test_the_nc_card_keeps_the_entry_of_2_3_0_with_no_new_table():
    section = version_section("nc", "2.3.0")

    assert "The same data as 2.2.0" in section


def test_the_nc_card_gets_no_threat_actors_config_or_section_from_a_stray_file():
    front_matter, text = split(render_card("nc", FILES))

    assert "threat_actors" not in [c["config_name"] for c in front_matter["configs"]]
    assert "Curated countries" not in text
