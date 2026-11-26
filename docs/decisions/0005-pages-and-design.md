# 0005. Front end, pages and design rules

Status: Accepted.
Date: 2026-11-26.

## Context

The dashboard grows from one page to seven pages in two languages, with a
static front page. One written record fixes the rules first. Then each page
follows the same design, and the front end can be replaced later.

Facts that the decision depends on:

- A free Render service stops after 15 minutes without traffic and needs about
  one minute to restart ([Render docs](https://render.com/docs/free)).
- Streamlit serves one fixed HTML shell, titled "Streamlit" in its
  [`index.html`](https://github.com/streamlit/streamlit/blob/1.67.0/frontend/app/index.html)
  (tag 1.67.0, the version in `uv.lock`). `st.set_page_config` cannot set a
  description or Open Graph tags. The requests are open
  ([#16634](https://github.com/streamlit/streamlit/issues/16634),
  [#6567](https://github.com/streamlit/streamlit/issues/6567)).

## Decision

We keep Streamlit as the front end, with the code arrangement, the languages,
the pages, the design rules and the wording rules below.

### Front end

Streamlit stays. Its ready-made filters, tables and controls are what data
exploration needs, and coding them by hand would rebuild what exists.

### Its two limits

| Limit | Fact | Cover |
| --- | --- | --- |
| A server that sleeps on free hosting | A free Render service stops after 15 minutes without traffic and needs about one minute to restart. | The static front page, built by the data run (#73), opens at once and shows the main figures while the service sleeps. The wake request (#77) starts the dashboard while the visitor reads, which shortens the wait. |
| No link previews | Streamlit serves one fixed HTML shell. `st.set_page_config` cannot set a description or Open Graph tags. | The static front page (#73) carries the link preview. |

The static front page covers both limits: it is instant and it carries the
preview. The wake request does not remove the wait: the first dashboard visit
can still wait while the service starts.

### Code arrangement

- Streamlit is imported only in `src/app/`, so that replacing it later stays
  simple.
- Data loading, filters, calculations, chart functions and translations live
  in `src/tracker/` and run without Streamlit.
- The state of a view (page, filters, language) is one small object that can
  be read from a URL.
- Fallback if Streamlit must go: a new front end on the same core. The static
  front page already proves that at each build.

### No layer over widgets

Pages are thin, not generic. A widget layer would rebuild a framework for a
need that may never come.

### Languages

English is the default. French comes through gettext catalogues, because
gettext is plain Python and works inside Streamlit.

### Pages

Each paragraph gives the question of the page, its charts and its filters, as
its ticket states them.

- Overview ([#65](https://github.com/tryph0n/hybrid-tracker/issues/65)): a
  visitor must understand the state of the data in ten seconds. Four headline
  figures, a timeline by domain, a small map and the ten latest events. The
  ticket states no filter of its own: filter values come from the shared view
  state, and the 30-day figure ignores the period filter.
- Map ([#66](https://github.com/tryph0n/hybrid-tracker/issues/66)): where did
  the events happen. A full-width map with two views, incidents and countries,
  and a period slider. Both views take the filters of the entry point.
- Actors ([#101](https://github.com/tryph0n/hybrid-tracker/issues/101)): what
  is linked to each state, and who made the link. One panel per state, each
  with a timeline, the domain mix and the countries targeted, split by
  attribution level. The panels follow the filters of the entry point.
- Countries ([#67](https://github.com/tryph0n/hybrid-tracker/issues/67)): how
  does one country compare with the others. For the chosen country: figures,
  timeline, domain mix and latest events. A ranking table shows events per
  million inhabitants beside the raw count. The filter is a country selector,
  driven by the URL.
- Events ([#68](https://github.com/tryph0n/hybrid-tracker/issues/68)): how to
  reach the events behind a figure, check the source and take the selection
  away. The ticket states no chart: a sortable table, a detail panel and a CSV
  download. Filters: a search box over title and place, and all the filters of
  the entry point.
- Data and method, and About
  ([#102](https://github.com/tryph0n/hybrid-tracker/issues/102)): where the
  data comes from, how fresh it is, and what is counted and what is not. A
  sources table and a chart of the period that each source covers. The ticket
  states no filter. The About page states the maintainer, the licences and how
  to contribute, with no chart and no filter.

### Design rules

- One fixed colour per domain, in light and dark. The colours are checked for
  colour-blind separation (the check of #36).
- At most eight series per chart.
- Every chart has a title that says what the chart counts, for example
  "Events per month, by domain", not a conclusion such as "Drone incidents
  are rising". Reason: the interface shows what the data records, not an
  interpretation.
- Every chart has a caption that states its limit. A count follows source
  coverage, and a reader must not take a chart for more than it shows.

### Wording rules for attribution

The interface never states that a state did something. It states who
attributed it. A wrong attribution harms people and the credibility of the
project.

## Alternatives rejected

- A static site generated in Python (Jinja2, Plotly). It is instant and free,
  but the filters, tables and controls would be coded by hand.
- Streamlit in the browser ([stlite](https://github.com/whitphx/stlite)). Each
  visitor's browser must download and start a Python runtime, which is heavy
  for phones.
- Quarto dashboards. They have no translation catalogues.
- Observable Framework and Evidence. They add a JavaScript or SQL codebase to
  maintain.

## Consequences

- Each page ticket follows the pages, the design rules and the wording rules
  of this record. The core modules without Streamlit (#76) follow the code
  arrangement.
- The first visit after a sleep takes about one minute. The static front page
  opens at once, and the wake request shortens that wait.
- The dashboard itself has no link preview. The static front page covers it.
- Review the decision if Streamlit must go: the fallback above applies.
