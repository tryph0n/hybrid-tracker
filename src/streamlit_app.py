"""Dashboard: maps and charts of the unified hybrid-incident dataset.

Run from the repository root: streamlit run src/streamlit_app.py
"""

from collections.abc import Collection
from datetime import date

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import pycountry
import streamlit as st

from tracker.clock import utc_today
from tracker.hub import (
    REPO_ID,
    UNREACHABLE,
    configured_revision,
    load_published,
    snapshot_dir,
)
from tracker.schema import (
    ATTRIBUTIONS,
    CATEGORIES,
    CATEGORY_DOMAINS,
    DOMAINS,
    EVENT_STATUSES,
    PUBLISHED_COLUMNS,
)
from tracker.sources import SOURCES
from tracker.views import (
    CountryOnly,
    coordinate_coverage,
    country_only,
    incident_lines,
    latest_dates,
    memberships,
    no_country_line,
    no_country_places,
    official_by_actor,
    passing_rows,
    published_sources,
    scope_rows,
    split_by_scope,
    split_line,
    where_split,
    with_clusters,
    with_code_lists,
)

DOMAIN_LABELS = {
    "air": "Air",
    "sabotage": "Sabotage",
    "violence": "Violence",
    "maritime": "Maritime",
    "cyber": "Cyber",
    "information": "Information",
    "espionage": "Espionage",
    "other": "Other",
}

CATEGORY_LABELS = {
    "drone_overflight": "Drone overflight",
    "airport_disruption": "Airport disruption",
    "airspace_incursion": "Airspace incursion",
    "airspace_disruption": "Airspace disruption",
    "sabotage": "Sabotage",
    "arson": "Arson",
    "bomb_or_threat": "Bomb or threat",
    "assassination": "Assassination",
    "cable_or_pipeline_damage": "Cable or pipeline damage",
    "vessel_incident": "Vessel incident",
    "service_disruption": "Service disruption",
    "intrusion": "Intrusion",
    "data_theft_or_leak": "Data theft or leak",
    "influence_operation": "Influence operation",
    "symbolic_vandalism": "Symbolic vandalism",
    "reconnaissance": "Reconnaissance",
    "instrumentalised_migration": "Instrumentalised migration",
    "unclassified": "Unclassified",
}
EVENT_STATUS_LABELS = {
    "confirmed": "Confirmed",
    "unconfirmed": "Reported, not confirmed",
    "dismissed": "Dismissed by the source",
}
DRONE_PRESENCE_LABELS = {
    "confirmed": "Confirmed",
    "not_confirmed": "Not confirmed",
    "not_substantiated": "Not substantiated",
    "ruled_out": "Ruled out",
}
ATTRIBUTION_LABELS = {
    "official": "Attributed by an authority",
    "assessed": "Assessed by the source's analysts",
    "claimed": "Claimed by a group",
    "none": "No attribution in the source",
}
PERIODS = {
    "All time": None,
    "Last 12 months": 365,
    "Last 90 days": 90,
    "Last 30 days": 30,
}

# One fixed colour per domain, in DOMAIN_LABELS order, checked for colour-blind
# separation on each theme. "Other" stays neutral grey.
LIGHT_COLORS = [
    "#0072b2",
    "#d55e00",
    "#7a1f5c",
    "#56b4e9",
    "#00634a",
    "#c99700",
    "#e58fc0",
    "#898781",
]
DARK_COLORS = [
    "#2f7fd0",
    "#e8742a",
    "#b0558f",
    "#8fd0f5",
    "#3fd0a0",
    "#f0c040",
    "#f0a0c8",
    "#898781",
]
# Single-hue ramp for magnitudes, from few to many on a light background.
BLUE_RAMP = [
    "#cde2fb",
    "#9ec5f4",
    "#6da7ec",
    "#3987e5",
    "#256abf",
    "#184f95",
    "#0d366b",
]

EUROPE_CENTER = {"lat": 53, "lon": 14}

POINT_SOURCES = {
    "exact": "Coordinates from the source",
    "place": "Town centre (GeoNames) of the place that the source names",
}

