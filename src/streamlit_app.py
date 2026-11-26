"""Dashboard: maps and charts of the unified hybrid-incident dataset.

Run from the repository root: streamlit run src/streamlit_app.py
"""

from functools import partial

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
    coordinate_coverage,
    filter_events,
    official_by_actor,
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


@st.cache_data(ttl="1h", max_entries=2)
def load_events(revision: str) -> tuple[pd.DataFrame, str, str, str]:
    """Load and prepare the published events: the Hub is read once an hour.

    Returns:
        The events, the revision read, its date and whether the bundled
        snapshot was used because the Hub could not be read.
    """
    published = load_published(revision, snapshot_dir())
    events = published.events
    events["date"] = pd.to_datetime(events["date"])
    events["end_date"] = pd.to_datetime(events["end_date"])
    events["domain_label"] = events["domain"].map(DOMAIN_LABELS)
    events["category_label"] = events["category"].map(CATEGORY_LABELS)
    events["attribution_label"] = events["attribution"].map(ATTRIBUTION_LABELS)
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
    return (
        with_code_lists(with_clusters(events)),
        published.revision,
        published.date,
        published.failure,
    )


def country_name(code: str) -> str:
    country = pycountry.countries.get(alpha_2=code)
    if country is None:
        return code
    return getattr(country, "common_name", country.name)


def country_iso3(code: str) -> str | None:
    country = pycountry.countries.get(alpha_2=code)
    return country.alpha_3 if country else None


def count_by_country(events: pd.DataFrame) -> pd.DataFrame:
    """Events per country. An event spanning two countries counts in both."""
    codes = events["country_codes"].explode()
    counts = (
        codes[codes != ""].value_counts().rename_axis("code").reset_index(name="events")
    )
    counts["country"] = counts["code"].map(country_name)
    counts["iso3"] = counts["code"].map(country_iso3)
    return counts


def timeline_chart(
    events: pd.DataFrame, colors: dict[str, str], weekly: bool, surface: str
) -> go.Figure:
    period = events["date"].dt.to_period("W" if weekly else "M").dt.start_time
    counts = (
        events.groupby([period.rename("period"), "domain_label"])
        .size()
        .reset_index(name="events")
    )
    fig = px.bar(
        counts,
        x="period",
        y="events",
        color="domain_label",
        category_orders={"domain_label": list(colors)},
        color_discrete_map=colors,
        labels={"period": "", "events": "Events", "domain_label": ""},
    )
    fig.update_traces(marker_line_color=surface, marker_line_width=1)
    fig.update_layout(
        barcornerradius=3,
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02, "x": 0},
        margin={"l": 0, "r": 0, "t": 10, "b": 0},
        height=420,
    )
    return fig


