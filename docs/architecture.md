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
    step_image_Publish_Docker_image(["pipeline.yml: Publish Docker image<br/>uses vars.DOCKER_IMAGE, secrets.DOCKERHUB_TOKEN"])
    step_deploy_Deploy_to_Render(["pipeline.yml: Deploy to Render<br/>uses secrets.RENDER_DEPLOY_HOOK_URL"])
    step_mirror_Mirror_the_data_to_Hugging_Face(["pipeline.yml: Mirror the data to Hugging Face<br/>uses vars.HF_DATASET"])
  end
  image --> step_image_Publish_Docker_image
  events --> step_mirror_Mirror_the_data_to_Hugging_Face
  step_image_Publish_Docker_image -.->|image| step_deploy_Deploy_to_Render
```

## Pipeline

Each workflow in `.github/workflows/`: its triggers and settings, then one box per job, from left to right along the `needs` of the jobs. A box gives the job settings, then its steps in order. An arrow label names the job outputs that the next job reads. Dashed jobs and steps run only when their `if` condition holds. A step that uses secrets or variables names them: they are set in the repository settings (Secrets and variables, Actions).

```mermaid
---
config: {"flowchart": {"wrappingWidth": 400}}
---
flowchart LR
  classDef conditional stroke-dasharray: 6 4
  subgraph workflow_pipeline_yml_box[".github/workflows/pipeline.yml"]
    direction LR
    on_pipeline_yml_push(["push<br/>branches: main"])
    on_pipeline_yml_push --> workflow_pipeline_yml
    on_pipeline_yml_pull_request(["pull_request"])
    on_pipeline_yml_pull_request --> workflow_pipeline_yml
    on_pipeline_yml_schedule(["schedule<br/>cron: 17 4 * * *"])
    on_pipeline_yml_schedule --> workflow_pipeline_yml
    on_pipeline_yml_workflow_dispatch(["workflow_dispatch"])
    on_pipeline_yml_workflow_dispatch --> workflow_pipeline_yml
    workflow_pipeline_yml["workflow pipeline<br/>concurrency: group: ${{ github.event_name == 'pull_request' #amp;#amp; github.ref || 'pipeline' }}, cancel-in-progress: false<br/>permissions: contents: read<br/>env: PYTHONPATH: src, PYTHONDONTWRITEBYTECODE: 1"]
    subgraph job_pipeline_yml_lint["Lint"]
      direction TB
      job_pipeline_yml_lint_settings["job lint<br/>runs-on: ubuntu-latest<br/>timeout-minutes: 10"]
      step_lint_actions_checkout_v7["actions/checkout@v7"]
      job_pipeline_yml_lint_settings --> step_lint_actions_checkout_v7
      step_lint_actions_setup_python_v7["actions/setup-python@v7<br/>python-version: 3.13<br/>cache: pip"]
      step_lint_actions_checkout_v7 --> step_lint_actions_setup_python_v7
      step_lint_Install["Install"]
      step_lint_actions_setup_python_v7 --> step_lint_Install
      step_lint_Lint["Lint"]
      step_lint_Install --> step_lint_Lint
    end
    subgraph job_pipeline_yml_data["Data"]
      direction TB
      job_pipeline_yml_data_settings["job data<br/>runs-on: ubuntu-latest<br/>timeout-minutes: 15<br/>permissions: contents: write<br/>outputs: sha: ${{ steps.commit.outputs.sha || github.sha }}"]
      step_data_actions_checkout_v7["actions/checkout@v7"]
      job_pipeline_yml_data_settings --> step_data_actions_checkout_v7
      step_data_actions_setup_python_v7["actions/setup-python@v7<br/>python-version: 3.13<br/>cache: pip"]
      step_data_actions_checkout_v7 --> step_data_actions_setup_python_v7
      step_data_Install["Install"]
      step_data_actions_setup_python_v7 --> step_data_Install
      step_data_Refresh_live_sources["Refresh live sources<br/>runs tracker.ingest live<br/>if: github.event_name == 'schedule' || github.event_name == 'workflow_dispatch'"]:::conditional
      step_data_Install --> step_data_Refresh_live_sources
      step_data_Validate_stored_data["Validate stored data<br/>runs tracker.ingest check"]
      step_data_Refresh_live_sources --> step_data_Validate_stored_data
      step_data_Run_the_tests["Run the tests"]
      step_data_Validate_stored_data --> step_data_Run_the_tests
      step_data_Commit_refreshed_data["Commit refreshed data<br/>if: github.event_name == 'schedule' || github.event_name == 'workflow_dispatch'"]:::conditional
      step_data_Run_the_tests --> step_data_Commit_refreshed_data
    end
    subgraph job_pipeline_yml_docs["Docs"]
      direction TB
      job_pipeline_yml_docs_settings["job docs<br/>runs-on: ubuntu-latest<br/>timeout-minutes: 10"]
      step_docs_actions_checkout_v7["actions/checkout@v7"]
      job_pipeline_yml_docs_settings --> step_docs_actions_checkout_v7
      step_docs_actions_setup_python_v7["actions/setup-python@v7<br/>python-version: 3.13<br/>cache: pip"]
      step_docs_actions_checkout_v7 --> step_docs_actions_setup_python_v7
      step_docs_Install["Install"]
      step_docs_actions_setup_python_v7 --> step_docs_Install
      step_docs_Check_the_architecture_page["Check the architecture page<br/>runs scripts/architecture.py"]
      step_docs_Install --> step_docs_Check_the_architecture_page
      step_docs_Build_docs["Build docs"]
      step_docs_Check_the_architecture_page --> step_docs_Build_docs
    end
    subgraph job_pipeline_yml_image["Docker image"]
      direction TB
      job_pipeline_yml_image_settings["job image<br/>runs-on: ubuntu-latest<br/>timeout-minutes: 10<br/>outputs: image: ${{ steps.image.outputs.image }}<br/>if: github.ref == 'refs/heads/main' #amp;#amp; vars.DOCKER_IMAGE != ''"]
      step_image_actions_checkout_v7["actions/checkout@v7<br/>ref: ${{ needs.data.outputs.sha }}"]
      job_pipeline_yml_image_settings --> step_image_actions_checkout_v7
      step_image_Publish_Docker_image["Publish Docker image<br/>uses vars.DOCKER_IMAGE, secrets.DOCKERHUB_TOKEN"]
      step_image_actions_checkout_v7 --> step_image_Publish_Docker_image
    end
    subgraph job_pipeline_yml_deploy["Deploy to Render"]
      direction TB
      job_pipeline_yml_deploy_settings["job deploy<br/>runs-on: ubuntu-latest<br/>timeout-minutes: 5<br/>if: needs.image.outputs.image != ''"]
      step_deploy_Deploy_to_Render["Deploy to Render<br/>uses secrets.RENDER_DEPLOY_HOOK_URL"]
      job_pipeline_yml_deploy_settings --> step_deploy_Deploy_to_Render
    end
    subgraph job_pipeline_yml_mirror["Hugging Face mirror"]
      direction TB
      job_pipeline_yml_mirror_settings["job mirror<br/>runs-on: ubuntu-latest<br/>timeout-minutes: 10<br/>permissions: contents: read, id-token: write<br/>if: github.ref == 'refs/heads/main' #amp;#amp; vars.HF_DATASET != ''"]
      step_mirror_actions_checkout_v7["actions/checkout@v7<br/>ref: ${{ needs.data.outputs.sha }}"]
      job_pipeline_yml_mirror_settings --> step_mirror_actions_checkout_v7
      step_mirror_actions_setup_python_v7["actions/setup-python@v7<br/>python-version: 3.13<br/>cache: pip"]
      step_mirror_actions_checkout_v7 --> step_mirror_actions_setup_python_v7
      step_mirror_Install["Install"]
      step_mirror_actions_setup_python_v7 --> step_mirror_Install
      step_mirror_Mirror_the_data_to_Hugging_Face["Mirror the data to Hugging Face<br/>uses vars.HF_DATASET"]
      step_mirror_Install --> step_mirror_Mirror_the_data_to_Hugging_Face
    end
    workflow_pipeline_yml --> job_pipeline_yml_lint
    job_pipeline_yml_lint --> job_pipeline_yml_data
    job_pipeline_yml_lint --> job_pipeline_yml_docs
    job_pipeline_yml_data -->|sha| job_pipeline_yml_image
    job_pipeline_yml_docs --> job_pipeline_yml_image
    job_pipeline_yml_image -->|image| job_pipeline_yml_deploy
    job_pipeline_yml_data -->|sha| job_pipeline_yml_mirror
  end
  class job_pipeline_yml_image conditional
  class job_pipeline_yml_deploy conditional
  class job_pipeline_yml_mirror conditional
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