SOURCE_NAMES = {name: source.short_name for name, source in SOURCES.items()}
# The fields of one source: a line shows each of them for each source.
LINE_FIELDS = [
    "domain_label",
    "category_label",
    "status_label",
    "drone_presence_label",
    "attribution_label",
    "actor_names",
    "attributed_by_label",
    "country_names",
]


@st.cache_data(ttl="1h", max_entries=2)
def load_events(
    revision: str,
) -> tuple[
    pd.DataFrame,
    str,
    str,
    str,
    pd.DataFrame | None,
    dict[str, tuple[date, date] | None],
]:
    """Load and prepare the published events: the Hub is read once an hour.

    Returns:
        The events, the revision read, its date and whether the bundled
        snapshot was used because the Hub could not be read, the quality
        table (None when it could not be read), and the first and the newest day
        of each context source (see `tracker.hub.Published.context`).
    """
    published = load_published(revision, snapshot_dir())
    events = published.events
    if "drone_presence" not in events:
        events["drone_presence"] = None
    events["date"] = pd.to_datetime(events["date"])
    events["end_date"] = pd.to_datetime(events["end_date"])
    events["domain_label"] = events["domain"].map(DOMAIN_LABELS)
    events["category_label"] = events["category"].map(CATEGORY_LABELS)
    events["attribution_label"] = events["attribution"].map(ATTRIBUTION_LABELS)
    events["status_label"] = events["event_status"].map(EVENT_STATUS_LABELS)
    events["drone_presence_label"] = (
        events["drone_presence"].map(DRONE_PRESENCE_LABELS).fillna("")
    )
    events["attributed_by_label"] = events["attributed_by"].fillna("")
    events["actor_names"] = (
        events["actors"]
        .fillna("")
        .map(
            lambda cell: ", ".join(
                country_name(code) for code in cell.split(";") if code
            )
        )
    )
    events = with_code_lists(with_clusters(events))
    events["country_names"] = events["country_codes"].map(
        lambda codes: ", ".join(country_name(code) for code in codes)
    )
    return (
        events,
        published.revision,
        published.date,
        published.failure,
        published.quality,
        published.context,
    )


COUNTRIES_HELP = (
    "States of the European list of the data scope (43 states) that the "
    "selected incidents name."
)


def country_name(code: str) -> str:
    country = pycountry.countries.get(alpha_2=code)
    if country is None:
        return code
    return getattr(country, "common_name", country.name)


def country_iso3(code: str) -> str | None:
    country = pycountry.countries.get(alpha_2=code)
    return country.alpha_3 if country else None


def count_by_country(in_country: pd.DataFrame) -> pd.DataFrame:
    """Incidents per country, from the memberships of `country_codes`.

    An incident that one source puts in two countries, or that two sources put
    in two countries, counts in both.
    """
    counts = (
        in_country["value"]
        .value_counts()
        .rename_axis("code")
        .reset_index(name="events")
    )
    counts["country"] = counts["code"].map(country_name)
    counts["iso3"] = counts["code"].map(country_iso3)
    return counts


def timeline_chart(
    in_domain: pd.DataFrame, colors: dict[str, str], weekly: bool, surface: str
) -> go.Figure:
    """Incidents per week or month of their date, once in each of their domains."""
    period = in_domain["line_date"].dt.to_period("W" if weekly else "M").dt.start_time
    counts = (
        in_domain.groupby([period.rename("period"), "value"])
        .size()
        .reset_index(name="events")
    )
    fig = px.bar(
        counts,
        x="period",
        y="events",
        color="value",
        category_orders={"value": list(colors)},
        color_discrete_map=colors,
        labels={"period": "", "events": "Events", "value": ""},
    )
    fig.update_traces(marker_line_color=surface, marker_line_width=1)
    fig.update_layout(
        barcornerradius=3,
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02, "x": 0},
        margin={"l": 0, "r": 0, "t": 10, "b": 0},
        height=420,
    )
    return fig


