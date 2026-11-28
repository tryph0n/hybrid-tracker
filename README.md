# Europe Hybrid Incidents Tracker

[![pipeline](https://github.com/tryph0n/hybrid-tracker/actions/workflows/pipeline.yml/badge.svg)](https://github.com/tryph0n/hybrid-tracker/actions/workflows/pipeline.yml)

Maps and charts of drone incidents, sabotage and other hybrid operations in
Europe since 2022, from open datasets.

- Dashboard: [hybrid-tracker.onrender.com](https://hybrid-tracker.onrender.com).
  It sleeps when nobody visits: the first load can take about a minute.
- Data: [sam-bot/europe-hybrid-incidents](https://huggingface.co/datasets/sam-bot/europe-hybrid-incidents)
  on Hugging Face (CC BY 4.0), and
  [sam-bot/europe-hybrid-incidents-nc](https://huggingface.co/datasets/sam-bot/europe-hybrid-incidents-nc)
  (NonCommercial, CC BY-NC 4.0).
- Documentation: [hybrid-tracker.readthedocs.io](https://hybrid-tracker.readthedocs.io/):
  data dictionary, architecture, maintainer guide, API.
- Code: [github.com/tryph0n/hybrid-tracker](https://github.com/tryph0n/hybrid-tracker),
  Apache 2.0.

## Data

The events come from the event sources, all with the same columns. Stanford gives a
context table, in the NonCommercial dataset only.

| Source | Content | Updates | Licence |
|---|---|---|---|
| [European Observatory of Drone Incidents](https://droneobservatory.eu) | Drone incidents in Europe | Daily refresh | CC BY 4.0 |
| [Russian Operations Against Europe Dataset, 2022-2025](https://doi.org/10.7910/DVN/TQ0FMQ) | Operations that the author judges Russian | Frozen snapshot | CC BY 4.0 |
| [Stanford GNSS interference](https://rfi.stanford.edu) | Daily share of aircraft with a low navigation integrity category, by H3 cell (a context table, in the NonCommercial dataset only). Source: Stanford GPS Lab, rfi.stanford.edu. | Daily refresh | CC BY-NC 4.0 |

Each day at 04:17 UTC, the pipeline downloads each daily source again, checks every row,
publishes the two datasets, then updates the dashboard. See the
[architecture page](https://hybrid-tracker.readthedocs.io/en/latest/architecture.html).

## Reading the data

A reported incident is not a confirmed one, and a confirmed one is not an
attribution. Four fields keep these questions apart:

- `event_status`: is the event itself established (`confirmed`, `unconfirmed`,
  `dismissed`)? For EODI it comes from the field `estado`.
- `drone_presence`: does an authority attribute the incident to a drone
  (`confirmed`, `not_confirmed`, `not_substantiated`, `ruled_out`)? It is a fact
  apart from `event_status`, and it is empty for a source with no such field.
- `actors`: which states a source links to the event (ISO codes joined by ";").
  It is empty when nobody attributes the event.
- `attribution`: how strong that link is (`official` authority, `assessed` by
  the source's analysts, `claimed` by a group, `none`). `attributed_by` names
  who made the link.

`domain` groups the events (air, sabotage, violence, maritime, cyber,
information, espionage, other), and `category` refines it.

The data has a version, 2.7.0 today. The first version (1.0.0) used other
column names. It stays readable at the tag `v1.0.0` of the dataset, for example
`pd.read_csv("hf://datasets/sam-bot/europe-hybrid-incidents@v1.0.0/eodi.csv")`.
See the
[`Versions` section](https://github.com/tryph0n/hybrid-tracker/blob/main/cards/versions-open.md)
of the dataset card (its source is `cards/versions-open.md`) for the mapping.

The dashboard hides `dismissed` events by default: add "Dismissed by the
source" to the Event status filter to show them. The Sources table counts all
stored events, whatever the filters.

Known limits: counts are de-duplicated across sources only, not inside one
source ([#147](https://github.com/tryph0n/hybrid-tracker/issues/147)), and
counts over time follow each source's coverage period (see the Sources table of the
dashboard). Some sources give a date to the month or year only (`date_precision` is
`month` or, since version 2.1.0, `year`) and the event is dated the 1st of the
month the source gives, or 1 January when the source gives the year only. The period filter places an
event by that date, so for a period other than "All time" an event of the
month that contains the period start can be left out, even if it took place
inside the period. The dashboard shows a note when this can apply.

All columns and values are in the
[data dictionary](https://hybrid-tracker.readthedocs.io/en/latest/data-dictionary.html).

## Run locally

With Docker:

```bash
docker run -p 8501:8501 sambot961/hybrid-tracker
```

Then open http://localhost:8501.

With Python 3.13:

```bash
uv sync --locked --no-dev
uv run --no-dev streamlit run src/streamlit_app.py
```

## Contributing

Issues are welcome. Pull requests are not accepted.

To report a wrong mapping, such as a category, a country or a status that does
not match the source, [open an issue](https://github.com/tryph0n/hybrid-tracker/issues).
Report an error in the source data to that source. For a daily source, the
next refresh brings the fix. A frozen snapshot changes only with a new version
of the source.

The [maintainer guide](https://hybrid-tracker.readthedocs.io/en/latest/maintainer.html)
covers the checks, the pipeline and the settings.

To report a vulnerability, read the [security policy](https://github.com/tryph0n/hybrid-tracker/blob/main/SECURITY.md).

## Licence and credits

Code: Copyright 2026 Samy Antoine SAAD, licensed under the Apache License,
Version 2.0 (see [LICENSE](https://github.com/tryph0n/hybrid-tracker/blob/main/LICENSE)).

Data in the two public datasets: derived from the sources below and distributed
under their licences (CC BY 4.0, or CC BY-NC 4.0 for the NonCommercial dataset). Changes: the events of each source are mapped to one schema. Reuse
must credit the original sources:

- Lucas Alaniz Pintos, European Observatory of Drone Incidents (EODI): drone
  incidents in Europe. CC BY 4.0.
- Bart Schuurman, Russian Operations Against Europe Dataset, 2022-2025, Harvard
  Dataverse, V4. CC BY 4.0.

Approximate coordinates: an event that has a place and no coordinates gets those of
the town of that name in its country, with `geo_precision` `place`. The town
centre comes from GeoNames, which is not an event source. Changes: the file is
filtered to populated places with at least 500 inhabitants (`cities500`), and
matched to events by name and country. Reuse must credit it:

- GeoNames, https://www.geonames.org: place names and coordinates, filtered to
  populated places. Provided as is, without warranty. CC BY 4.0
  ([licence](https://creativecommons.org/licenses/by/4.0/)).

Threat groups: the open dataset has a `threat_actors` table that turns the
names of a threat group into one canonical name and a country of origin. It
comes from the MISP galaxy threat-actor cluster at a pinned commit (dual
licence: CC0 1.0 or BSD 2-clause, used here under CC0 1.0). Changes: the cluster
is reduced to one row per group, and `curation/actors.csv` adds a country to
groups that the cluster leaves without one, each with two public pages and
their exact words (the card of the dataset lists them). The
country is the origin of the group, not proof of state sponsorship. Credit:

- MISP galaxy (MISP Project / CIRCL), threat-actor cluster: names, aliases and
  countries of threat groups, https://github.com/MISP/misp-galaxy. CC0 1.0
  ([licence](https://creativecommons.org/publicdomain/zero/1.0/)).
