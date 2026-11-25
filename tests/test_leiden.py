"""The Leiden adapter on a hand-written sample of the EventData sheet."""

import json
import re
from datetime import datetime

import openpyxl
import pandas as pd
import pytest
from conftest import FIXTURES

from tracker import leiden
from tracker.schema import validate

SAMPLE = FIXTURES / "leiden_eventdata.csv"
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
LINK = "https://example.org/report"


def event_data(
    tmp_path, raw: pd.DataFrame, links: dict[int, str] | None = None
) -> pd.DataFrame:
    """Write `raw` as an xlsx file with hyperlinks, and read it as `fetch` does.

    Cells that look like a date become real dates, as in the download. `links`
    maps a row position to the hyperlink target of its "What" cell.
    """
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "EventData"
    sheet.append(list(raw.columns))
    for position, row in enumerate(raw.itertuples(index=False)):
        sheet.append(
            [
                datetime.strptime(cell, "%Y-%m-%d")
                if isinstance(cell, str) and DATE.fullmatch(cell)
                else None
                if pd.isna(cell)
                else cell
                for cell in row
            ]
        )
        if links and position in links:
            sheet.cell(
                row=position + 2, column=raw.columns.get_loc("What") + 1
            ).hyperlink = links[position]
    path = tmp_path / "RussianOps.xlsx"
    book.save(path)
    return leiden.read_workbook(str(path))


@pytest.fixture
def events(tmp_path) -> pd.DataFrame:
    return leiden.normalize(event_data(tmp_path, pd.read_csv(SAMPLE), {1: LINK}))


def test_normalize_follows_schema(events):
    validate(events)
    # The three note rows below the events are dropped.
    assert len(events) == 7
    unknown_month = events["event_id"] == "leiden:ed264516da77"
    assert (events.loc[unknown_month, "date_precision"] == "year").all()
    assert (events.loc[~unknown_month, "date_precision"] == "month").all()
    assert (events["event_status"] == "confirmed").all()
    assert (events["actors"] == "RU").all()
    assert (events["attribution"] == "assessed").all()
    assert (events["attributed_by"] == "Leiden University dataset (B. Schuurman)").all()
    assert events[["lat", "lon"]].isna().all().all()


@pytest.mark.parametrize(
    ("event_id", "expected"),
    [
        (
            "leiden:a6a0e7c45896",
            {
                "date": "2022-01-01",
                "end_date": "2022-12-31",
                "countries": "",
                "geo_precision": "sea",
                "domain": "espionage",
                "category": "reconnaissance",
                "url": leiden.DATASET_URL,
            },
        ),
        (
            "leiden:1dd47a0d91c9",
            {
                "date": "2022-03-01",
                "end_date": "2022-08-30",
                "countries": "AT;DE",
                "place": "Austria; Germany",
                "geo_precision": "country",
                "domain": "information",
                "category": "influence_operation",
                "subcategory": "Influence operation",
                "url": LINK,
            },
        ),
        (
            "leiden:3b7aef22c6d4",
            {
                "end_date": "",
                "countries": "EE",
                "domain": "information",
                "category": "symbolic_vandalism",
            },
        ),
        (
            "leiden:ed264516da77",
            {
                "date": "2023-01-01",
                "date_precision": "year",
                "countries": "NL",
                "domain": "sabotage",
                "category": "sabotage",
            },
        ),
        (
            "leiden:9758c68a3b4d",
            {"countries": "DE", "domain": "sabotage", "category": "arson"},
        ),
        (
            "leiden:7b41580d6722",
            {
                "countries": "CZ;GB;DE",
                "domain": "other",
                "category": "instrumentalised_migration",
            },
        ),
        (
            "leiden:c8c12cff9e70",
            {
                "date": "2025-12-01",
                "end_date": "2025-12-20",
                "countries": "PL;DE",
                "geo_precision": "country",
                "domain": "sabotage",
                "category": "sabotage",
                "subcategory": "Sabotage (preparation)",
            },
        ),
    ],
)
def test_normalize_maps_values(events, event_id, expected):
    row = events.set_index("event_id").loc[event_id]
    assert row[list(expected)].fillna("").to_dict() == expected


@pytest.mark.parametrize(
    ("label", "domain", "category"),
    [
        ("Sabotage", "sabotage", "sabotage"),
        ("Sabotage (preparation)", "sabotage", "sabotage"),
        ("Sabotage (attempt)", "sabotage", "sabotage"),
        ("Arson", "sabotage", "arson"),
        ("Terrorism", "violence", "bomb_or_threat"),
        ("Assassination", "violence", "assassination"),
        ("Assassination (plot)", "violence", "assassination"),
        ("Assassination (attempt)", "violence", "assassination"),
        ("Influence operation", "information", "influence_operation"),
        ("Vandalism", "information", "symbolic_vandalism"),
        ("Reconnaissance", "espionage", "reconnaissance"),
        ("Disruption", "air", "airspace_disruption"),
        ("Disruptive immigration", "other", "instrumentalised_migration"),
        ("Unknown", "other", "unclassified"),
    ],
)
def test_every_label_maps_to_a_valid_pair(tmp_path, label, domain, category):
    raw = pd.read_csv(SAMPLE)
    raw.loc[2, "EventCategory"] = label
    events = leiden.normalize(event_data(tmp_path, raw))
    validate(events)
    row = events.iloc[2]
    assert (row["domain"], row["category"], row["subcategory"]) == (
        domain,
        category,
        label,
    )


