---
title: Europe Hybrid Incidents Tracker
colorFrom: blue
colorTo: gray
sdk: docker
app_port: 8501
pinned: false
short_description: Drone incidents and sabotage in Europe, from open data
---

# Europe Hybrid Incidents Tracker

Maps and charts of drone incidents, sabotage and other hybrid operations in
Europe since 2022, compiled from open datasets into one event table.

The block above this title is the Hugging Face Space configuration.

## Run locally

```bash
pip install -r requirements.txt
streamlit run src/streamlit_app.py
```

## Data

One CSV per source in `data/events/`, all with the same columns
(`src/tracker/schema.py`). The app reads these files and nothing else.

| Source | Kind | Refresh | Licence |
|---|---|---|---|
| [European Observatory of Drone Incidents](https://droneobservatory.eu) | live | daily, by the pipeline | CC BY 4.0 |
| [Russian Operations Against Europe Dataset, 2022-2025](https://doi.org/10.7910/DVN/TQ0FMQ) | static | by hand, on a new upstream version | CC BY 4.0 |

- **Static** sources are frozen historical snapshots, pinned to one upstream
  file version.
- **Live** sources are fetched in full at each run and replace the stored file.

```bash
export PYTHONPATH=src
python -m tracker.ingest live      # refresh every live source
python -m tracker.ingest static    # re-ingest every static source
python -m tracker.ingest check     # validate the stored files
```

Two fields keep separate questions apart:

- `event_status`: is the event itself established (`confirmed`, `unconfirmed`,
  `dismissed`)?
- `russia_link`: who links it to Russia (`official` authority, `assessed` by
  the source's analysts, `none`)?

Known limits: sources are not de-duplicated against each other, and counts
over time follow each source's coverage period.

## Adding a source

1. Write `src/tracker/<name>.py` with a `fetch()` returning the unified schema.
2. Register it in `src/tracker/sources.py` as `static` or `live`.
3. Run `python -m tracker.ingest <name>` and commit the CSV.

## Pipeline

`.github/workflows/pipeline.yml` runs on every push, and every day at 04:17 UTC:

1. lint and validate the stored data;
2. on the daily or a manual run: refresh live sources and commit the changes;
3. deploy the app and data to the Hugging Face Space.

Deployment needs, in the GitHub repository settings (Secrets and variables,
Actions):

- secret `HF_TOKEN`: a Hugging Face token with write access;
- variable `HF_SPACE_ID`: `<user>/<space-name>`.

The deploy step is skipped until `HF_SPACE_ID` is set.

## Licence

Code: Copyright 2026 Samy Antoine SAAD, licensed under the Apache License,
Version 2.0 (see `LICENSE`).

Data in `data/events/`: derived from the sources below and distributed under
CC BY 4.0. Reuse must credit the original sources.

## Credits

- Lucas Alaniz Pintos, European Observatory of Drone Incidents (EODI): drone
  incidents in Europe. CC BY 4.0.
- Bart Schuurman, Russian Operations Against Europe Dataset, 2022-2025, Harvard
  Dataverse, V4. CC BY 4.0.
