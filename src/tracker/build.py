"""Build the published tables from the clean tables of the raw store.

Run through the `tracker` command: `uv run tracker build [--out build]`. A raw
store is needed (see `tracker.store`).

DuckDB runs in memory. Before the SQL files run, three tables and one function
exist: `clean`, the union of the clean tables of every source; `registry`, one
row per source with its metadata and licence family; `geocode`, one GeoNames
town per (folded name, country) from the latest stored GeoNames file (see
`tracker.geonames`); and `fold(text)`, which folds a name as `geocode` is
folded. A raw store without a GeoNames file fails the build: run
`tracker ingest geonames`. After the SQL files, a row of `clean` that had
coordinates must be unchanged, and a row that gained coordinates must have
`geo_precision` `place`. Each file of `sql/` runs in name order
and must create `events_<family>` and `sources_<family>` for each of
`FAMILIES`. The tables are then checked and written to `<out>/<family>/` as
`events.parquet`, `events.csv` and `sources.parquet`. Nothing is written to
`<out>` if a check fails.
"""

import hashlib
import logging
import shutil
import tempfile
from pathlib import Path

import duckdb
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from duckdb.sqltypes import VARCHAR

from tracker import dedup, geonames
from tracker.errors import UserError
from tracker.ingest import clean_path
from tracker.schema import (
    CLUSTER_COLUMNS,
    COLUMNS,
    PUBLISHED_COLUMNS,
    read_csv,
    validate_published,
)
from tracker.sources import FAMILIES, SOURCES
from tracker.store import Store

logger = logging.getLogger(__name__)

SQL_DIR = Path(__file__).resolve().parents[2] / "sql"

CURATION_DIR = Path(__file__).resolve().parents[2] / "curation"

NUMERIC_COLUMNS = ("lat", "lon")

CLUSTER_TYPES = {"cluster_id": "string", "n_sources": "int64", "is_canonical": "bool"}

SOURCE_COLUMNS = ["source", "title", "kind", "homepage", "licence", "citation"]


def family_sources(family: str) -> list[str]:
    """Keys of the sources whose rows the public dataset of `family` holds.

    The `open` family holds `open` sources. The `nc` family holds every source.
    """
    return [
        name
        for name, source in SOURCES.items()
        if family == "nc" or source.family == family
    ]


def expected_types() -> dict[str, str]:
    """Arrow type name of each column of a published events table.

    pandas writes a text column as `large_string` and DuckDB as `string`: the
    names are compared after the first is mapped to the second.
    """
    types = {
        column: "double" if column in NUMERIC_COLUMNS else "string"
        for column in COLUMNS
    }
    return types | CLUSTER_TYPES


def type_name(arrow_type: pa.DataType) -> str:
    """Arrow type name with `large_string` read as `string`."""
    name = str(arrow_type)
    return "string" if name == "large_string" else name


def load_clean(store: Store, work: Path) -> dict[str, int]:
    """Copy the clean table of each source to `work` and count its rows.

    Raises:
        ValueError: A source has an unknown family or no clean table.
    """
    counts: dict[str, int] = {}
    for name, source in SOURCES.items():
        if source.family not in FAMILIES:
            raise ValueError(f"{name}: unknown licence family {source.family!r}")
        table = store.backend.read(clean_path(name))
        if table is None:
            raise UserError(f"{name}: no clean table, run `tracker ingest {name}`")
        (work / f"{name}.parquet").write_bytes(table)
        counts[name] = pq.read_metadata(work / f"{name}.parquet").num_rows
    return counts


def load_geocode(store: Store) -> pd.DataFrame:
    """The GeoNames lookup of the latest stored file, with its digest logged.

    Raises:
        ValueError: The store has no GeoNames file.
    """
    content = store.latest(geonames.SOURCE, geonames.FILE_NAME)
    if content is None:
        raise UserError(
            f"{geonames.SOURCE}: no stored file, run `tracker ingest {geonames.SOURCE}`"
        )
    stored = [
        line
        for line in store.payloads(geonames.SOURCE)
        if line["name"] == geonames.FILE_NAME
    ]
    logger.info(
        "%s: %s sha256 %s, fetched %s",
        geonames.SOURCE,
        geonames.FILE_NAME,
        hashlib.sha256(content).hexdigest(),
        stored[-1]["fetched_at"],
    )
    return geonames.lookup(content)


