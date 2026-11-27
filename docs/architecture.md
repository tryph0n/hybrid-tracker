# Architecture

1. Each source module in `src/tracker/` has two steps: `extract()` downloads
   the files of one open dataset, and `normalize()` maps the stored files to
   the unified schema.
2. `tracker ingest` saves the downloaded files in the raw store, validates the
   mapped events and stores them as a clean Parquet table in the raw store.
   No data file is committed to the repository.
3. `tracker build` writes the published tables from the clean tables, and
   `tracker publish` sends them to two public Hugging Face datasets.
4. The Streamlit page `src/streamlit_app.py` reads the published events from
   the public dataset, and falls back to a snapshot bundled in the image.
5. The pipeline refreshes the live sources every day and publishes the
   datasets and the app.

This page is generated from the code by `scripts/architecture.py`: do not edit
it by hand. After a change, run this command and commit the result:

```bash
uv run python scripts/architecture.py
```

The pipeline fails while the page is out of date.

## Overview

Where the events come from, where they are stored, and where they go. Rounded boxes are outside the repository. Edge labels name the pipeline job or the manual step that moves the data. The Docker image holds `src/`, `.streamlit/`, and the packages of `uv.lock`.

```mermaid
flowchart TB
  source_eodi("eodi<br/>live")
  source_leiden("leiden<br/>static")
  ingest["tracker.ingest"]
  app["streamlit_app.py"]
  service_Docker_Hub("Docker Hub")
  service_Render("Render")
  service_Hugging_Face("Hugging Face")
  service_Heartbeat_monitor("Heartbeat monitor")
  source_eodi -->|"Data job"| ingest
  source_leiden -->|"Data job"| ingest
  app -->|"Docker image job"| service_Docker_Hub
  service_Docker_Hub -->|"Deploy to Render job"| service_Render
  ingest -->|"Hugging Face publish job"| service_Hugging_Face
  service_Hugging_Face -->|read by the app| app
```

