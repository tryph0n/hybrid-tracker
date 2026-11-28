"""Filters and headline counts of the dashboard, as plain functions on a frame.

An incident is a cluster of rows (see `tracker.dedup`). The functions here keep
the rows of every source and never pick one row to state a value: the app shows
what each source records. The steps are:

1. `scope_rows`: the rows of the selected sources.
2. `passing_rows`: the rows that pass the filters on the fields of one source.
3. `incident_lines`: one line per incident, from the rows in scope.
4. `memberships`: the incidents that each value counts, from the passing rows.
"""

import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import pandas as pd

from tracker.schema import EUROPEAN_STATES

EMPTY = "—"
DAY = re.compile(r"\d{4}-\d{2}-\d{2}")


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
    `lon`, `geo_precision` and `title` from the first row in scope (see `scope_rows`), and
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
    base = [
        "cluster_id",
        "date",
        "end_date",
        "date_precision",
        "place",
        "lat",
        "lon",
        "geo_precision",
    ]
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


def split_by_scope(
    in_country: pd.DataFrame, selected: Collection[str] = ()
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split country memberships into the European list and the other states.

    Args:
        in_country: The memberships of `country_codes`, from `memberships`.
        selected: The codes of the Countries filter. Empty keeps every code.

    Returns:
        The memberships of the states on the list, and those of the other
        states. Both keep only the selected codes when `selected` is set.
    """
    if selected:
        in_country = in_country[in_country["value"].isin(selected)]
    on_list = in_country["value"].isin(EUROPEAN_STATES)
    return in_country[on_list], in_country[~on_list]


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

    A source gives coordinates when at least one of its rows in scope has them.
    """

    sources_with: list[str]
    sources_without: list[str]


@dataclass(frozen=True)
class WhereSplit:
    """The listed incidents by the view that shows them.

    `located`, `countries_only` and `no_country` are disjoint and add up to
    `total`, so that no incident is left out. `at_sea` is true when every
    incident of `no_country` has `geo_precision` `sea`.
    """

    total: int
    located: int
    countries_only: int
    no_country: int
    at_sea: bool


NO_PLACE = "no place in the source"


def coordinate_coverage(scope: pd.DataFrame) -> CoordinateCoverage:
    """Split the sources of the rows in scope by whether they give coordinates."""
    row_coordinates = scope["lat"].notna() & scope["lon"].notna()
    with_coordinates = set(scope.loc[row_coordinates, "source"])
    sources = list(dict.fromkeys(scope["source"]))
    return CoordinateCoverage(
        sources_with=[name for name in sources if name in with_coordinates],
        sources_without=[name for name in sources if name not in with_coordinates],
    )


def _without_place(lines: pd.DataFrame) -> pd.DataFrame:
    """The lines with no coordinates and no country."""
    has_coordinates = lines["lat"].notna() & lines["lon"].notna()
    return lines[~has_coordinates & ~lines["has_country"]]


def where_split(lines: pd.DataFrame) -> WhereSplit:
    """Count the lines by the view that shows them.

    Args:
        lines: The lines from `incident_lines`.
    """
    has_coordinates = lines["lat"].notna() & lines["lon"].notna()
    nowhere = _without_place(lines)
    return WhereSplit(
        total=len(lines),
        located=int(has_coordinates.sum()),
        countries_only=int((~has_coordinates & lines["has_country"]).sum()),
        no_country=len(nowhere),
        at_sea=bool((nowhere["geo_precision"] == "sea").all()),
    )


def split_line(
    split: WhereSplit, *, countries_pointer: str | None = "Countries view"
) -> str:
    """The line that says where each listed incident is shown.

    A part with no incident has no pointer.

    Args:
        split: The counts from `where_split`.
        countries_pointer: Where the incidents of a country only are shown, or
            None when nothing shows them.
    """

    def part(count: int, text: str, pointer: str | None) -> str:
        if not count:
            return f"0 {text}"
        return f"{count:,} {text} ({pointer})" if pointer else f"{count:,} {text}"

    unit = "incident" if split.total == 1 else "incidents"
    last = "at sea only" if split.at_sea else "with no country"
    return (
        f"{split.total:,} {unit}: "
        + part(split.located, "at a place", "map")
        + ", "
        + part(split.countries_only, "in a country only", countries_pointer)
        + ", "
        + part(split.no_country, last, "listed below")
    )


@dataclass(frozen=True)
class CountryOnly:
    """The incidents with a country and no point, by country and domain.

    `counts` has one line for each state of the European list and domain, with
    `code`, `domain`, `solid` (incidents that name only this country) and
    `hatched` (incidents that also name other countries). `outside` holds the
    memberships of the other states that these incidents name. `several` is the
    number of incidents of `counts` that name several countries.
    """

    counts: pd.DataFrame
    outside: pd.DataFrame
    several: int


def country_only(
    rows: pd.DataFrame, lines: pd.DataFrame, selected: Collection[str] = ()
) -> CountryOnly:
    """Count the incidents with a country and no coordinates.

    An incident counts once in each country that it names and once in each of
    its domains. It is hatched when its passing rows name more than one
    country, on the list and the Countries filter or not.

    Args:
        rows: The passing rows, with `line_date` and `domain_label`.
        lines: The lines from `incident_lines`.
        selected: The codes of the Countries filter. Empty keeps every code.
    """
    no_point = lines["lat"].isna() | lines["lon"].isna()
    shown = set(lines.loc[no_point & lines["has_country"], "cluster_id"])
    mine = rows[rows["cluster_id"].isin(shown)]
    named = memberships(mine, "country_codes")
    several = named.groupby("cluster_id")["value"].nunique() > 1
    on_list, outside = split_by_scope(named, selected)
    domains = memberships(mine, "domain_label").rename(columns={"value": "domain"})
    pairs = on_list.rename(columns={"value": "code"})[["cluster_id", "code"]].merge(
        domains[["cluster_id", "domain"]], on="cluster_id"
    )
    pairs["hatched"] = pairs["cluster_id"].map(several).astype(bool)
    counts = (
        pairs.groupby(["code", "domain"])["hatched"]
        .agg(hatched="sum", total="size")
        .reset_index()
    )
    counts["hatched"] = counts["hatched"].astype(int)
    counts["solid"] = counts["total"].astype(int) - counts["hatched"]
    return CountryOnly(
        counts=counts[["code", "domain", "solid", "hatched"]],
        outside=outside,
        several=int(pairs.loc[pairs["hatched"], "cluster_id"].nunique()),
    )


def no_country_places(lines: pd.DataFrame) -> list[tuple[str, int]]:
    """The places, as the source writes them, of the lines with no country or point.

    Returns:
        Each place with its number of incidents, the largest count first, then
        by place from A to Z. An empty place reads `NO_PLACE`.
    """
    places = _without_place(lines)["place"].fillna("").replace("", NO_PLACE)
    counts = places.value_counts()
    return sorted(
        ((str(p), int(n)) for p, n in counts.items()), key=lambda x: (-x[1], x[0])
    )


def no_country_line(split: WhereSplit, places: Sequence[tuple[str, int]]) -> str:
    """The line that lists the places of the incidents with no country."""
    start = "At sea only, no country" if split.at_sea else "With no country"
    return f"{start}: " + ", ".join(f"{place} ({n:,})" for place, n in places) + "."


def source_date(value: Any) -> str:
    """A date of the quality table as shown: a day, a minute in UTC, or "".

    A value of one day stays a day, because the source gives no time. A value
    with a time shows to the minute in UTC. A missing value gives an empty text.
    """
    if value is None or pd.isna(value) or value == "":
        return ""
    text = str(value)
    if DAY.fullmatch(text):
        return text
    try:
        moment = pd.Timestamp(text)
    except ValueError:
        return text
    moment = moment.tz_localize("UTC") if moment.tzinfo is None else moment
    return moment.tz_convert("UTC").strftime("%Y-%m-%d %H:%M UTC")


def latest_dates(quality: pd.DataFrame | None) -> dict[str, tuple[str, str]]:
    """The shown `downloaded_at` and `updated_by_source` of each source.

    Takes the newest row of each source (by `run_at`). Gives no entry when the
    table is missing or lacks the date columns.
    """
    wanted = {"source", "run_at", "downloaded_at", "updated_by_source"}
    if quality is None or not wanted <= set(quality.columns):
        return {}
    newest = quality.sort_values("run_at").groupby("source").tail(1)
    return {
        str(row.source): (
            source_date(row.downloaded_at),
            source_date(row.updated_by_source),
        )
        for row in newest.itertuples()
    }


def published_sources(
    events: pd.DataFrame, order: Sequence[str], tables: Collection[str] = ()
) -> list[str]:
    """The sources that have rows in the published events, in the source order.

    Args:
        events: The published events.
        order: Every source of the registry, first in the source order first.
        tables: Sources that have data in a published table of their own, not in
            the events (a context source). They are listed too.
    """
    present = set(events["source"]) | set(tables)
    return [name for name in order if name in present]