def prepare(con: duckdb.DuckDBPyConnection, work: Path, geocode: pd.DataFrame) -> None:
    """Create the tables and the function that the SQL files read.

    `clean` casts each column to its published type: pandas writes a column
    with no value as `double`, and the union must not inherit that. `geocode`
    is the lookup of `tracker.geonames`, and `fold` is its name folding.
    """
    con.create_function("fold", geonames.fold, [VARCHAR], VARCHAR)
    con.register("geocode_frame", geocode)
    con.execute("CREATE TABLE geocode AS SELECT * FROM geocode_frame")
    con.unregister("geocode_frame")
    files = [str(work / f"{name}.parquet") for name in SOURCES]
    columns = ", ".join(
        f"CAST({column} AS {'DOUBLE' if column in NUMERIC_COLUMNS else 'VARCHAR'}) "
        f"AS {column}"
        for column in COLUMNS
    )
    con.execute(
        f"CREATE TABLE clean AS SELECT {columns} "
        "FROM read_parquet(?, union_by_name=true)",
        [files],
    )
    registry = pd.DataFrame(
        [
            {
                "source": name,
                "title": source.title,
                "kind": source.kind,
                "homepage": source.homepage,
                "licence": source.licence,
                "family": source.family,
                "citation": source.citation,
            }
            for name, source in SOURCES.items()
        ]
    )
    con.register("registry_frame", registry)
    con.execute("CREATE TABLE registry AS SELECT * FROM registry_frame")
    con.unregister("registry_frame")


def check_coordinates(con: duckdb.DuckDBPyConnection) -> None:
    """Check that the SQL files kept the coordinates that a source gives.

    Raises:
        ValueError: A row of `clean` that had coordinates differs in `events`
            (latitude, longitude or `geo_precision`), or a row that had none
            has coordinates with a `geo_precision` other than `place`.
    """
    changed = con.execute(
        "SELECT count(*) FROM clean JOIN events USING (event_id) "
        "WHERE clean.lat IS NOT NULL AND ("
        "clean.lat IS DISTINCT FROM events.lat "
        "OR clean.lon IS DISTINCT FROM events.lon "
        "OR clean.geo_precision IS DISTINCT FROM events.geo_precision)"
    ).fetchall()[0][0]
    added = con.execute(
        "SELECT count(*) FROM clean JOIN events USING (event_id) "
        "WHERE clean.lat IS NULL AND events.lat IS NOT NULL "
        "AND events.geo_precision <> 'place'"
    ).fetchall()[0][0]
    if changed:
        raise UserError(f"{changed} rows with source coordinates were changed")
    if added:
        raise UserError(f"{added} rows gained coordinates without `place` precision")


def add_clusters(con: duckdb.DuckDBPyConnection, curation_dir: Path) -> None:
    """Add the cluster columns to `events_<family>` of each family.

    The clusters of a family come from its own rows: the `open` family has no
    row of a NonCommercial source, so its clusters never use one. A line of a
    curation file whose rows are not both in the family is left out of it.

    Raises:
        UserError: A curation line names an event id that is not in `events`,
            or a clustering rule fails (see `tracker.dedup.assign_clusters`).
    """
    merges = dedup.read_curation(curation_dir / "merges.csv")
    splits = dedup.read_curation(curation_dir / "splits.csv")
    known = {row[0] for row in con.execute("SELECT event_id FROM events").fetchall()}
    dedup.check_ids(merges, known, "merges")
    dedup.check_ids(splits, known, "splits")
    for family in FAMILIES:
        table = f"events_{family}"
        frame = con.execute(f"SELECT * FROM {table}").df()
        present = set(frame["event_id"])
        clustered = dedup.assign_clusters(
            frame,
            list(SOURCES),
            dedup.pairs_within(merges, present),
            dedup.pairs_within(splits, present),
        )
        con.register("clusters_frame", clustered[["event_id", *CLUSTER_COLUMNS]])
        con.execute(
            f"CREATE OR REPLACE TABLE {table} AS "
            f"SELECT {table}.*, clusters_frame.* EXCLUDE (event_id) "
            f"FROM {table} JOIN clusters_frame USING (event_id) "
            "ORDER BY source, event_id"
        )
        con.unregister("clusters_frame")


def quote(path: Path) -> str:
    """A path as a SQL string literal."""
    return "'" + str(path).replace("'", "''") + "'"


