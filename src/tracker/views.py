"""Filters and headline counts of the dashboard, as plain functions on a frame.

An incident is a cluster of rows (see `tracker.dedup`). The functions here keep
the rows of every source and never pick one row to state a value: the app shows
what each source records. The steps are:

1. `scope_rows`: the rows of the selected sources.
2. `passing_rows`: the rows that pass the filters on the fields of one source.
3. `incident_lines`: one line per incident, from the rows in scope.
4. `memberships`: the incidents that each value counts, from the passing rows.
"""

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass

import pandas as pd

EMPTY = "—"


def split_codes(cells: pd.Series) -> pd.Series:
    """Split cells of ISO codes joined by ";" into lists. An empty cell gives []."""
    return cells.fillna("").map(lambda cell: [code for code in cell.split(";") if code])


def with_code_lists(events: pd.DataFrame) -> pd.DataFrame:
    """Add `country_codes` and `actor_codes`, the lists of codes of each event."""
    return events.assign(
        country_codes=split_codes(events["countries"]),
        actor_codes=split_codes(events["actors"]),
    )


def with_clusters(events: pd.DataFrame) -> pd.DataFrame:
    """Give each event its own cluster when the table has no cluster columns.

    Versions of the published tables before 2.2.0, and the snapshot that the
    app bundles, have no `cluster_id`: each row then counts as one incident.
    """
    if "cluster_id" in events.columns:
        return events
    return events.assign(cluster_id=events["event_id"], n_sources=1, is_canonical=True)


def overlaps(codes: pd.Series, selected: Collection[str]) -> pd.Series:
    """True where a list of codes holds at least one selected code."""
    chosen = set(selected)
    return codes.map(lambda found: bool(chosen.intersection(found)))


def scope_rows(
    events: pd.DataFrame, *, sources: Collection[str], order: Sequence[str]
) -> pd.DataFrame:
    """Keep the rows of the selected sources, in the source order.

    The source filter sets the scope: a row of another source is in no line and
    in no count. Each row gets `is_line`, true on the first row of its incident
    in the source order, and `line_date`, the date of that row. This row gives
    the line its title, date and place (record 0004, Source order). A source
    that is not in `order` comes last.

    Args:
        events: A frame from `with_code_lists` and `with_clusters`.
        sources: The selected sources.
        order: Every source, first in the source order first.
    """
    rank = {name: place for place, name in enumerate(order)}
    rows = events[events["source"].isin(sources)]
    places = rows["source"].map(lambda name: rank.get(name, len(rank)))
    rows = rows.iloc[places.to_numpy().argsort(kind="stable")]
    is_line = ~rows["cluster_id"].duplicated()
    line_date = (
        rows["date"].where(is_line).groupby(rows["cluster_id"]).transform("first")
    )
    return rows.assign(is_line=is_line, line_date=line_date)


def passing_rows(
    scope: pd.DataFrame,
    *,
    domains: Collection[str],
    categories: Collection[str],
    statuses: Collection[str],
    attributions: Collection[str],
    actors: Collection[str] = (),
    countries: Collection[str] = (),
) -> pd.DataFrame:
    """Keep the rows of the scope that pass every filter except the period.

    A row passes when it passes all the filters together: an incident whose
    EODI row is `air` and `dismissed` and whose Leiden row is `espionage` and
    `confirmed` does not pass "domain `air`, status not `dismissed`". An
    incident is listed when one of its rows passes.

    An empty `actors` or `countries` keeps every row, also the rows that name no
    state or no country: attribution is a filter, never an inclusion test.
    """
    keep = (
        scope["domain"].isin(domains)
        & scope["category"].isin(categories)
        & scope["event_status"].isin(statuses)
        & scope["attribution"].isin(attributions)
    )
    if actors:
        keep &= overlaps(scope["actor_codes"], actors)
    if countries:
        keep &= overlaps(scope["country_codes"], countries)
    return scope[keep]


def per_source_values(
    scope: pd.DataFrame, column: str, names: Mapping[str, str]
) -> pd.Series:
    """Give the text of a field for each incident, from the rows in scope.

    The text is the value when the rows agree. Otherwise it is each value with
    the name of its source, in the source order: "Air (EODI); Espionage
    (Leiden)". An empty value shows as an em dash and its source, "— (EODI)",
    when the rows disagree. When every row is empty, the rows agree and the
    text is empty.

    Args:
        scope: A frame from `scope_rows`.
        column: The column to read.
        names: The name of each source.

    Returns:
        The texts, indexed by `cluster_id`.
    """
    found: dict[str, list[tuple[str, str]]] = {}
    texts = scope[column].fillna("").astype(str)
    for cluster, source, text in zip(
        scope["cluster_id"], scope["source"], texts, strict=True
    ):
        found.setdefault(cluster, []).append((source, text))
    values = {}
    for cluster, pairs in found.items():
        if len({text for _, text in pairs}) == 1:
            values[cluster] = pairs[0][1]
        else:
            values[cluster] = "; ".join(
                f"{text or EMPTY} ({names[source]})" for source, text in pairs
            )
    return pd.Series(values, dtype=object)