| Source | Name | Kind | Licence | Clean table in the raw store |
| --- | --- | --- | --- | --- |
| `eodi` | [European Observatory of Drone Incidents](https://droneobservatory.eu) | live | CC BY 4.0 | `clean/eodi.parquet` |
| `leiden` | [Russian Operations Against Europe Dataset, 2022-2025](https://doi.org/10.7910/DVN/TQ0FMQ) | static | CC BY 4.0 | `clean/leiden.parquet` |

## Pipeline

The jobs of each workflow in `.github/workflows/`. Jobs run from left to right: a job starts when the jobs it needs have passed. Dashed jobs run only when their `if` condition holds. Edge labels name the job outputs that the next job reads. Secrets and variables are set in the repository settings (Secrets and variables, Actions). The steps table leaves out the steps without a name, and shows "a script" for a `run` of several lines.

`pipeline.yml` runs on `push` (`branches: main`), `pull_request`, `schedule` (`cron: 17 4 * * *`), `workflow_dispatch`.

```mermaid
flowchart LR
  classDef conditional stroke-dasharray: 6 4
  job_lint["Lint"]
  job_data["Data"]
  job_docs["Docs"]
  job_image_check["Docker build check"]:::conditional
  job_image["Docker image"]:::conditional
  job_deploy["Deploy to Render"]:::conditional
  job_publish["Hugging Face publish"]:::conditional
  job_report_failure["Report failure"]:::conditional
  job_source_alerts["Source alerts"]:::conditional
  job_close_failure["Close failure issue"]:::conditional
  job_heartbeat["Heartbeat"]:::conditional
  job_lint --> job_data
  job_lint --> job_docs
  job_data --> job_image
  job_docs --> job_image
  job_image -->|image| job_deploy
  job_data --> job_publish
  job_lint --> job_report_failure
  job_data --> job_report_failure
  job_docs --> job_report_failure
  job_image --> job_report_failure
  job_deploy --> job_report_failure
  job_publish --> job_report_failure
  job_source_alerts --> job_report_failure
  job_data --> job_source_alerts
  job_lint --> job_close_failure
  job_data --> job_close_failure
  job_docs --> job_close_failure
  job_image --> job_close_failure
  job_deploy --> job_close_failure
  job_publish --> job_close_failure
  job_source_alerts --> job_close_failure
  job_lint --> job_heartbeat
  job_data --> job_heartbeat
  job_docs --> job_heartbeat
  job_image --> job_heartbeat
  job_deploy --> job_heartbeat
  job_publish --> job_heartbeat
  job_source_alerts --> job_heartbeat
```

| Job | Runs if | Secrets and variables |
| --- | --- | --- |
| Lint |  |  |
| Data |  |  |
| Docs |  |  |
| Docker build check | `github.event_name == 'pull_request'` |  |
| Docker image | `github.ref == 'refs/heads/main' && vars.DOCKER_IMAGE != ''` | `DOCKERHUB_TOKEN` (secret), `DOCKER_IMAGE` (variable) |
| Deploy to Render | `needs.image.outputs.image != ''` | `RENDER_DEPLOY_HOOK_URL` (secret) |
| Hugging Face publish | `github.ref == 'refs/heads/main' && (github.event_name == 'schedule' \|\| github.event_name == 'workflow_dispatch')` |  |
| Report failure | `failure() && (github.event_name == 'schedule' \|\| github.event_name == 'workflow_dispatch')` |  |
| Source alerts | `github.ref == 'refs/heads/main' && (github.event_name == 'schedule' \|\| github.event_name == 'workflow_dispatch')` |  |
| Close failure issue | `${{ !failure() && !cancelled() && github.event_name == 'schedule' }}` |  |
| Heartbeat | `${{ !failure() && !cancelled() && github.event_name == 'schedule' }}` | `HEARTBEAT_URL` (secret) |

| Job | Step | Runs if | Runs |
| --- | --- | --- | --- |
| Lint | Install |  | `uv sync --locked` |
| Lint | Lint |  | `uv run ruff check . && uv run ruff format --check .` |
| Lint | Type check |  | `uv run --locked mypy` |
| Data | Install |  | `uv sync --locked` |
| Data | Refresh live sources | `github.event_name == 'schedule' \|\| github.event_name == 'workflow_dispatch'` | `uv run tracker ingest live` |
| Data | Ingest static sources missing from the store | `github.event_name == 'schedule' \|\| github.event_name == 'workflow_dispatch'` | `uv run tracker ingest static --if-missing` |
| Data | Build the published tables | `github.event_name == 'schedule' \|\| github.event_name == 'workflow_dispatch'` | `uv run tracker build` |
| Data | Write the quality table and the run summary | `github.event_name == 'schedule' \|\| github.event_name == 'workflow_dispatch'` | `uv run tracker quality` |
| Data | Keep the published tables for the publish job | `github.event_name == 'schedule' \|\| github.event_name == 'workflow_dispatch'` | `actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9` |
| Data | Run the tests |  | `uv run pytest --cov --cov-report=term --cov-report=markdown-append:"$GITHUB_STEP_SUMMARY"` |
| Docs | Install |  | `uv sync --locked --group docs` |
| Docs | Check the architecture page |  | `uv run python scripts/architecture.py --check` |
| Docs | Build docs |  | `uv run sphinx-build -W --keep-going -b html docs docs/_build/html` |
| Docker build check | Build the image |  | `docker/build-push-action@c3c9e263c25d99ce0380d002d59b67737d91b0dc` |
| Docker build check | Check the user and the health check |  | a script |
| Docker build check | Start the container and wait for the health answer |  | a script |
| Docker image | Name the image and check the registry |  | a script |
| Docker image | Set up Buildx | `steps.image.outputs.published != 'true'` | `docker/setup-buildx-action@f87e5991a6d7451dcb8d9637bfbc97413f497069` |
| Docker image | Build the image for the scan | `steps.image.outputs.published != 'true'` | `docker/build-push-action@c3c9e263c25d99ce0380d002d59b67737d91b0dc` |
| Docker image | Scan the image and write the report | `steps.image.outputs.published != 'true'` | `aquasecurity/trivy-action@ed142fd0673e97e23eac54620cfb913e5ce36c25` |
| Docker image | Add the report to the job summary | `steps.image.outputs.published != 'true'` | a script |
| Docker image | Fail on a critical finding that has a fix | `steps.image.outputs.published != 'true'` | `aquasecurity/trivy-action@ed142fd0673e97e23eac54620cfb913e5ce36c25` |
| Docker image | Publish the image with its provenance | `steps.image.outputs.published != 'true'` | `docker/build-push-action@c3c9e263c25d99ce0380d002d59b67737d91b0dc` |
| Docker image | Attest the build provenance | `steps.image.outputs.published != 'true'` | `actions/attest@1e69f48acb82d1966a394da916b4c1698aa569d6` |
| Docker image | Move latest to the attested image | `steps.image.outputs.published != 'true'` | `docker buildx imagetools create --tag "$DOCKER_IMAGE:latest" "$DOCKER_IMAGE@$DIGEST"` |
| Deploy to Render | Deploy to Render |  | a script |
| Deploy to Render | Wait for the new image to answer | `steps.deploy.outputs.called == 'true'` | a script |
| Hugging Face publish | Install |  | `uv sync --locked` |
| Hugging Face publish | Get the published tables |  | `actions/download-artifact@9000827ccba6bdab643e8b6fd33ac0654aef8333` |
| Hugging Face publish | Publish the open dataset |  | `uv run tracker publish open` |
| Hugging Face publish | Publish the NonCommercial dataset |  | `uv run tracker publish nc` |
| Report failure | Open an issue, or comment on the open one |  | a script |
| Source alerts | Install |  | `uv sync --locked` |
| Source alerts | Get the alerts of the quality check |  | `actions/download-artifact@9000827ccba6bdab643e8b6fd33ac0654aef8333` |
| Source alerts | Open, comment on or close the alert issues |  | `uv run tracker alerts --run-url "$GITHUB_SERVER_URL/$GITHUB_REPOSITORY/actions/runs/$GITHUB_RUN_ID"` |
| Close failure issue | Close the open failure issues |  | a script |
| Heartbeat | Ping the heartbeat monitor |  | a script |

## Modules

Python files and their imports: an arrow points to the imported module.

```mermaid
flowchart TB
  module_streamlit_app_py["streamlit_app.py"]
  module_tracker_actors["tracker.actors"]
  module_tracker_alerts["tracker.alerts"]
  module_tracker_build["tracker.build"]
  module_tracker_cli["tracker.cli"]
  module_tracker_clock["tracker.clock"]
  module_tracker_dedup["tracker.dedup"]
  module_tracker_eodi["tracker.eodi"]
  module_tracker_errors["tracker.errors"]
  module_tracker_geonames["tracker.geonames"]
  module_tracker_hub["tracker.hub"]
  module_tracker_ingest["tracker.ingest"]
  module_tracker_leiden["tracker.leiden"]
  module_tracker_provenance["tracker.provenance"]
  module_tracker_publish["tracker.publish"]
  module_tracker_quality["tracker.quality"]
  module_tracker_schema["tracker.schema"]
  module_tracker_sources["tracker.sources"]
  module_tracker_stanford["tracker.stanford"]
  module_tracker_store["tracker.store"]
  module_tracker_views["tracker.views"]
  module_architecture_py["architecture.py"]
  module_streamlit_app_py --> module_tracker_clock
  module_streamlit_app_py --> module_tracker_hub
  module_streamlit_app_py --> module_tracker_schema
  module_streamlit_app_py --> module_tracker_sources
  module_streamlit_app_py --> module_tracker_views
  module_tracker_actors --> module_tracker_errors
  module_tracker_actors --> module_tracker_store
  module_tracker_alerts --> module_tracker_errors
  module_tracker_build --> module_tracker_actors
  module_tracker_build --> module_tracker_dedup
  module_tracker_build --> module_tracker_errors
  module_tracker_build --> module_tracker_geonames
  module_tracker_build --> module_tracker_ingest
  module_tracker_build --> module_tracker_schema
  module_tracker_build --> module_tracker_sources
  module_tracker_build --> module_tracker_store
  module_tracker_cli --> module_tracker_alerts
  module_tracker_cli --> module_tracker_build
  module_tracker_cli --> module_tracker_errors
  module_tracker_cli --> module_tracker_ingest
  module_tracker_cli --> module_tracker_publish
  module_tracker_cli --> module_tracker_quality
  module_tracker_cli --> module_tracker_sources
  module_tracker_cli --> module_tracker_store
  module_tracker_dedup --> module_tracker_errors
  module_tracker_eodi --> module_tracker_errors
  module_tracker_eodi --> module_tracker_schema
  module_tracker_eodi --> module_tracker_store
  module_tracker_geonames --> module_tracker_errors
  module_tracker_geonames --> module_tracker_store
  module_tracker_hub --> module_tracker_publish
  module_tracker_ingest --> module_tracker_clock
  module_tracker_ingest --> module_tracker_errors
  module_tracker_ingest --> module_tracker_provenance
  module_tracker_ingest --> module_tracker_schema
  module_tracker_ingest --> module_tracker_sources
  module_tracker_ingest --> module_tracker_store
  module_tracker_leiden --> module_tracker_errors
  module_tracker_leiden --> module_tracker_schema
  module_tracker_leiden --> module_tracker_store
  module_tracker_publish --> module_tracker_actors
  module_tracker_publish --> module_tracker_build
  module_tracker_publish --> module_tracker_errors
  module_tracker_publish --> module_tracker_provenance
  module_tracker_publish --> module_tracker_quality
  module_tracker_publish --> module_tracker_schema
  module_tracker_publish --> module_tracker_sources
  module_tracker_quality --> module_tracker_build
  module_tracker_quality --> module_tracker_clock
  module_tracker_quality --> module_tracker_errors
  module_tracker_quality --> module_tracker_ingest
  module_tracker_quality --> module_tracker_schema
  module_tracker_quality --> module_tracker_sources
  module_tracker_quality --> module_tracker_store
  module_tracker_schema --> module_tracker_errors
  module_tracker_sources --> module_tracker_actors
  module_tracker_sources --> module_tracker_eodi
  module_tracker_sources --> module_tracker_geonames
  module_tracker_sources --> module_tracker_leiden
  module_tracker_sources --> module_tracker_store
  module_tracker_stanford --> module_tracker_errors
  module_tracker_stanford --> module_tracker_store
  module_tracker_store --> module_tracker_clock
  module_tracker_views --> module_tracker_schema
  module_architecture_py --> module_tracker_ingest
  module_architecture_py --> module_tracker_sources
```

| Module | File | Purpose | Third-party imports |
| --- | --- | --- | --- |
| `streamlit_app.py` | `src/streamlit_app.py` | Dashboard: maps and charts of the unified hybrid-incident dataset. | pandas, plotly, pycountry, streamlit |
| `tracker.actors` | `src/tracker/actors.py` | Threat-group lookup from the MISP galaxy threat-actor cluster. | pandas, pycountry |
| `tracker.alerts` | `src/tracker/alerts.py` | One alert issue per source, kept open while the source has a cause. |  |
| `tracker.build` | `src/tracker/build.py` | Build the published tables from the clean tables of the raw store. | duckdb, pandas, pyarrow |
| `tracker.cli` | `src/tracker/cli.py` | The \`tracker\` command: one subcommand per action. |  |
| `tracker.clock` | `src/tracker/clock.py` | The current day, in UTC like the stored event dates. | pandas |
| `tracker.dedup` | `src/tracker/dedup.py` | Clusters of the rows that two sources record for one incident. | pandas, pycountry, rapidfuzz |
| `tracker.eodi` | `src/tracker/eodi.py` | European Observatory of Drone Incidents (EODI) - live source. | pandas |
| `tracker.errors` | `src/tracker/errors.py` | Errors that the code raises on purpose with a message for the user. |  |
| `tracker.geonames` | `src/tracker/geonames.py` | GeoNames populated places - reference data, not an event source. | pandas |
| `tracker.hub` | `src/tracker/hub.py` | Read the published tables from the public Hugging Face dataset. | huggingface_hub, pandas |
| `tracker.ingest` | `src/tracker/ingest.py` | Download sources, keep their payloads in the raw store and write clean tables. | pandas |
| `tracker.leiden` | `src/tracker/leiden.py` | Russian Operations Against Europe Dataset (Leiden University) - static source. | openpyxl, pandas, pycountry |
| `tracker.provenance` | `src/tracker/provenance.py` | Code commit and workflow run of a pipeline step, read from the GitHub variables. |  |
| `tracker.publish` | `src/tracker/publish.py` | Publish the tables of \`tracker build\` to the two public Hugging Face datasets. | huggingface_hub, pandas |
| `tracker.quality` | `src/tracker/quality.py` | Quality table and run summary: one row per source and per run. | pandas |
| `tracker.schema` | `src/tracker/schema.py` | Unified event schema shared by every source adapter. | pandas |
| `tracker.sources` | `src/tracker/sources.py` | Registry of data sources. | pandas |
| `tracker.stanford` | `src/tracker/stanford.py` | Stanford GNSS interference (rfi.stanford.edu): daily heatmaps and context table. | brotli, h3, pandas |
| `tracker.store` | `src/tracker/store.py` | Raw store client: keeps each source payload as fetched, with a manifest. | huggingface_hub |
| `tracker.views` | `src/tracker/views.py` | Filters and headline counts of the dashboard, as plain functions on a frame. | pandas |
| `architecture.py` | `scripts/architecture.py` | Generate docs/architecture.md, the architecture diagrams, from the code. | yaml |