def points_map(
    located: pd.DataFrame, in_domain: pd.DataFrame, colors: dict[str, str], dark: bool
) -> go.Figure:
    """One marker for each incident and domain of its passing rows, at its place."""
    located = located.assign(
        day=located["date"].dt.strftime("%Y-%m-%d"),
        here=located.groupby(["lat", "lon"])["lat"].transform("size"),
        point=located["geo_precision"].map(POINT_SOURCES).fillna(""),
    ).merge(
        in_domain[["cluster_id", "value"]].rename(columns={"value": "marker_domain"}),
        on="cluster_id",
    )
    fig = px.scatter_map(
        located,
        lat="lat",
        lon="lon",
        color="marker_domain",
        hover_name="title",
        hover_data={
            "day": True,
            "place": True,
            "category_label": True,
            "drone_presence_label": True,
            "attribution_label": True,
            "attributed_by_label": True,
            "actor_names": True,
            "here": True,
            "point": True,
            "lat": False,
            "lon": False,
            "marker_domain": False,
        },
        category_orders={"marker_domain": list(colors)},
        color_discrete_map=colors,
        labels={
            "marker_domain": "",
            "day": "Date",
            "place": "Place",
            "category_label": "Category",
            "drone_presence_label": "Drone presence",
            "attribution_label": "Attribution",
            "attributed_by_label": "Attributed by",
            "actor_names": "Actors",
            "here": "Events here",
            "point": "Point",
        },
        opacity=0.75,
        zoom=2.7,
        center=EUROPE_CENTER,
        map_style="carto-darkmatter" if dark else "carto-positron",
        height=560,
    )
    fig.update_traces(marker_size=9)
    fig.update_layout(
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.01, "x": 0},
        margin={"l": 0, "r": 0, "t": 10, "b": 0},
    )
    return fig


def countries_map(counts: pd.DataFrame, dark: bool) -> go.Figure:
    fig = px.choropleth(
        counts.dropna(subset=["iso3"]),
        locations="iso3",
        color="events",
        hover_name="country",
        hover_data={"iso3": False},
        scope="europe",
        # On a dark background the low end must be the dark one.
        color_continuous_scale=BLUE_RAMP[::-1] if dark else BLUE_RAMP,
        labels={"events": "Events"},
        height=560,
    )
    fig.update_geos(
        bgcolor="rgba(0,0,0,0)",
        showframe=False,
        showcoastlines=False,
        landcolor="#262730" if dark else "#f0efec",
        countrycolor="#555555" if dark else "#c3c2b7",
        lataxis_range=[34, 72],
        lonaxis_range=[-25, 45],
    )
    fig.update_layout(margin={"l": 0, "r": 0, "t": 10, "b": 0})
    return fig


def countries_bar(counts: pd.DataFrame) -> go.Figure:
    top = counts.nlargest(15, "events", keep="all")
    fig = px.bar(
        top,
        x="events",
        y="country",
        orientation="h",
        text="events",
        labels={"events": "Events", "country": ""},
    )
    fig.update_traces(
        marker_color=BLUE_RAMP[3], textposition="outside", cliponaxis=False
    )
    fig.update_layout(
        barcornerradius=3,
        yaxis={"autorange": "reversed"},
        margin={"l": 0, "r": 30, "t": 10, "b": 0},
        height=30 * len(top) + 60,
    )
    return fig


def outside_note(
    outside: pd.DataFrame, selected: Collection[str], on_list_empty: bool
) -> str:
    """The sentence that names the states outside the list that incidents name.

    Args:
        outside: The memberships of the states outside the list.
        selected: The codes of the Countries filter.
        on_list_empty: True when no state of the list is counted.

    Returns:
        The sentence, or "" when the incidents name no such state.
    """
    counts = count_by_country(outside).sort_values(
        ["events", "country"], ascending=[False, True], kind="stable"
    )
    if counts.empty:
        return ""
    named = ", ".join(
        f"{name} ({n:,})" for name, n in zip(counts["country"], counts["events"])
    )
    start = (
        "The Countries filter selects states outside the list"
        if selected and on_list_empty
        else "The incidents also name states outside the list"
    )
    return f"{start}, not counted here: {named}."


