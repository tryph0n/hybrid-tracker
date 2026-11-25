"""Filters and headline counts of the dashboard, as plain functions on a frame."""

from collections.abc import Collection
from dataclasses import dataclass

import pandas as pd


def split_codes(cells: pd.Series) -> pd.Series:
    """Split cells of ISO codes joined by ";" into lists. An empty cell gives []."""
    return cells.fillna("").map(lambda cell: [code for code in cell.split(";") if code])


def with_code_lists(events: pd.DataFrame) -> pd.DataFrame:
    """Add `country_codes` and `actor_codes`, the lists of codes of each event."""
    return events.assign(
        country_codes=split_codes(events["countries"]),
        actor_codes=split_codes(events["actors"]),
    )


def overlaps(codes: pd.Series, selected: Collection[str]) -> pd.Series:
    """True where a list of codes holds at least one selected code."""
    chosen = set(selected)
    return codes.map(lambda found: bool(chosen.intersection(found)))


def filter_events(
    events: pd.DataFrame,
    *,
    sources: Collection[str],
    domains: Collection[str],
    categories: Collection[str],
    statuses: Collection[str],
    attributions: Collection[str],
    actors: Collection[str] = (),
    countries: Collection[str] = (),
) -> pd.DataFrame:
    """Keep the events that every filter except the period keeps.

    An empty `actors` or `countries` keeps every event, also the events that name
    no state or no country: attribution is a filter, never an inclusion test.
    A frame from `with_code_lists` is required.
    """
    keep = (
        events["source"].isin(sources)
        & events["domain"].isin(domains)
        & events["category"].isin(categories)
        & events["event_status"].isin(statuses)
        & events["attribution"].isin(attributions)
    )
    if actors:
        keep &= overlaps(events["actor_codes"], actors)
    if countries:
        keep &= overlaps(events["country_codes"], countries)
    return events[keep]


def official_by_actor(events: pd.DataFrame) -> dict[str, int]:
    """Count the `official` events of each state, most events first.

    An event that names several states counts once for each of them. An event
    with no state named is in no count.
    """
    official = events[events["attribution"] == "official"]
    counts = official["actor_codes"].explode().dropna().value_counts()
    return {str(code): int(n) for code, n in counts.items()}


@dataclass(frozen=True)
class CoordinateCoverage:
    """Where the selected events can be seen, and which sources give coordinates.

    `located`, `countries_only` and `on_no_map` are disjoint and add up to the
    number of events. A source gives coordinates when at least one of its events
    has them.
    """

    sources_with: list[str]
    sources_without: list[str]
    located: int
    countries_only: int
    on_no_map: int


def coordinate_coverage(events: pd.DataFrame) -> CoordinateCoverage:
    """Count the events by the view that shows them, and split sources by coordinates."""
    has_coordinates = events["lat"].notna() & events["lon"].notna()
    has_country = events["countries"].notna()
    with_coordinates = set(events.loc[has_coordinates, "source"])
    sources = list(dict.fromkeys(events["source"]))
    return CoordinateCoverage(
        sources_with=[name for name in sources if name in with_coordinates],
        sources_without=[name for name in sources if name not in with_coordinates],
        located=int(has_coordinates.sum()),
        countries_only=int((~has_coordinates & has_country).sum()),
        on_no_map=int((~has_coordinates & ~has_country).sum()),
    )
