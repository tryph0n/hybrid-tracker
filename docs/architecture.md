# Architecture

1. Each source module in `src/tracker/` downloads one open dataset and maps it
   to the unified schema.
2. `python -m tracker.ingest` validates the result and stores it as
   `data/events/<source>.csv`, committed to the repository.
3. The Streamlit page `src/streamlit_app.py` reads these files and nothing else.
4. The pipeline refreshes the live sources every day and publishes the app.

This page is generated from the code by `scripts/architecture.py`: do not edit
it by hand. After a change, run this command and commit the result:

```bash
PYTHONPATH=src python scripts/architecture.py
```

The pipeline fails while the page is out of date.

## Overview

Where the events come from, where they are stored, and how the app is published. Edge labels name the workflow steps that do the work.

```mermaid
---
config: {"flowchart": {"wrappingWidth": 400}}
---
flowchart TB
  subgraph sources["Open datasets: tracker.sources"]
    source_eodi["European Observatory of Drone Incidents<br/>eodi: live, CC BY 4.0<br/>https://droneobservatory.eu<br/>https://droneobservatory.eu/datos/incidentes.csv<br/>https://droneobservatory.eu/datos/incidentes_sin_ubicacion.json<br/>https://droneobservatory.eu/{id}"]
    source_leiden["Russian Operations Against Europe Dataset, 2022-2025<br/>leiden: static, CC BY 4.0<br/>https://doi.org/10.7910/DVN/TQ0FMQ<br/>https://dataverse.harvard.edu/api/access/datafile/13350817"]
  end
  ingest["tracker.ingest<br/>Fetch sources and store them as data/events/#lt;source#gt;.csv."]
  source_eodi -- "pipeline.yml: Refresh live sources" --> ingest
  source_leiden -- "by hand: tracker.ingest static" --> ingest
  subgraph events["data/events/"]
    events_eodi[("eodi.csv")]
    events_leiden[("leiden.csv")]
  end
  ingest --> events_eodi
  ingest --> events_leiden
  app["src/streamlit_app.py<br/>Dashboard: maps and charts of the unified hybrid-incident dataset."]
  events --> app
  image[["Docker image: Dockerfile<br/>FROM python:3.13-slim<br/>COPY requirements.txt, .streamlit/, src/, data/<br/>EXPOSE 8501"]]
  app --> image
  events --> image
  subgraph delivery["Workflow steps that use secrets or variables"]
    step_Publish_Docker_image(["pipeline.yml: Publish Docker image<br/>uses vars.DOCKER_IMAGE, secrets.DOCKERHUB_TOKEN"])
    step_Deploy_to_Render(["pipeline.yml: Deploy to Render<br/>uses secrets.RENDER_DEPLOY_HOOK_URL"])
  end
  image --> step_Publish_Docker_image
  step_Publish_Docker_image -.->|image| step_Deploy_to_Render
```

## Pipeline

Each workflow in `.github/workflows/`: its triggers, settings and jobs, then the steps in order. Dashed steps run only when their `if` condition holds. Dotted arrows point to the secrets and variables a step uses: they are set in the repository settings.