def export(con: duckdb.DuckDBPyConnection, family: str, folder: Path) -> None:
    """Write the tables of one family to `folder`, in a stable order.

    The order is in the query: the `ORDER_BY` option of COPY writes a folder.
    """
    folder.mkdir(parents=True)
    query = f"SELECT * FROM events_{family} ORDER BY source, event_id"
    parquet = quote(folder / "events.parquet")
    csv = quote(folder / "events.csv")
    sources = quote(folder / "sources.parquet")
    con.execute(f"COPY ({query}) TO {parquet} (FORMAT parquet, COMPRESSION zstd)")
    con.execute(f"COPY ({query}) TO {csv} (FORMAT csv, HEADER true)")
    con.execute(
        f"COPY (SELECT * FROM sources_{family} ORDER BY source) TO {sources} "
        "(FORMAT parquet, COMPRESSION zstd)"
    )


def check(family: str, folder: Path, counts: dict[str, int]) -> None:
    """Check the published files of one family.

    Args:
        family: One of `FAMILIES`.
        folder: Where `export` wrote the family.
        counts: Row count of the clean table of each source.

    Raises:
        ValueError: The columns or types differ from the schema, `validate`
            fails, a source has a different row count than its clean table, or
            the CSV file does not hold the rows of the Parquet file.
    """
    events_file = folder / "events.parquet"
    schema = pq.read_schema(events_file)
    found = {field.name: type_name(field.type) for field in schema}
    if found != expected_types() or list(found) != PUBLISHED_COLUMNS:
        raise UserError(f"{family}: events.parquet breaks the schema: {found}")

    events = pd.read_parquet(events_file)
    validate_published(events)
    csv_events = read_csv(folder / "events.csv")
    validate_published(csv_events)
    if len(csv_events) != len(events):
        raise UserError(
            f"{family}: events.csv has {len(csv_events)} rows, "
            f"events.parquet has {len(events)}"
        )

    published = {
        str(name): int(count) for name, count in events["source"].value_counts().items()
    }
    expected = {name: counts[name] for name in family_sources(family)}
    for name in sorted(set(expected) | set(published)):
        if published.get(name, 0) != expected.get(name, 0):
            raise UserError(
                f"{name}: {published.get(name, 0)} rows in {family}/events.parquet, "
                f"{expected.get(name, 0)} in the clean table"
            )

    listed = pd.read_parquet(folder / "sources.parquet")
    if list(listed.columns) != SOURCE_COLUMNS:
        raise UserError(f"{family}: unexpected sources columns {list(listed.columns)}")
    if sorted(listed["source"]) != sorted(expected):
        raise UserError(
            f"{family}: sources.parquet lists {sorted(listed['source'])}, "
            f"expected {sorted(expected)}"
        )


def build(
    store: Store,
    out: Path,
    sql_dir: Path = SQL_DIR,
    curation_dir: Path | None = None,
) -> None:
    """Run the SQL files, make the clusters and write the tables of each family.

    Args:
        store: The raw store, read for the clean tables.
        out: Folder that gets one subfolder per family. Existing files of the
            same names are replaced, and only after every check passed.
        sql_dir: Folder of the SQL files, run in name order.
        curation_dir: Folder of `merges.csv` and `splits.csv` (see
            `tracker.dedup`). The default is `CURATION_DIR`.

    Raises:
        ValueError: A source has no clean table, or a check of `check` fails.
        duckdb.Error: A SQL file fails.
    """
    files = sorted(sql_dir.glob("*.sql"))
    if not files:
        raise UserError(f"No SQL file in {sql_dir}")
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        counts = load_clean(store, work)
        geocode = load_geocode(store)
        con = duckdb.connect(":memory:")
        try:
            prepare(con, work, geocode)
            for file in files:
                logger.info("Running %s", file.name)
                con.execute(file.read_text())
            check_coordinates(con)
            add_clusters(con, curation_dir or CURATION_DIR)
            staged = work / "out"
            for family in FAMILIES:
                export(con, family, staged / family)
                check(family, staged / family, counts)
        finally:
            con.close()
        for family in FAMILIES:
            target = out / family
            target.mkdir(parents=True, exist_ok=True)
            for file in (staged / family).iterdir():
                shutil.copyfile(file, target / file.name)
    for family in FAMILIES:
        logger.info("%s: %d sources", family, len(family_sources(family)))
