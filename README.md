# Europe Hybrid Incidents Tracker

Maps and charts of drone incidents, sabotage and other hybrid operations in
Europe since 2022, compiled from open datasets into one event table.

Live dashboard: [hybrid-tracker.onrender.com](https://hybrid-tracker.onrender.com).
It sleeps when nobody visits, so the first page load can take about a minute.

## Run locally

```bash
pip install -r requirements.txt
streamlit run src/streamlit_app.py
```

Or run the published image: `docker run -p 8501:8501 sambot961/hybrid-tracker`.

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
4. With `docs/requirements.txt` installed, run
   `PYTHONPATH=src python scripts/architecture.py` and commit
   `docs/architecture.md`: CI fails while the page does not match the code.

## Documentation

`docs/` holds the Sphinx documentation: this README, a data dictionary and an
API reference read from the docstrings of `src/tracker/`. Build it with:

```bash
pip install -r docs/requirements.txt
sphinx-build -W --keep-going -b html docs docs/_build/html
```

`docs/architecture.md` has diagrams of the sources, the pipeline and the modules.

## Pipeline

`.github/workflows/pipeline.yml` runs on pushes to `main`, on pull requests,
and every day at 04:17 UTC:

1. lint and validate the stored data;
2. on the daily or a manual run: refresh live sources and commit the changes;
3. check that `docs/architecture.md` matches the code, and build the docs;
4. on `main`: build and push the Docker image when its inputs changed, then
   deploy that exact image to Render.

The image tag is a hash of the files the `Dockerfile` copies, so an unchanged
app and data publish nothing. `latest` follows the newest image.

Deployment needs, in the GitHub repository settings (Secrets and variables,
Actions):

- variable `DOCKER_IMAGE`: public Docker Hub repository, `<user>/<repository>`;
- secret `DOCKERHUB_TOKEN`: a Docker Hub access token with write access;
- secret `RENDER_DEPLOY_HOOK_URL`: the deploy hook (Settings, Deploy Hook) of
  a Render web service created from the image
  `docker.io/<user>/<repository>:latest`.

The image step is skipped until `DOCKER_IMAGE` is set, and the Render step
until `RENDER_DEPLOY_HOOK_URL` is set.

The Render service runs on the free plan with `PORT=8501` and the health check
path `/_stcore/health`. It sleeps after 15 minutes without visits, so the
next visit waits about a minute for it to start.

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
