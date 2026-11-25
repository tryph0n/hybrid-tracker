# Architecture

1. Each source module in `src/tracker/` downloads one open dataset and maps it
   to the unified schema.
2. `python -m tracker.ingest` validates the result and stores it as
   `data/events/<source>.csv`, committed to the repository.
3. The Streamlit page `src/streamlit_app.py` reads these files and nothing else.
4. The pipeline refreshes the live sources every day, publishes the app and
   mirrors `data/events/` to a public Hugging Face dataset. The app does not
   read the mirror.

This page is generated from the code by `scripts/architecture.py`: do not edit
it by hand. After a change, run this command and commit the result:

```bash
uv run python scripts/architecture.py
```

The pipeline fails while the page is out of date.

## Overview

Where the events come from, where they are stored, and where they go. Rounded boxes are outside the repository. Edge labels name the pipeline job or the manual step that moves the data. The Docker image holds `src/`, `.streamlit/`, `data/`, and the packages of `uv.lock`.

```mermaid
flowchart TB
  source_eodi("eodi<br/>live")
  source_leiden("leiden<br/>static")
  ingest["tracker.ingest"]
  events[("data/events/")]
  app["streamlit_app.py"]
  service_Docker_Hub("Docker Hub")
  service_Render("Render")
  service_Hugging_Face("Hugging Face")
  service_Heartbeat_monitor("Heartbeat monitor")
  source_eodi -->|"Data job"| ingest
  source_leiden -->|"by hand"| ingest
  ingest --> events
  events --> app
  app -->|"Docker image job"| service_Docker_Hub
  service_Docker_Hub -->|"Deploy to Render job"| service_Render
  events -->|"Hugging Face mirror job"| service_Hugging_Face
```