def incident_lines(
    scope: pd.DataFrame,
    passing: pd.DataFrame,
    *,
    fields: Sequence[str],
    names: Mapping[str, str],
) -> pd.DataFrame:
    """Build one line for each incident that has a passing row.

    The line gets its `date`, `end_date`, `date_precision`, `place`, `lat`,
    `lon` and `title` from the first row in scope (see `scope_rows`), and
    `line_source`, the source of that row. Each column of `fields` holds the
    text of `per_source_values`, from all the rows in scope, not only the
    passing ones: the line does not change when a filter other than the source
    filter changes. `has_country` is true when a passing row names a country.
    The lines are in the order of the first row of each incident in `scope`.

    Args:
        scope: A frame from `scope_rows`.
        passing: The rows of `scope` of the incidents to list.
        fields: The columns to combine by source.
        names: The name of each source.
    """
    listed = scope[scope["cluster_id"].isin(passing["cluster_id"])]
    base = ["cluster_id", "date", "end_date", "date_precision", "place", "lat", "lon"]
    lines = listed.loc[listed["is_line"], [*base, "title", "source"]].rename(
        columns={"source": "line_source"}
    )
    for column in fields:
        lines[column] = lines["cluster_id"].map(
            per_source_values(listed, column, names)
        )
    named = set(passing.loc[passing["country_codes"].map(bool), "cluster_id"])
    return lines.assign(has_country=lines["cluster_id"].isin(named))


def memberships(rows: pd.DataFrame, column: str) -> pd.DataFrame:
    """List the values of a field that each incident counts in.

    An incident counts once in each value that one of the rows gives it, so the
    counts by domain can add up to more than the number of incidents. A column of
    lists, such as `country_codes`, gives one value for each item. An empty
    value is no value.

    Args:
        rows: The passing rows, with `line_date`.
        column: The column to read.

    Returns:
        One line for each incident and value, with `cluster_id`, `value` and the
        `line_date` of the incident.
    """
    found = rows[["cluster_id", "line_date", column]].rename(columns={column: "value"})
    found = found.explode("value").dropna(subset=["value"])
    found = found[found["value"] != ""]
    return found.drop_duplicates(["cluster_id", "value"])


def official_by_actor(rows: pd.DataFrame) -> dict[str, int]:
    """Count the incidents of each state that a passing `official` row names.

    The states come from the rows that are `official`, on the same row. An
    incident that names several states counts once for each of them. An
    incident with no state named is in no count.
    """
    official = memberships(rows[rows["attribution"] == "official"], "actor_codes")
    counts = official["value"].value_counts()
    return {str(code): int(n) for code, n in counts.items()}


@dataclass(frozen=True)
class CoordinateCoverage:
    """Where the listed incidents can be seen, and which sources give coordinates.

    `located`, `countries_only` and `on_no_map` are disjoint and add up to the
    number of incidents. A source gives coordinates when at least one of its
    rows in scope has them.
    """

    sources_with: list[str]
    sources_without: list[str]
    located: int
    countries_only: int
    on_no_map: int


def coordinate_coverage(lines: pd.DataFrame, scope: pd.DataFrame) -> CoordinateCoverage:
    """Count the lines by the view that shows them, and split sources by coordinates.

    Args:
        lines: The lines from `incident_lines`.
        scope: The rows in scope of the listed incidents.
    """
    has_coordinates = lines["lat"].notna() & lines["lon"].notna()
    has_country = lines["has_country"]
    row_coordinates = scope["lat"].notna() & scope["lon"].notna()
    with_coordinates = set(scope.loc[row_coordinates, "source"])
    sources = list(dict.fromkeys(scope["source"]))
    return CoordinateCoverage(
        sources_with=[name for name in sources if name in with_coordinates],
        sources_without=[name for name in sources if name not in with_coordinates],
        located=int(has_coordinates.sum()),
        countries_only=int((~has_coordinates & has_country).sum()),
        on_no_map=int((~has_coordinates & ~has_country).sum()),
    )
