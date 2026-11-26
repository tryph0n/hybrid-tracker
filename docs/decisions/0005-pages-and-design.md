# 0005. Front end, pages and design rules

Status: Proposed.
Date: 2026-11-26.

## Context

The dashboard grows from one page to seven pages in two languages, with a
static front page. One written record fixes the rules first. Then each page
follows the same design, and the front end can be replaced later.

Facts that the decision depends on:

- A free Render service stops after 15 minutes without traffic and needs about
  one minute to restart ([Render docs](https://render.com/docs/free)).
- Streamlit serves a fixed `index.html` with the title "Streamlit".
  `st.set_page_config` cannot set a description or Open Graph tags. The
  requests are open
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
| A server that sleeps on free hosting | A free Render service stops after 15 minutes without traffic and needs about one minute to restart. | The wake request: the front page wakes the dashboard, is monitored and links to it (#77). |
| No link previews | Streamlit serves a fixed `index.html` with the title "Streamlit". `st.set_page_config` cannot set a description or Open Graph tags. | The static front page, built by the data run (#73), carries the title, the description and the tags. |

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

Each page answers one question, with its own charts and filters. The ticket of
the page sets them.

| Page | Ticket |
| --- | --- |
| Overview | #65 |
| Map | #66 |
| Actors | #101 |
| Countries | #67 |
| Events | #68 |
| Data and method, and About | #102 |

### Design rules

- One fixed colour per domain, in light and dark. The colours are checked for
  colour-blind separation (the check of #36).
- At most eight series per chart.
- Every chart has a title that states the finding and a caption that states
  its limit. A count follows source coverage, and a reader must not take a
  chart for more than it shows.

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
- The first visit after a sleep takes about one minute. The front page covers
  it with the wake request.
- Shared links show no preview of the dashboard. They show the preview of the
  static front page.
- Review the decision if Streamlit must go: the fallback above applies.