```mermaid
---
config: {"flowchart": {"wrappingWidth": 400}}
---
flowchart TB
  classDef conditional stroke-dasharray: 6 4
  subgraph workflow_pipeline_yml_box[".github/workflows/pipeline.yml"]
    on_pipeline_yml_push(["push<br/>branches: main"])
    on_pipeline_yml_push --> workflow_pipeline_yml
    on_pipeline_yml_pull_request(["pull_request"])
    on_pipeline_yml_pull_request --> workflow_pipeline_yml
    on_pipeline_yml_schedule(["schedule<br/>cron: 17 4 * * *"])
    on_pipeline_yml_schedule --> workflow_pipeline_yml
    on_pipeline_yml_workflow_dispatch(["workflow_dispatch"])
    on_pipeline_yml_workflow_dispatch --> workflow_pipeline_yml
    workflow_pipeline_yml["workflow pipeline<br/>concurrency: group: ${{ github.event_name == 'pull_request' #amp;#amp; github.ref || 'pipeline' }}, cancel-in-progress: false<br/>permissions: contents: write<br/>env: PYTHONPATH: src, PYTHONDONTWRITEBYTECODE: 1"]
    job_pipeline_yml_pipeline["job pipeline<br/>runs-on: ubuntu-latest<br/>timeout-minutes: 15"]
    workflow_pipeline_yml --> job_pipeline_yml_pipeline
    step_actions_checkout_v7["actions/checkout@v7"]
    job_pipeline_yml_pipeline --> step_actions_checkout_v7
    step_actions_setup_python_v7["actions/setup-python@v7<br/>python-version: 3.13<br/>cache: pip"]
    step_actions_checkout_v7 --> step_actions_setup_python_v7
    step_Install["Install"]
    step_actions_setup_python_v7 --> step_Install
    step_Lint["Lint"]
    step_Install --> step_Lint
    step_Refresh_live_sources["Refresh live sources<br/>runs tracker.ingest live<br/>if: github.event_name == 'schedule' || github.event_name == 'workflow_dispatch'"]:::conditional
    step_Lint --> step_Refresh_live_sources
    step_Validate_stored_data["Validate stored data<br/>runs tracker.ingest check"]
    step_Refresh_live_sources --> step_Validate_stored_data
    step_Run_the_tests["Run the tests"]
    step_Validate_stored_data --> step_Run_the_tests
    step_Commit_refreshed_data["Commit refreshed data<br/>if: github.event_name == 'schedule' || github.event_name == 'workflow_dispatch'"]:::conditional
    step_Run_the_tests --> step_Commit_refreshed_data
    step_Check_the_architecture_page["Check the architecture page<br/>runs scripts/architecture.py"]
    step_Commit_refreshed_data --> step_Check_the_architecture_page
    step_Build_docs["Build docs"]
    step_Check_the_architecture_page --> step_Build_docs
    step_Publish_Docker_image["Publish Docker image<br/>if: github.ref == 'refs/heads/main' #amp;#amp; vars.DOCKER_IMAGE != ''"]:::conditional
    step_Build_docs --> step_Publish_Docker_image
    step_Deploy_to_Render["Deploy to Render<br/>if: steps.image.outputs.image != ''"]:::conditional
    step_Publish_Docker_image --> step_Deploy_to_Render
  end
  step_Publish_Docker_image -.->|image| step_Deploy_to_Render
  subgraph settings["Repository settings: Secrets and variables, Actions"]
    secrets_DOCKERHUB_TOKEN{{"secret DOCKERHUB_TOKEN"}}
    secrets_RENDER_DEPLOY_HOOK_URL{{"secret RENDER_DEPLOY_HOOK_URL"}}
    vars_DOCKER_IMAGE[/"variable DOCKER_IMAGE"/]
  end
  step_Publish_Docker_image -.-> secrets_DOCKERHUB_TOKEN
  step_Deploy_to_Render -.-> secrets_RENDER_DEPLOY_HOOK_URL
  step_Publish_Docker_image -.-> vars_DOCKER_IMAGE
```

## Modules

Python files and their imports: an arrow points to the imported module. Each box gives the module docstring and its third-party imports.

```mermaid
---
config: {"flowchart": {"wrappingWidth": 400}}
---
flowchart TB
  subgraph folder_src["src/"]
    module_streamlit_app_py["streamlit_app.py<br/>Dashboard: maps and charts of the unified hybrid-incident dataset.<br/>imports pandas, plotly, pycountry, streamlit"]
  end
  subgraph folder_src_tracker["src/tracker/: Ingestion and storage of hybrid-incident events from open datasets."]
    module_tracker_eodi["tracker.eodi<br/>European Observatory of Drone Incidents (EODI) - live source.<br/>imports pandas"]
    module_tracker_ingest["tracker.ingest<br/>Fetch sources and store them as data/events/#lt;source#gt;.csv."]
    module_tracker_leiden["tracker.leiden<br/>Russian Operations Against Europe Dataset (Leiden University) - static source.<br/>imports pandas, pycountry"]
    module_tracker_schema["tracker.schema<br/>Unified event schema shared by every source adapter.<br/>imports pandas"]
    module_tracker_sources["tracker.sources<br/>Registry of data sources and access to their stored events.<br/>imports pandas"]
  end
  subgraph folder_scripts["scripts/"]
    module_architecture_py["architecture.py<br/>Generate docs/architecture.md, the architecture diagrams, from the code.<br/>imports yaml"]
  end
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
