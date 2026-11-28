"""Build the published tables from the clean tables of the raw store.

Run through the `tracker` command: `uv run tracker build [--out build]`. A raw
store is needed (see `tracker.store`).

DuckDB runs in memory. Before the SQL files run, three tables and one function
exist: `clean`, the union of the clean tables of the built sources; `registry`, one
row per built source with its metadata and licence family; `geocode`, one GeoNames
town per (folded name, country) from the latest stored GeoNames file (see
`tracker.geonames`); and `fold(text)`, which folds a name as `geocode` is
folded. A raw store without a GeoNames file fails the build: run
`tracker ingest geonames`. After the SQL files, a row of `clean` that had
coordinates must be unchanged, and a row that gained coordinates must have
`geo_precision` `place`. Each file of `sql/` runs in name order
and must create `events_<family>` and `sources_<family>` for each of
`FAMILIES`. The tables are then checked and written to `<out>/<family>/` as
`events.parquet`, `events.csv` and `sources.parquet`. The `open` family also
gets `threat_actors.parquet`, the threat-group table that `tracker.actors`
reduces from the latest stored MISP galaxy file and `curation/actors.csv`; a
store without that file fails the build: run `tracker ingest misp_galaxy`.
Nothing is written to `<out>` if a check fails.

A context source (`Source.table`, see `tracker.stanford`) has no clean table with
the event columns: its month files are copied to `<out>/<family>/<table>/`, for
each family that holds its rows, and never enter `clean` or `events`. It has a
row in `registry` and in the `sources` table of each such family. It is left
out when the clean store holds no month file of it.

A source with no clean table is left out: `build` returns its name, and the
registry, the clusters, the row-count check and the `sources` table all use
the other sources only. The build fails when no source has a clean table. A
family that no built source feeds is written with no row and the schema types.
"""

import hashlib
import logging
import shutil
import tempfile
from collections.abc import Collection, Iterable, Sequence
from pathlib import Path

import duckdb
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from duckdb.sqltypes import VARCHAR

from tracker import actors, dedup, geonames, stanford
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

CLUSTER_SQL_TYPES = {
    "cluster_id": "VARCHAR",
    "n_sources": "BIGINT",
    "is_canonical": "BOOLEAN",
}
"""SQL type of each cluster column: a family with no row must still write them."""

ACTORS_FILE = "threat_actors.parquet"
ACTORS_FAMILY = "open"
"""The family whose dataset holds the `threat_actors` table."""

CONTEXT_TABLES = {stanford.SOURCE: stanford.TABLE}
"""Folder of the month files of each context source in a published dataset."""

SOURCE_COLUMNS = ["source", "title", "kind", "homepage", "licence", "citation"]