def country_only_caption(
    total: int, bars: CountryOnly, selected: Collection[str]
) -> str:
    """The caption of the bars: its limit, then the sentences that apply.

    Args:
        total: The incidents with a country and no point.
        bars: The counts from `country_only`.
        selected: The codes of the Countries filter.
    """
    text = (
        "Incidents with a country and no point on the map, states of the European "
        "list only. An incident counts once in each country and each domain that "
        "its sources give it."
    )
    added = int((bars.counts["solid"] + bars.counts["hatched"]).sum())
    if added > total:
        text += f" The bars add up to {added:,}, more than the {total:,} incidents."
    if bars.several:
        text += (
            f" {bars.several:,} of these {total:,} incidents name several "
            "countries: their parts are hatched."
        )
    note = outside_note(bars.outside, selected, bars.counts.empty)
    return f"{text} {note}".strip()


def country_only_chart(
    counts: pd.DataFrame, colors: dict[str, str], surface: str
) -> go.Figure:
    """Stacked bars by domain for each country, hatched where several countries.

    Args:
        counts: `CountryOnly.counts`, with at least one line.
        colors: The colour of each domain label.
        surface: The page colour, for the edges and the hatch.
    """
    counts = counts.assign(
        country=counts["code"].map(country_name),
        total=counts["solid"] + counts["hatched"],
    )
    order = (
        counts.groupby("country")["total"]
        .sum()
        .reset_index()
        .sort_values(["total", "country"], ascending=[False, True], kind="stable")
    )
    fig = go.Figure()
    for domain in [d for d in colors if d in set(counts["domain"])]:
        own = counts[counts["domain"] == domain].set_index("country")
        own = own[["solid", "hatched", "total"]].reindex(order["country"]).fillna(0)
        texts = [
            f"{domain}<br>{int(row.total):,} "
            f"{'incident' if row.total == 1 else 'incidents'}: "
            f"{int(row.solid):,} name only {country}, "
            f"{int(row.hatched):,} also name other countries"
            for country, row in own.iterrows()
        ]
        for part, hatched in (("solid", False), ("hatched", True)):
            marker = {
                "color": colors[domain],
                "line": {"color": surface, "width": 1},
            }
            if hatched:
                marker["pattern"] = {
                    "shape": "/",
                    "bgcolor": colors[domain],
                    "fgcolor": surface,
                }
            fig.add_bar(
                x=own[part].astype(int).tolist(),
                y=list(own.index),
                orientation="h",
                name=domain,
                legendgroup=domain,
                showlegend=not hatched,
                marker=marker,
                customdata=texts,
                hovertemplate="%{customdata}<extra></extra>",
            )
    fig.add_bar(
        x=[None],
        y=[None],
        orientation="h",
        name="Hatched: also names other countries",
        legendgroup="hatch",
        marker={
            "color": "#898781",
            "pattern": {"shape": "/", "bgcolor": "#898781", "fgcolor": surface},
        },
    )
    fig.update_layout(
        barmode="stack",
        barcornerradius=3,
        xaxis={"type": "linear", "rangemode": "tozero", "title": "Incidents"},
        yaxis={
            "categoryorder": "array",
            "categoryarray": list(order["country"]),
            "autorange": "reversed",
            "title": "",
        },
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02, "x": 0},
        margin={"l": 0, "r": 30, "t": 10, "b": 0},
        height=30 * len(order) + 100,
    )
    return fig


def sources_table(
    events: pd.DataFrame,
    quality: pd.DataFrame | None,
    listed: list[str],
    context: dict[str, tuple[date, date] | None],
) -> pd.DataFrame:
    """One row per listed source. A context source has no event: its Events cell
    is empty, and its From and To are the first and the newest day of its table,
    empty when its month files cannot be read. Events stays a number column."""
    dates = latest_dates(quality)
    rows = []
    for name in listed:
        source = SOURCES[name]
        own = events[events["source"] == name]
        if source.table == "context":
            count, (first, last) = None, context.get(name) or (None, None)
        else:
            count = len(own)
            first, last = own["date"].min().date(), own["date"].max().date()
        rows.append(
            {
                "Source": source.title,
                "Update": "Daily refresh"
                if source.kind == "live"
                else "Frozen snapshot",
                "Events": count,
                "Downloaded": dates.get(name, ("", ""))[0],
                "Updated by source": dates.get(name, ("", ""))[1],
                "From": first,
                "To": last,
                "Licence": source.licence,
                "Link": source.homepage,
            }
        )
    table = pd.DataFrame(rows)
    return table.astype({"Events": "Int64"}) if rows else table


