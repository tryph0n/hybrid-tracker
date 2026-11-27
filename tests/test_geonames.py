import io
import time
import zipfile

import pytest
from conftest import geonames_archive

from tracker import geonames

pytestmark = pytest.mark.disable_socket


def place(id_, name, country="PL", **fields):
    return {"id": id_, "name": name, "country": country, "lat": "50", "lon": "20"} | (
        fields
    )


def pick(places, name, country="PL"):
    table = geonames.lookup(geonames_archive(places))
    found = table[(table["key"] == geonames.fold(name)) & (table["country"] == country)]
    assert len(found) <= 1
    return None if found.empty else int(found["geonameid"].iloc[0])


def test_fold_drops_case_and_diacritics_and_extra_spaces():
    assert geonames.fold("  Białowieża ") == "białowieza"
    assert geonames.fold("CUHUREȘTII  de Jos") == "cuhurestii de jos"
    assert geonames.fold("Straße") == "strasse"


def test_a_place_answers_to_its_name_ascii_name_and_alternate_names():
    places = [
        place(
            1,
            "Białowieża",
            ascii="Bialowieza",
            alternates="Belovezha,BIA,Беловежа",
        )
    ]
    for name in ["Białowieża", "bialowieza", "Belovezha", "беловежа"]:
        assert pick(places, name) == 1


def test_the_country_constrains_the_match():
    places = [place(1, "Narva", "EE"), place(2, "Narva", "PL")]
    assert pick(places, "Narva", "EE") == 1
    assert pick(places, "Narva", "PL") == 2
    assert pick(places, "Narva", "LT") is None


def test_an_unknown_name_has_no_match():
    assert pick([place(1, "Hanko", "FI")], "Saxony-Anhalt", "DE") is None


def test_the_larger_population_wins():
    places = [
        place(1, "Zagórze", population=900),
        place(2, "Zagórze", population=5000),
    ]
    assert pick(places, "Zagórze") == 2


def test_a_name_beats_an_alternate_name_whatever_the_population():
    places = [
        place(1, "Big Town", alternates="Hamlet", population=900000),
        place(2, "Hamlet", population=600),
    ]
    assert pick(places, "Hamlet") == 2


def test_the_feature_code_breaks_a_population_tie():
    places = [
        place(1, "Twin", code="PPL", population=1000),
        place(2, "Twin", code="PPLA2", population=1000),
        place(3, "Twin", code="PPLA", population=1000),
    ]
    assert pick(places, "Twin") == 3


def test_the_smaller_id_breaks_the_last_tie():
    places = [place(9, "Twin"), place(4, "Twin"), place(7, "Twin")]
    assert pick(places, "Twin") == 4


def test_the_choice_does_not_depend_on_the_file_order():
    places = [
        place(1, "Twin", population=10),
        place(2, "Twin", population=10, code="PPLA"),
        place(3, "Twin", population=99),
        place(4, "Other"),
    ]
    forward = geonames.lookup(geonames_archive(places))
    backward = geonames.lookup(geonames_archive(places[::-1]))
    assert forward.equals(backward)


@pytest.mark.parametrize("code", ["PPLX", "PPLQ", "PPLH", "PPLW", "PPLCH"])
def test_a_section_an_abandoned_or_a_historical_place_is_left_out(code):
    places = [place(1, "Ghost", code=code, population=99999), place(2, "Ghost")]
    assert pick(places, "Ghost") == 2
    assert pick([place(1, "Ghost", code=code)], "Ghost") is None


def test_the_code_of_namibia_is_not_a_missing_value():
    assert pick([place(1, "Windhoek", "NA")], "Windhoek", "NA") == 1


def test_a_name_with_a_quote_character_is_read_as_text():
    assert pick([place(1, 'Ville "Haute"')], 'Ville "Haute"') == 1


def test_the_coordinates_are_those_of_the_file():
    table = geonames.lookup(
        geonames_archive([place(1, "Hanko", "FI", lat="59.83333", lon="22.95")])
    )
    row = table[table["key"] == "hanko"].iloc[0]
    assert (row["lat"], row["lon"]) == (59.83333, 22.95)


def test_an_archive_without_one_text_file_is_rejected():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("a.txt", "")
        archive.writestr("b.txt", "")
    with pytest.raises(ValueError, match="one .txt file"):
        geonames.read_places(buffer.getvalue())


def test_the_test_archive_has_the_same_bytes_at_any_clock_time(monkeypatch):
    now = time.time()
    monkeypatch.setattr(time, "time", lambda: now)
    first = geonames_archive()
    monkeypatch.setattr(time, "time", lambda: now + 3600)

    assert geonames_archive() == first