def test_row_takes_the_what_hyperlink_or_the_dataset_page(events):
    urls = events["url"].tolist()
    assert urls.count(LINK) == 1
    assert urls.count(leiden.DATASET_URL) == 6


@pytest.mark.parametrize("text", ["DNA", "July", "May"])
def test_known_end_texts_give_an_empty_end_date(tmp_path, text):
    raw = pd.read_csv(SAMPLE)
    raw.loc[0, "IncidentDateEnd"] = text
    events = leiden.normalize(event_data(tmp_path, raw))
    validate(events)
    assert events["end_date"].iloc[0] == ""


def test_unknown_end_text_stops_the_ingestion(tmp_path):
    raw = pd.read_csv(SAMPLE)
    raw.loc[0, "IncidentDateEnd"] = "Ongoing"
    with pytest.raises(ValueError, match="IncidentDateEnd"):
        leiden.normalize(event_data(tmp_path, raw))


def test_end_date_before_start_fails_validation(tmp_path):
    raw = pd.read_csv(SAMPLE)
    raw.loc[0, "IncidentDateEnd"] = "2021-01-01"
    with pytest.raises(ValueError, match="end_date before date"):
        validate(leiden.normalize(event_data(tmp_path, raw)))


def test_title_is_stripped(events):
    assert (
        events.set_index("event_id")
        .loc["leiden:9758c68a3b4d", "title"]
        .endswith("sample)")
    )


def test_unknown_country_stops_the_ingestion(tmp_path):
    raw = pd.read_csv(SAMPLE)
    raw.loc[2, "Where"] = "Ruritania"
    with pytest.raises(LookupError):
        leiden.normalize(event_data(tmp_path, raw))


def test_unknown_category_fails_validation(tmp_path):
    raw = pd.read_csv(SAMPLE)
    raw.loc[2, "EventCategory"] = "Cyber attack"
    with pytest.raises(ValueError, match="without domain"):
        validate(leiden.normalize(event_data(tmp_path, raw)))


def test_event_ids_are_hashes(tmp_path):
    ids = leiden.event_ids(
        event_data(tmp_path, pd.read_csv(SAMPLE)).dropna(subset=["IncidentYear"])
    )
    assert len(ids) == 7
    assert ids.str.fullmatch(r"leiden:[0-9a-f]{12}").all()
    assert ids.is_unique


def test_event_ids_survive_shuffle_and_renumbering(tmp_path):
    raw = event_data(tmp_path, pd.read_csv(SAMPLE)).dropna(subset=["IncidentYear"])
    shuffled = raw.sample(frac=1, random_state=1).reset_index(drop=True)
    assert set(leiden.event_ids(shuffled)) == set(leiden.event_ids(raw))


def test_event_ids_survive_a_dropped_row(tmp_path):
    raw = event_data(tmp_path, pd.read_csv(SAMPLE)).dropna(subset=["IncidentYear"])
    kept = raw.drop(raw.index[0]).reset_index(drop=True)
    assert set(leiden.event_ids(kept)) == set(leiden.event_ids(raw)) - {
        leiden.event_ids(raw).iloc[0]
    }


def test_missing_what_gives_the_same_id_as_an_empty_what(tmp_path):
    raw = event_data(tmp_path, pd.read_csv(SAMPLE)).dropna(subset=["IncidentYear"])
    missing = raw.assign(What=None)
    empty = raw.assign(What="")
    assert (leiden.event_ids(missing) == leiden.event_ids(empty)).all()


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_version_answer_of_the_pinned_version_passes():
    leiden.check_version(load("leiden_dataverse_version.json"))


def test_other_licence_stops_the_run():
    answer = load("leiden_dataverse_version.json")
    answer["data"]["license"]["name"] = "CC BY-NC-SA 4.0"
    with pytest.raises(ValueError, match="CC BY-NC-SA 4.0"):
        leiden.check_version(answer)


def test_new_version_stops_the_run_and_names_it():
    answer = load("leiden_dataverse_version.json")
    answer["data"]["versionNumber"] = 5
    with pytest.raises(ValueError, match="Version 5.0"):
        leiden.check_version(answer)


def test_spreadsheet_id_is_read_from_the_file_list():
    assert leiden.spreadsheet_id(load("leiden_dataverse_files.json")) == 13350817


def test_file_list_without_one_spreadsheet_stops_the_run():
    answer = load("leiden_dataverse_files.json")
    answer["data"][0]["dataFile"]["filename"] = "notes.pdf"
    with pytest.raises(ValueError, match="one .xlsx"):
        leiden.spreadsheet_id(answer)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://a.org/p?utm_source=x.com", "https://a.org/p"),
        ("https://a.org/p?id=3&utm_medium=m&utm_source=x.com", "https://a.org/p?id=3"),
        (" https://a.org/p?id=3 ", "https://a.org/p?id=3"),
        ("https://a.org/p?q=a%20b+c&z=1&a=2", "https://a.org/p?q=a%20b+c&z=1&a=2"),
        ("https://a.org/p?", "https://a.org/p?"),
        ("", ""),
    ],
)
def test_tracking_parameters_are_removed_from_links(url, expected):
    assert leiden.strip_tracking(url) == expected