| Source | Name | Kind | Licence | Stored as |
| --- | --- | --- | --- | --- |
| `eodi` | [European Observatory of Drone Incidents](https://droneobservatory.eu) | live | CC BY 4.0 | `data/events/eodi.csv` |
| `leiden` | [Russian Operations Against Europe Dataset, 2022-2025](https://doi.org/10.7910/DVN/TQ0FMQ) | static | CC BY 4.0 | `data/events/leiden.csv` |

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
  job_mirror["Hugging Face mirror"]:::conditional
  job_report_failure["Report failure"]:::conditional
  job_close_failure["Close failure issue"]:::conditional
  job_heartbeat["Heartbeat"]:::conditional
  job_lint --> job_data
  job_lint --> job_docs
  job_data -->|sha| job_image
  job_docs --> job_image
  job_image -->|image| job_deploy
  job_data -->|sha| job_mirror
  job_lint --> job_report_failure
  job_data --> job_report_failure
  job_docs --> job_report_failure
  job_image --> job_report_failure
  job_deploy --> job_report_failure
  job_mirror --> job_report_failure
  job_lint --> job_close_failure
  job_data --> job_close_failure
  job_docs --> job_close_failure
  job_image --> job_close_failure
  job_deploy --> job_close_failure
  job_mirror --> job_close_failure
  job_lint --> job_heartbeat
  job_data --> job_heartbeat
  job_docs --> job_heartbeat
  job_image --> job_heartbeat
  job_deploy --> job_heartbeat
  job_mirror --> job_heartbeat
```

| Job | Runs if | Secrets and variables |
| --- | --- | --- |
| Lint |  |  |
| Data |  |  |
| Docs |  |  |
| Docker build check | `github.event_name == 'pull_request'` |  |
| Docker image | `github.ref == 'refs/heads/main' && vars.DOCKER_IMAGE != ''` | `DOCKERHUB_TOKEN` (secret), `DOCKER_IMAGE` (variable) |
| Deploy to Render | `needs.image.outputs.image != ''` | `RENDER_DEPLOY_HOOK_URL` (secret) |
| Hugging Face mirror | `github.ref == 'refs/heads/main' && vars.HF_DATASET != ''` | `HF_DATASET` (variable) |
| Report failure | `failure() && (github.event_name == 'schedule' \|\| github.event_name == 'workflow_dispatch')` |  |
| Close failure issue | `${{ !failure() && !cancelled() && github.event_name == 'schedule' }}` |  |
| Heartbeat | `${{ !failure() && !cancelled() && github.event_name == 'schedule' }}` | `HEARTBEAT_URL` (secret) |

| Job | Step | Runs if | Runs |
| --- | --- | --- | --- |
| Lint | Install |  | `uv sync --locked` |
| Lint | Lint |  | `uv run ruff check . && uv run ruff format --check .` |
| Lint | Type check |  | `uv run --locked mypy` |
| Data | Install |  | `uv sync --locked` |
| Data | Refresh live sources | `github.event_name == 'schedule' \|\| github.event_name == 'workflow_dispatch'` | `uv run python -m tracker.ingest live` |
| Data | Validate stored data |  | `uv run python -m tracker.ingest check` |
| Data | Run the tests |  | `uv run pytest --cov --cov-report=term --cov-report=markdown-append:"$GITHUB_STEP_SUMMARY"` |
| Data | Commit refreshed data | `github.event_name == 'schedule' \|\| github.event_name == 'workflow_dispatch'` | a script |
| Docs | Install |  | `uv sync --locked --group docs` |
| Docs | Check the architecture page |  | `uv run python scripts/architecture.py --check` |
| Docs | Build docs |  | `uv run sphinx-build -W --keep-going -b html docs docs/_build/html` |
| Docker build check | Build the image |  | `docker/build-push-action@c3c9e263c25d99ce0380d002d59b67737d91b0dc` |
| Docker build check | Check the user and the health check |  | a script |
| Docker build check | Start the container and wait for the health answer |  | a script |
| Docker image | Publish Docker image |  | a script |
| Deploy to Render | Deploy to Render |  | a script |
| Deploy to Render | Wait for the new image to answer | `steps.deploy.outputs.called == 'true'` | a script |
| Hugging Face mirror | Install |  | `uv sync --locked` |
| Hugging Face mirror | Mirror the data to Hugging Face |  | a script |
| Report failure | Open an issue, or comment on the open one |  | a script |
| Close failure issue | Close the open failure issues |  | a script |
| Heartbeat | Ping the heartbeat monitor |  | a script |

## Modules

Python files and their imports: an arrow points to the imported module.

```mermaid
flowchart TB
  module_streamlit_app_py["streamlit_app.py"]
  module_tracker_eodi["tracker.eodi"]
  module_tracker_ingest["tracker.ingest"]
  module_tracker_leiden["tracker.leiden"]
  module_tracker_schema["tracker.schema"]
  module_tracker_sources["tracker.sources"]
  module_architecture_py["architecture.py"]
  module_streamlit_app_py --> module_tracker_schema
  module_streamlit_app_py --> module_tracker_sources
  module_tracker_eodi --> module_tracker_schema
  module_tracker_ingest --> module_tracker_schema
  module_tracker_ingest --> module_tracker_sources
  module_tracker_leiden --> module_tracker_schema
  module_tracker_sources --> module_tracker_eodi
  module_tracker_sources --> module_tracker_leiden
  module_architecture_py --> module_tracker_sources
```

| Module | File | Purpose | Third-party imports |
| --- | --- | --- | --- |
| `streamlit_app.py` | `src/streamlit_app.py` | Dashboard: maps and charts of the unified hybrid-incident dataset. | pandas, plotly, pycountry, streamlit |
| `tracker.eodi` | `src/tracker/eodi.py` | European Observatory of Drone Incidents (EODI) - live source. | pandas |
| `tracker.ingest` | `src/tracker/ingest.py` | Fetch sources and store them as data/events/\<source\>.csv. |  |
| `tracker.leiden` | `src/tracker/leiden.py` | Russian Operations Against Europe Dataset (Leiden University) - static source. | pandas, pycountry |
| `tracker.schema` | `src/tracker/schema.py` | Unified event schema shared by every source adapter. | pandas |
| `tracker.sources` | `src/tracker/sources.py` | Registry of data sources and access to their stored events. | pandas |
| `architecture.py` | `scripts/architecture.py` | Generate docs/architecture.md, the architecture diagrams, from the code. | yaml |
