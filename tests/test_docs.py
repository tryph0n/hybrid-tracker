"""The data dictionary table that docs/conf.py builds from schema.py."""

import importlib.util

from conftest import ROOT

from tracker.schema import ALLOWED_VALUES, COLUMNS, REQUIRED


def columns_table() -> list[list[str]]:
    spec = importlib.util.spec_from_file_location("conf", ROOT / "docs" / "conf.py")
    conf = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(conf)
    rows = conf.columns_table().splitlines()[2:]
    return [[cell.strip() for cell in row.strip("|").split("|")] for row in rows]


def test_data_dictionary_describes_every_column():
    table = columns_table()
    assert [row[0].strip("`") for row in table] == COLUMNS
    for name, required, values, description in table:
        name = name.strip("`")
        assert required == ("yes" if name in REQUIRED else "no")
        assert bool(values) == (name in ALLOWED_VALUES), name
        assert description, f"{name} has no description"