st.set_page_config(page_title="Europe Hybrid Incidents Tracker", layout="wide")

dark = st.context.theme.type == "dark"
surface = "#0e1117" if dark else "#ffffff"
colors = dict(zip(DOMAIN_LABELS.values(), DARK_COLORS if dark else LIGHT_COLORS))

all_events, data_revision, data_date, hub_failure, quality, context = load_events(
    configured_revision()
)
published = published_sources(all_events, list(SOURCES))
listed_sources = published_sources(all_events, list(SOURCES), context)

st.title("Europe Hybrid Incidents Tracker")
st.caption(
    "Drone incidents, sabotage and other hybrid operations in Europe since 2022, "
    "compiled from open datasets. A reported incident is not a confirmed one, "
    "and a confirmed one is not an attribution. An attribution is a statement "
    "of the source named in each event, not an established fact: use the filters."
)


def select_domain_categories():
    """Select every category of the chosen domains: runs when the Domain changes."""
    st.session_state["categories"] = [
        c for c in CATEGORIES if CATEGORY_DOMAINS[c] in st.session_state["domains"]
    ]


with st.sidebar:
    st.header("Filters")
    period_label = st.selectbox("Period", list(PERIODS), key="period")
    period_note = st.container()
    source_names = st.multiselect(
        "Sources",
        published,
        default=published,
        format_func=lambda name: SOURCES[name].title,
        key="sources",
    )
    domains = st.multiselect(
        "Domain",
        DOMAINS,
        default=DOMAINS,
        format_func=lambda key: DOMAIN_LABELS[key],
        on_change=select_domain_categories,
        key="domains",
    )
    category_options = [c for c in CATEGORIES if CATEGORY_DOMAINS[c] in domains]
    if "categories" not in st.session_state:
        st.session_state["categories"] = category_options
    categories = st.multiselect(
        "Category",
        category_options,
        format_func=lambda key: CATEGORY_LABELS[key],
        key="categories",
    )
    actor_options = sorted(
        {code for codes in all_events["actor_codes"] for code in codes},
        key=country_name,
    )
    actors = st.multiselect(
        "Actor",
        actor_options,
        format_func=country_name,
        placeholder="All events",
        help="A state that a source links to the event. Empty keeps every event, "
        "also those with no state named.",
        key="actors",
    )
    attributions = st.multiselect(
        "Attribution",
        ATTRIBUTIONS,
        default=ATTRIBUTIONS,
        format_func=lambda key: ATTRIBUTION_LABELS[key],
        help="Who links the event to the actor. Every level is shown by default.",
        key="attributions",
    )
    statuses = st.multiselect(
        "Event status",
        EVENT_STATUSES,
        default=["confirmed", "unconfirmed"],
        format_func=lambda key: EVENT_STATUS_LABELS[key],
        help=(
            "Dismissed events (a source or an authority denies that the "
            "incident happened) are hidden by default."
        ),
        key="statuses",
    )
    country_options = sorted(
        {code for codes in all_events["country_codes"] for code in codes if code},
        key=country_name,
    )
    countries = st.multiselect(
        "Countries",
        country_options,
        format_func=country_name,
        placeholder="All countries",
        key="countries",
    )

scope = scope_rows(all_events, sources=source_names, order=published)
# Every filter except the period: used for the "last 30 days" figure.
passing = passing_rows(
    scope,
    domains=domains,
    categories=categories,
    statuses=statuses,
    attributions=attributions,
    actors=actors,
    countries=countries,
)

today = utc_today()
days = PERIODS[period_label]
line_dates = passing.drop_duplicates("cluster_id")["line_date"]
month_lines = incident_lines(scope, passing, fields=[], names=SOURCE_NAMES)
month_sources = month_lines.loc[
    month_lines["date_precision"].isin(["month", "year"]), "line_source"
].unique()
if days is not None and len(month_sources):
    titles = ", ".join(SOURCES[name].title for name in SOURCES if name in month_sources)
    period_note.caption(
        f"Some dates are known to the month or year only ({titles}): the event "
        "is dated the 1st of the month the source gives, or 1 January when it "
        "gives the year only. Such an event counts in a period only if that "
        "date falls inside it, so an event of the month that contains the "
        "period start can be left out."
    )