def family_sources(family: str, names: Iterable[str] | None = None) -> list[str]:
    """Keys of the sources whose rows the public dataset of `family` holds.

    The `open` family holds `open` sources. The `nc` family holds every source.

    Args:
        family: One of `FAMILIES`.
        names: The sources that are published. Default: every source of the
            registry.
    """
    kept = set(SOURCES if names is None else names)
    return [
        name
        for name, source in SOURCES.items()
        if name in kept and (family == "nc" or source.family == family)
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
    """Copy the clean table of each source that has one to `work`, and count its rows.

    A source with no clean table is left out of the result and logged. A
    context source has none: `load_context` reads it.

    Raises:
        UserError: No source has a clean table.
        ValueError: A source has an unknown family.
    """
    counts: dict[str, int] = {}
    for name, source in SOURCES.items():
        if source.family not in FAMILIES:
            raise ValueError(f"{name}: unknown licence family {source.family!r}")
        if source.table == "context":
            continue
        table = store.backend.read(clean_path(name))
        if table is None:
            logger.warning("%s: no clean table, left out of the build", name)
            continue
        (work / f"{name}.parquet").write_bytes(table)
        counts[name] = pq.read_metadata(work / f"{name}.parquet").num_rows
    if not counts:
        raise UserError("No source has a clean table, run `tracker ingest`")
    return counts


def load_context(store: Store, work: Path) -> dict[str, list[str]]:
    """Copy the month files of each context source to `work/<table>/`.

    A context source with no month file in the clean store is left out of the
    result and logged.

    Returns:
        The months (`YYYY-MM`, oldest first) of each source that has month files.

    Raises:
        UserError: A month file does not have the columns of the context table.
    """
    months: dict[str, list[str]] = {}
    for name, source in SOURCES.items():
        if source.table != "context":
            continue
        folder = work / CONTEXT_TABLES[name]
        found = []
        for month in stanford.stored_months(store):
            content = store.backend.read(stanford.month_path(month))
            if content is None:
                continue
            folder.mkdir(exist_ok=True)
            (folder / f"{month}.parquet").write_bytes(content)
            if pq.read_schema(folder / f"{month}.parquet").names != stanford.COLUMNS:
                raise UserError(f"{name}: month file {month} breaks the schema")
            found.append(month)
        if found:
            months[name] = found
        else:
            logger.warning("%s: no month file, left out of the build", name)
    return months


def empty_families(left_out: Sequence[str]) -> list[str]:
    """The families that no built source feeds with events, given those left out."""
    built = [
        name
        for name, source in SOURCES.items()
        if name not in left_out and source.table == "events"
    ]
    return [family for family in FAMILIES if not family_sources(family, built)]


def left_out_summary(names: Sequence[str]) -> str:
    """The run summary section that names each source and each family left empty."""
    lines = ["### Sources left out of the build (no clean table)", ""]
    lines += [f"- `{name}`: run `tracker ingest {name}`" for name in names]
    empty = empty_families(names)
    if empty:
        lines += ["", "Families with no source, written with no row:"]
        lines += [f"- `{family}`" for family in empty]
    return "\n".join([*lines, ""])


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


def load_actors(store: Store, curation_dir: Path) -> pd.DataFrame:
    """The `threat_actors` table from the latest stored MISP galaxy file.

    The digest of the file is logged. The reduction reads the stored file and
    `curation/actors.csv` only.

    Raises:
        UserError: The store has no MISP galaxy file, or the reduction fails
            (see `tracker.actors.reduce`).
    """
    content = store.latest(actors.SOURCE, actors.FILE_NAME)
    if content is None:
        raise UserError(
            f"{actors.SOURCE}: no stored file, run `tracker ingest {actors.SOURCE}`"
        )
    logger.info(
        "%s: %s sha256 %s",
        actors.SOURCE,
        actors.FILE_NAME,
        hashlib.sha256(content).hexdigest(),
    )
    return actors.reduce(content, actors.read_curation(curation_dir / "actors.csv"))


def export_actors(table: pd.DataFrame, folder: Path) -> None:
    """Write the `threat_actors` table to `folder` as Parquet."""
    table.to_parquet(folder / ACTORS_FILE, index=False, compression="zstd")


def check_actors(folder: Path, table: pd.DataFrame) -> None:
    """Check the written `threat_actors` file against the table.

    Raises:
        UserError: The columns or the row count differ.
    """
    written = pd.read_parquet(folder / ACTORS_FILE)
    if list(written.columns) != actors.COLUMNS or len(written) != len(table):
        raise UserError(f"{ACTORS_FILE} breaks its schema: {list(written.columns)}")


def prepare(
    con: duckdb.DuckDBPyConnection,
    work: Path,
    geocode: pd.DataFrame,
    names: Sequence[str],
    context: Collection[str],
) -> None:
    """Create the tables and the function that the SQL files read.

    `names` are the sources that have a clean table: `clean` holds those only.
    `registry` holds them and the context sources in `context`, which have
    month files and no clean table.

    `clean` casts each column to its published type: pandas writes a column
    with no value as `double`, and the union must not inherit that. A column
    that no stored table has (`drone_presence` before the first ingest of
    each source after the schema change) is read as empty. `geocode`
    is the lookup of `tracker.geonames`, and `fold` is its name folding.
    """
    con.create_function("fold", geonames.fold, [VARCHAR], VARCHAR)
    con.register("geocode_frame", geocode)
    con.execute("CREATE TABLE geocode AS SELECT * FROM geocode_frame")
    con.unregister("geocode_frame")
    files = [str(work / f"{name}.parquet") for name in names]
    stored = {
        row[0]
        for row in con.execute(
            "DESCRIBE SELECT * FROM read_parquet(?, union_by_name=true)", [files]
        ).fetchall()
    }
    columns = ", ".join(
        f"CAST({column if column in stored else 'NULL'} "
        f"AS {'DOUBLE' if column in NUMERIC_COLUMNS else 'VARCHAR'}) AS {column}"
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
            if name in names or name in context
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


def add_clusters(
    con: duckdb.DuckDBPyConnection, curation_dir: Path, names: Sequence[str]
) -> None:
    """Add the cluster columns to `events_<family>` of each family.

    `names` are the sources that are built, in rank order.

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
    cluster_select = ", ".join(
        f"CAST(clusters_frame.{column} AS {CLUSTER_SQL_TYPES[column]}) AS {column}"
        for column in CLUSTER_COLUMNS
    )
    for family in FAMILIES:
        table = f"events_{family}"
        frame = con.execute(f"SELECT * FROM {table}").df()
        present = set(frame["event_id"])
        clustered = dedup.assign_clusters(
            frame,
            list(names),
            dedup.pairs_within(merges, present),
            dedup.pairs_within(splits, present),
        )
        con.register("clusters_frame", clustered[["event_id", *CLUSTER_COLUMNS]])
        con.execute(
            f"CREATE OR REPLACE TABLE {table} AS "
            f"SELECT {table}.*, {cluster_select} "
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


def check(
    family: str, folder: Path, counts: dict[str, int], context: Collection[str]
) -> None:
    """Check the published files of one family.

    Args:
        family: One of `FAMILIES`.
        folder: Where `export` wrote the family.
        counts: Row count of the clean table of each built source.
        context: The context sources that are built. They have no event row
            and a row in `sources.parquet`.

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
    if not events.empty:
        validate_published(events)
    csv_events = read_csv(folder / "events.csv")
    if not csv_events.empty:
        validate_published(csv_events)
    if len(csv_events) != len(events):
        raise UserError(
            f"{family}: events.csv has {len(csv_events)} rows, "
            f"events.parquet has {len(events)}"
        )

    published = {
        str(name): int(count) for name, count in events["source"].value_counts().items()
    }
    expected = {name: counts[name] for name in family_sources(family, counts)}
    for name in sorted(set(expected) | set(published)):
        if published.get(name, 0) != expected.get(name, 0):
            raise UserError(
                f"{name}: {published.get(name, 0)} rows in {family}/events.parquet, "
                f"{expected.get(name, 0)} in the clean table"
            )

    listed = pd.read_parquet(folder / "sources.parquet")
    if list(listed.columns) != SOURCE_COLUMNS:
        raise UserError(f"{family}: unexpected sources columns {list(listed.columns)}")
    names = sorted(family_sources(family, [*counts, *context]))
    if sorted(listed["source"]) != names:
        raise UserError(
            f"{family}: sources.parquet lists {sorted(listed['source'])}, "
            f"expected {names}"
        )


def build(
    store: Store,
    out: Path,
    sql_dir: Path = SQL_DIR,
    curation_dir: Path | None = None,
) -> list[str]:
    """Run the SQL files, make the clusters and write the tables of each family.

    Args:
        store: The raw store, read for the clean tables.
        out: Folder that gets one subfolder per family. Existing files of the
            same names are replaced, and only after every check passed.
        sql_dir: Folder of the SQL files, run in name order.
        curation_dir: Folder of `merges.csv` and `splits.csv` (see
            `tracker.dedup`). The default is `CURATION_DIR`.

    Returns:
        The sources that have no clean table (for a context source, no month
        file), left out of the tables.

    Raises:
        UserError: No source has a clean table, or a check of `check` fails.
        ValueError: A source has an unknown family.
        duckdb.Error: A SQL file fails.
    """
    files = sorted(sql_dir.glob("*.sql"))
    if not files:
        raise UserError(f"No SQL file in {sql_dir}")
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        counts = load_clean(store, work)
        context = load_context(store, work)
        built = [name for name in SOURCES if name in counts]
        geocode = load_geocode(store)
        threat_actors = load_actors(store, curation_dir or CURATION_DIR)
        con = duckdb.connect(":memory:")
        try:
            prepare(con, work, geocode, built, context)
            for file in files:
                logger.info("Running %s", file.name)
                con.execute(file.read_text())
            check_coordinates(con)
            add_clusters(con, curation_dir or CURATION_DIR, built)
            staged = work / "out"
            for family in FAMILIES:
                export(con, family, staged / family)
                check(family, staged / family, counts, context)
                if family == ACTORS_FAMILY:
                    export_actors(threat_actors, staged / family)
                    check_actors(staged / family, threat_actors)
                for name in family_sources(family, context):
                    table = CONTEXT_TABLES[name]
                    shutil.copytree(work / table, staged / family / table)
        finally:
            con.close()
        for family in FAMILIES:
            target = out / family
            target.mkdir(parents=True, exist_ok=True)
            for file in (staged / family).iterdir():
                if file.is_dir():
                    shutil.copytree(file, target / file.name, dirs_exist_ok=True)
                else:
                    shutil.copyfile(file, target / file.name)
    for family in FAMILIES:
        logger.info("%s: %d sources", family, len(family_sources(family, built)))
    return [name for name in SOURCES if name not in counts and name not in context]
