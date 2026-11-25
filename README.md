# Europe Hybrid Incidents Tracker

[![pipeline](https://github.com/tryph0n/hybrid-tracker/actions/workflows/pipeline.yml/badge.svg)](https://github.com/tryph0n/hybrid-tracker/actions/workflows/pipeline.yml)

Maps and charts of drone incidents, sabotage and other hybrid operations in
Europe since 2022, from open datasets.

- Dashboard: [hybrid-tracker.onrender.com](https://hybrid-tracker.onrender.com).
  It sleeps when nobody visits: the first load can take about a minute.
- Data: [sam-bot/europe-hybrid-incidents](https://huggingface.co/datasets/sam-bot/europe-hybrid-incidents)
  on Hugging Face, or the [CSV files](https://github.com/tryph0n/hybrid-tracker/tree/main/data/events).
  CC BY 4.0.
- Documentation: [hybrid-tracker.readthedocs.io](https://hybrid-tracker.readthedocs.io/):
  data dictionary, architecture, maintainer guide, API.
- Code: [github.com/tryph0n/hybrid-tracker](https://github.com/tryph0n/hybrid-tracker),
  Apache 2.0.

## Data

One CSV file per source, all with the same columns.

| Source | Content | Updates | Licence |
|---|---|---|---|
| [European Observatory of Drone Incidents](https://droneobservatory.eu) | Drone incidents in Europe | Daily refresh | CC BY 4.0 |
| [Russian Operations Against Europe Dataset, 2022-2025](https://doi.org/10.7910/DVN/TQ0FMQ) | Operations that the author judges Russian | Frozen snapshot | CC BY 4.0 |

Each day at 04:17 UTC, the pipeline downloads each daily source again, checks every row,
commits the files, then updates the dashboard and the dataset. See the
[architecture page](https://hybrid-tracker.readthedocs.io/en/latest/architecture.html).

## Reading the data

A reported incident is not a confirmed one, and a confirmed one is not an
attribution. Two fields keep these questions apart:

- `event_status`: is the event itself established (`confirmed`, `unconfirmed`,
  `dismissed`)?
- `russia_link`: who links it to Russia (`official` authority, `assessed` by
  the source's analysts, `none`)?

The dashboard hides `dismissed` events by default: add "Dismissed by the
source" to the Event status filter to show them. The Sources table counts all
stored events, whatever the filters.

Known limits: sources are not de-duplicated against each other, and counts
over time follow each source's coverage period (see the Sources table of the
dashboard).

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

## Licence and credits

Code: Copyright 2026 Samy Antoine SAAD, licensed under the Apache License,
Version 2.0 (see [LICENSE](https://github.com/tryph0n/hybrid-tracker/blob/main/LICENSE)).

Data in `data/events/`: derived from the sources below and distributed under
CC BY 4.0. Changes: the events of each source are mapped to one schema. Reuse
must credit the original sources:

- Lucas Alaniz Pintos, European Observatory of Drone Incidents (EODI): drone
  incidents in Europe. CC BY 4.0.
- Bart Schuurman, Russian Operations Against Europe Dataset, 2022-2025, Harvard
  Dataverse, V4. CC BY 4.0.