rows = (
    passing
    if days is None
    else passing[passing["line_date"] > today - pd.Timedelta(days=days)]
)

if rows.empty:
    st.info("No event matches these filters.")
    st.stop()

lines = incident_lines(scope, rows, fields=LINE_FIELDS, names=SOURCE_NAMES).sort_values(
    "date", ascending=False, kind="stable"
)
listed = scope[scope["cluster_id"].isin(lines["cluster_id"])]
link_sources = [name for name in published if name in source_names]
for name in link_sources:
    own = listed[listed["source"] == name].drop_duplicates("cluster_id")
    links = own.set_index("cluster_id")["url"].to_dict()
    lines[f"link_{name}"] = [links.get(cluster, "") for cluster in lines["cluster_id"]]
in_domain = memberships(rows, "domain_label")
in_list, outside = split_by_scope(memberships(rows, "country_codes"), countries)
by_country = count_by_country(in_list)
countries_help = (
    f"{COUNTRIES_HELP} {outside_note(outside, countries, by_country.empty)}".strip()
)
last_30 = (line_dates > today - pd.Timedelta(days=30)).sum()
previous_30 = (
    (line_dates > today - pd.Timedelta(days=60))
    & (line_dates <= today - pd.Timedelta(days=30))
).sum()

kpi_events, kpi_countries, kpi_recent, kpi_official = st.columns(4)
kpi_events.metric(
    "Incidents",
    f"{len(lines):,}",
    help="Events that more than one source reports count once.",
)
kpi_countries.metric("Countries", len(by_country), help=countries_help)
kpi_recent.metric(
    "Last 30 days",
    int(last_30),
    delta=int(last_30 - previous_30),
    delta_color="inverse",
    help="Compared with the 30 days before. Ignores the period filter.",
)
official = official_by_actor(rows)
if official:
    official_help = "Events a public authority attributed to a state, by state: " + (
        ", ".join(f"{country_name(code)} {n:,}" for code, n in official.items()) + "."
    )
else:
    official_help = "No event of this selection has an official attribution."
kpi_official.metric(
    "Officially attributed",
    int(rows.loc[rows["attribution"] == "official", "cluster_id"].nunique()),
    help=official_help + " An attribution is a statement of its author, not a fact.",
)

weekly = days is not None and days <= 90
st.subheader(f"Events per {'week' if weekly else 'month'}")
st.plotly_chart(timeline_chart(in_domain, colors, weekly, surface))
st.caption(
    "Counts follow source coverage, not only reality: each source covers its own "
    "period (see the Sources table). An incident counts in each domain that one "
    "of its sources gives it, so the counts by domain can add up to more than "
    "the number of incidents."
)

st.subheader("Where")
map_view = st.segmented_control(
    "Map view",
    ["Incidents", "Countries"],
    default="Incidents",
    label_visibility="collapsed",
)
located = lines.dropna(subset=["lat", "lon"])
coverage = coordinate_coverage(listed)
split = where_split(lines)
without_titles = ", ".join(SOURCES[name].title for name in coverage.sources_without)
if map_view == "Countries":
    st.plotly_chart(countries_map(by_country, dark))
    st.caption(
        "Incidents per country, states of the European list only. An incident "
        "counts once in each country that one of its sources gives it."
    )