def points_map(located: pd.DataFrame, colors: dict[str, str], dark: bool) -> go.Figure:
    located = located.assign(
        day=located["date"].dt.strftime("%Y-%m-%d"),
        here=located.groupby(["lat", "lon"])["lat"].transform("size"),
    )
    fig = px.scatter_map(
        located,
        lat="lat",
        lon="lon",
        color="domain_label",
        hover_name="title",
        hover_data={
            "day": True,
            "place": True,
            "category_label": True,
            "attribution_label": True,
            "attributed_by_label": True,
            "actor_names": True,
            "here": True,
            "lat": False,
            "lon": False,
            "domain_label": False,
        },
        category_orders={"domain_label": list(colors)},
        color_discrete_map=colors,
        labels={
            "domain_label": "",
            "day": "Date",
            "place": "Place",
            "category_label": "Category",
            "attribution_label": "Attribution",
            "attributed_by_label": "Attributed by",
            "actor_names": "Actors",
            "here": "Events here",
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


def sources_table(events: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for name, source in SOURCES.items():
        own = events[events["source"] == name]
        rows.append(
            {
                "Source": source.title,
                "Update": "Daily refresh"
                if source.kind == "live"
                else "Frozen snapshot",
                "Events": len(own),
                "From": own["date"].min().date(),
                "To": own["date"].max().date(),
                "Licence": source.licence,
                "Link": source.homepage,
            }
        )
    return pd.DataFrame(rows)


st.set_page_config(page_title="Europe Hybrid Incidents Tracker", layout="wide")

dark = st.context.theme.type == "dark"
surface = "#0e1117" if dark else "#ffffff"
colors = dict(zip(DOMAIN_LABELS.values(), DARK_COLORS if dark else LIGHT_COLORS))

all_events, data_revision, data_date, hub_failure = load_events(configured_revision())

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
        list(SOURCES),
        default=list(SOURCES),
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
        help="Dismissed events (the source found no incident) are hidden by default.",
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

select = partial(
    filter_events,
    all_events,
    sources=source_names,
    domains=domains,
    categories=categories,
    statuses=statuses,
    attributions=attributions,
    actors=actors,
    countries=countries,
)
# Every filter except the period: used for the "last 30 days" figure.
scoped = select()

today = utc_today()
days = PERIODS[period_label]
month_sources = scoped.loc[
    scoped["date_precision"].isin(["month", "year"]), "source"
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
events = scoped if days is None else select(after=today - pd.Timedelta(days=days))

if events.empty:
    st.info("No event matches these filters.")
    st.stop()

by_country = count_by_country(events)
if countries:
    by_country = by_country[by_country["code"].isin(countries)]
last_30 = (scoped["date"] > today - pd.Timedelta(days=30)).sum()
previous_30 = (
    (scoped["date"] > today - pd.Timedelta(days=60))
    & (scoped["date"] <= today - pd.Timedelta(days=30))
).sum()

kpi_events, kpi_countries, kpi_recent, kpi_official = st.columns(4)
kpi_events.metric(
    "Incidents",
    f"{len(events):,}",
    help="Events that more than one source reports count once.",
)
kpi_countries.metric("Countries", len(by_country))
kpi_recent.metric(
    "Last 30 days",
    int(last_30),
    delta=int(last_30 - previous_30),
    delta_color="inverse",
    help="Compared with the 30 days before. Ignores the period filter.",
)
official = official_by_actor(events)
if official:
    official_help = "Events a public authority attributed to a state, by state: " + (
        ", ".join(f"{country_name(code)} {n:,}" for code, n in official.items()) + "."
    )
else:
    official_help = "No event of this selection has an official attribution."
kpi_official.metric(
    "Officially attributed",
    int((events["attribution"] == "official").sum()),
    help=official_help + " An attribution is a statement of its author, not a fact.",
)

weekly = days is not None and days <= 90
st.subheader(f"Events per {'week' if weekly else 'month'}")
st.plotly_chart(timeline_chart(events, colors, weekly, surface))
st.caption(
    "Counts follow source coverage, not only reality: each source covers its own "
    "period (see the Sources table)."
)

st.subheader("Where")
map_view = st.segmented_control(
    "Map view",
    ["Incidents", "Countries"],
    default="Incidents",
    label_visibility="collapsed",
)
located = events.dropna(subset=["lat", "lon"])
coverage = coordinate_coverage(events)
on_no_map = coverage.on_no_map
if on_no_map == 1:
    no_map_note = " 1 event has no country (open sea) and is on no map."
elif on_no_map:
    no_map_note = f" {on_no_map:,} events have no country (open sea) and are on no map."
else:
    no_map_note = ""
without_titles = ", ".join(SOURCES[name].title for name in coverage.sources_without)
if coverage.countries_only == 1:
    countries_only_note = (
        " 1 event has a country and no coordinates: it is in the Countries view only."
    )
elif coverage.countries_only:
    countries_only_note = (
        f" {coverage.countries_only:,} events have a country and no coordinates: "
        "they are in the Countries view only."
    )
else:
    countries_only_note = ""
if map_view == "Countries":
    st.plotly_chart(countries_map(by_country, dark))
    st.caption(
        "Events per country, Europe only. An event spanning several countries "
        "counts once in each country shown." + no_map_note
    )
elif located.empty:
    st.info(
        "None of the selected events has coordinates. Sources with no coordinates: "
        f"{without_titles}."
        + countries_only_note
        + " Try the Countries view."
        + no_map_note
    )
else:
    stacked = int(located.duplicated(["lat", "lon"], keep=False).sum())
    if stacked:
        stacked_note = (
            f" {stacked:,} of them share their place with other events: "
            "hover over a marker to see how many are there. "
            "The Events table lists them all."
        )
    else:
        stacked_note = ""
    if len(located) == 1:
        located_note = f"1 of {len(events):,} selected events has coordinates."
    else:
        located_note = (
            f"{len(located):,} of {len(events):,} selected events have coordinates."
        )
    with_titles = ", ".join(SOURCES[name].title for name in coverage.sources_with)
    sources_note = f" Sources with coordinates: {with_titles}."
    if without_titles:
        sources_note += f" Sources with no coordinates: {without_titles}."
    st.plotly_chart(points_map(located, colors, dark))
    st.caption(
        located_note
        + stacked_note
        + sources_note
        + " The legend lists only the domains of the events that have "
        "coordinates: it is not the Domain filter." + countries_only_note + no_map_note
    )

st.subheader("Most affected countries (top 15, ties kept)")
st.plotly_chart(countries_bar(by_country))

st.subheader("Events")
table = events.sort_values("date", ascending=False).assign(
    Country=lambda frame: frame["country_codes"].map(
        lambda codes: ", ".join(country_name(code) for code in codes if code)
    ),
    Status=lambda frame: frame["event_status"].map(EVENT_STATUS_LABELS),
)
st.dataframe(
    table[
        [
            "date",
            "end_date",
            "Country",
            "domain_label",
            "category_label",
            "title",
            "Status",
            "attribution_label",
            "actor_names",
            "attributed_by",
            "url",
        ]
    ],
    hide_index=True,
    column_config={
        "date": st.column_config.DateColumn("Date", format="YYYY-MM-DD"),
        "end_date": st.column_config.DateColumn("End date", format="YYYY-MM-DD"),
        "domain_label": "Domain",
        "category_label": "Category",
        "title": st.column_config.TextColumn("What", width="large"),
        "attribution_label": "Attribution",
        "actor_names": "Actors",
        "attributed_by": "Attributed by",
        "url": st.column_config.LinkColumn("Details", display_text="Open"),
    },
)
st.download_button(
    "Download selection (CSV)",
    data=events.assign(
        date=events["date"].dt.strftime("%Y-%m-%d"),
        end_date=events["end_date"].dt.strftime("%Y-%m-%d"),
    )[PUBLISHED_COLUMNS].to_csv(index=False),
    file_name="hybrid_incidents.csv",
    mime="text/csv",
)

st.subheader("Sources")
st.dataframe(
    sources_table(all_events),
    hide_index=True,
    column_config={"Link": st.column_config.LinkColumn("Link")},
)
st.caption(
    " ".join(f"{source.citation} {source.licence}." for source in SOURCES.values())
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