else:
    bars = country_only(rows, lines, countries)
    st.caption(
        split_line(
            split, countries_pointer="bars below" if not bars.counts.empty else None
        )
    )
    if located.empty:
        st.info(
            "None of the selected events has coordinates. Sources with no "
            f"coordinates: {without_titles}."
        )
    else:
        stacked = int(located.duplicated(["lat", "lon"], keep=False).sum())
        if stacked:
            stacked_note = (
                f" {stacked:,} of the incidents on the map share their place with "
                "other events: hover over a marker to see how many are there. "
                "The Events table lists them all."
            )
        else:
            stacked_note = ""
        with_titles = ", ".join(SOURCES[name].title for name in coverage.sources_with)
        sources_note = f" Sources with coordinates: {with_titles}."
        if without_titles:
            sources_note += f" Sources with no coordinates: {without_titles}."
        st.plotly_chart(points_map(located, in_domain, colors, dark))
        st.caption(
            stacked_note.strip()
            + sources_note
            + " An event has one marker for each domain that one of its sources "
            "gives it, at the same place. The legend lists only the domains of the "
            "events that have coordinates: it is not the Domain filter."
        )
    if split.countries_only:
        bars_title = (
            f"{split.countries_only:,} "
            f"{'incident' if split.countries_only == 1 else 'incidents'} "
            "in a country only (no point on the map)"
        )
        st.markdown(f"##### {bars_title}")
        if not bars.counts.empty:
            st.plotly_chart(country_only_chart(bars.counts, colors, surface))
        st.caption(country_only_caption(split.countries_only, bars, countries))
    if split.no_country:
        st.caption(no_country_line(split, no_country_places(lines)))

st.subheader("Most affected countries (top 15, ties kept)")
st.plotly_chart(countries_bar(by_country))
st.caption(
    "Counts every selected incident that names the country, with or without a "
    "point on the map."
)

st.subheader("Events")
st.caption(
    "One line for each incident. A value that the sources do not share is "
    "shown with the name of each source. The title, the dates and the place "
    "are those of the first source in the list of sources."
)
st.dataframe(
    lines[
        [
            "date",
            "end_date",
            "country_names",
            "domain_label",
            "category_label",
            "title",
            "status_label",
            "drone_presence_label",
            "attribution_label",
            "actor_names",
            "attributed_by_label",
            *[f"link_{name}" for name in link_sources],
        ]
    ],
    hide_index=True,
    column_config={
        "date": st.column_config.DateColumn("Date", format="YYYY-MM-DD"),
        "end_date": st.column_config.DateColumn("End date", format="YYYY-MM-DD"),
        "country_names": "Country",
        "domain_label": "Domain",
        "category_label": "Category",
        "title": st.column_config.TextColumn("What", width="large"),
        "status_label": "Status",
        "drone_presence_label": "Drone presence",
        "attribution_label": "Attribution",
        "actor_names": "Actors",
        "attributed_by_label": "Attributed by",
        **{
            f"link_{name}": st.column_config.LinkColumn(
                SOURCE_NAMES[name], display_text="Open"
            )
            for name in link_sources
        },
    },
)
st.download_button(
    "Download selection (CSV)",
    data=listed.assign(
        date=listed["date"].dt.strftime("%Y-%m-%d"),
        end_date=listed["end_date"].dt.strftime("%Y-%m-%d"),
    )[PUBLISHED_COLUMNS].to_csv(index=False),
    file_name="hybrid_incidents.csv",
    mime="text/csv",
)

st.subheader("Sources")
st.dataframe(
    sources_table(all_events, quality, listed_sources, context),
    # A missing value shows as a blank cell, not "None", and Events stays a
    # number column, so it sorts as numbers.
    placeholder="",
    hide_index=True,
    column_config={
        "Downloaded": st.column_config.TextColumn(
            "Downloaded",
            help="Time of the download whose data this table shows.",
        ),
        "Updated by source": st.column_config.TextColumn(
            "Updated by source",
            help=(
                "Date of the last change that the source gives. "
                "Blank: the source gives no date."
            ),
        ),
        "Link": st.column_config.LinkColumn("Link"),
    },
)
st.caption(
    " ".join(
        f"{SOURCES[name].citation} {SOURCES[name].licence}." for name in listed_sources
    )
)
revision_note = f"revision {data_revision[:7]}" + (
    f" of {data_date}" if data_date else ""
)
if hub_failure:
    cause = (
        "The Hub could not be reached"
        if hub_failure == UNREACHABLE
        else f"The data could not be read at revision {configured_revision()}"
    )
    st.caption(
        f"{cause}: showing the snapshot bundled with the app "
        f"({revision_note}). The data may be older than the published tables."
    )
else:
    st.caption(f"Data: {REPO_ID} on Hugging Face, {revision_note}.")
