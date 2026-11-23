# Maintainer guide

This page is for the person who runs the project. For the data and the
dashboard, see the home page.

## Set up

Use Python 3.13 and a virtual environment. The install line is the one of the
`Lint` and `Docs` jobs in `.github/workflows/pipeline.yml`:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt -r docs/requirements.txt
export PYTHONPATH=src
```

## Sources and ingest

A source is live or static (`Source.kind` in `src/tracker/sources.py`).

- A live source is fetched in full at each run and replaces the stored file.
  The pipeline refreshes it each day.
- A static source is a frozen historical snapshot, pinned to one upstream file
  version. You re-ingest it by hand.

Each source is stored as `data/events/<name>.csv`. Commands, from the
repository root:

```bash
python -m tracker.ingest live      # every live source
python -m tracker.ingest static    # every static source
python -m tracker.ingest <name>    # one source, for example eodi
python -m tracker.ingest check     # validate the stored files, no network
```

`ingest` validates the events before it writes the file. It rejects a refresh
that loses more than 5% of the stored events (`MAX_SHRINK` in
`src/tracker/ingest.py`): an upstream outage is more likely than a real
correction. The adapters also stop on an unknown upstream label.

When a new version of a static source is published, follow the docstring of
its module (`src/tracker/leiden.py`): update the file URL, ingest, read the
diff of the CSV, and update the version in the citation and in the credits.

## Add a source

1. Check the licence. It must allow redistribution under CC BY 4.0, because
   the stored data is published under CC BY 4.0. Write down the citation.
2. Write `src/tracker/<name>.py`. It defines `fetch()`, which returns a
   DataFrame in the columns of `tracker.schema.COLUMNS`. Put the mapping in a
   `normalize()` function, so tests can run it without network. Raise on an
   unknown upstream label. Use the existing adapters as examples.
3. Import the module in `src/tracker/sources.py` and add a `Source` entry:
   title, `kind`, homepage, licence, citation, `fetch`. The key is lowercase.
   It is the CSV name, the `source` value, the prefix of `event_id`
   (`<name>:<id>`) and the ingest target. Do not use `check`, `live` or
   `static`.
4. Add any new package to `requirements.txt`, pinned.
5. For a new category, status or link value: add it to `src/tracker/schema.py`
   and to the labels and colours of `src/streamlit_app.py`.
6. Run `python -m tracker.ingest <name>`, then `python -m tracker.ingest check`.
   Commit `data/events/<name>.csv`.
7. Add `tests/fixtures/events/<name>.csv`: a few rows in the stored format. Add
   `tests/test_<name>.py` with a recorded upstream sample: the mapping, and the
   stop on an unknown label.
8. Credit the source in `README.md`: a row in the source table, with the
   homepage, and a line in "Licence and credits", with the citation and the
   licence. Add the citation and the homepage to the dataset card,
   `data/events/README.md`. If the scope grows, update `size_categories` and
   the intro of the card. `tests/test_attribution.py` checks the credits.
9. Run `python scripts/architecture.py` and commit `docs/architecture.md`.
10. A live source that needs a secret: add `env:` to the "Refresh live
    sources" step and set the secret in the repository.
11. Run the checks below. After the merge, start a manual run of a live
    source (see "Run the pipeline by hand"): a pull request does not run the
    refresh.

## Run the checks

In the order of the pipeline:

```bash
ruff check . && ruff format --check .
python -m tracker.ingest check
pytest
python scripts/architecture.py --check
sphinx-build -W --keep-going -b html docs docs/_build/html
```

`pytest` includes the dashboard, run headless on the stored data and on
samples. The check of `docs/architecture.md` prints the command that fixes it.
The `docs/` build runs with `-W`: a warning fails it.

A pull request runs the same checks. It does not refresh the data, build the
image or deploy: a broken adapter fails on the next scheduled or manual run,
and a broken `Dockerfile` fails on `main`.

## Pipeline

`.github/workflows/pipeline.yml` runs on each push to `main`, on each pull
request, each day at 04:17 UTC, and by hand. It has six jobs: Lint, Data, Docs,
Docker image, Deploy to Render and Hugging Face mirror. The
[architecture page](architecture.md) shows how they connect and what each one
does.

- On a scheduled or manual run, Data refreshes the live sources, validates and
  tests the data, and commits the changes to `data/events`. The commit does not
  start a new run: the image and the mirror use that commit in the same run.
- The daily commits come from a bot. Pull before you create a branch.
- The image tag is a hash of the files that the `Dockerfile` copies:
  `Dockerfile`, `requirements.txt`, `.streamlit`, `src` and `data`. A change to
  any of them, the dataset card included, gives a new tag. The same inputs give
  the same tag, and nothing is pushed to Docker Hub. `latest` follows the newest
  image.
- Render redeploys the tag of the run on every `main` run, also when the tag
  did not change.
- The Docker image, Deploy and mirror jobs run on `main` only. Each one does
  nothing until its variable or secret is set.

### Run the pipeline by hand

Run `gh workflow run pipeline.yml`, or use Actions, pipeline, Run workflow. On
a branch other than `main`, the refresh commits the data to that branch.

### Schedule stops after 60 days

GitHub disables a scheduled workflow after 60 days without activity in the
repository. Enable it again with `gh workflow enable pipeline.yml`, or in
Actions, pipeline, Enable workflow.

## Settings

In the GitHub repository settings, Secrets and variables, Actions:

| Name | Kind | Value |
|---|---|---|
| `DOCKER_IMAGE` | variable | Public Docker Hub repository, `<user>/<repository>`. The namespace must be the Docker Hub login: the job logs in with it. |
| `DOCKERHUB_TOKEN` | secret | Docker Hub access token with Read and Write scope |
| `RENDER_DEPLOY_HOOK_URL` | secret | Deploy hook of the Render service (Settings, Deploy Hook) |
| `HF_DATASET` | variable | `<user>/<dataset>` on Hugging Face |

The Render service is a web service created from the image
`docker.io/<user>/<repository>:latest`. It runs on the free plan, with
`PORT=8501` and the health check path `/_stcore/health`. It sleeps after 15
minutes without visits, so the next visit waits about a minute.

The mirror needs no secret. In the Hugging Face dataset settings, add a
Trusted Publisher for this repository, branch `main` and workflow
`pipeline.yml`. Renaming the repository, the branch or the workflow file breaks
the trust: update the Trusted Publisher.

The Docs are built on Read the Docs from the `docs/` folder. The project
has pull request previews on.

## Runbook

A failed run shows a red job in Actions. Read the log of that job first.

| Failed job | Likely cause | Action |
|---|---|---|
| Lint | Style error | Run the ruff commands above and fix. |
| Data, refresh | The upstream format changed, an unknown label, the shrink guard, an outage | Old data stays and the later jobs skip. Run `python -m tracker.ingest <name>` locally and read the error. Fix the adapter, or wait for the upstream, then run the pipeline by hand. |
| Data, validate or tests | A hand edit of a CSV, or an adapter bug. The error names the column. | Find the cause, fix it, push. |
| Data, commit | A push from another run in between | Run the pipeline again. |
| Docs, architecture | The page does not match the code | Run `python scripts/architecture.py` and commit the page. |
| Docs, build | A docstring or Markdown error, or a changed `schema.py` | Run the `sphinx-build` command above and read the warning. |
| Docker image | Expired token, wrong namespace, Docker Hub outage | Check `DOCKERHUB_TOKEN` and `DOCKER_IMAGE`, then run the job again. |
| Deploy to Render | Hook rejected (HTTP 4xx), or the app does not start | `curl -f` checks only the HTTP status of the hook. For a 4xx, create a new hook and set the secret again. Else read the Render logs and check `PORT` and the health check path. |
| Hugging Face mirror | Trusted Publisher not matching, Hugging Face outage, "Mirror check failed" | Check the Trusted Publisher settings, then run the job again. The upload is idempotent. |

## Rotate a credential

- Docker Hub token: create a new token with Read and Write scope, run
  `gh secret set DOCKERHUB_TOKEN` (it prompts for the value), then delete the
  old token.
- Render hook: create a new deploy hook in Render, run
  `gh secret set RENDER_DEPLOY_HOOK_URL`, then delete the old hook.

## Roll back

- App: deploy an earlier image. Make the call that the Deploy job makes, with
  an earlier tag from Docker Hub:
  `curl -fsS --get --data-urlencode "imgURL=docker.io/<user>/<repository>:<tag>" "$RENDER_DEPLOY_HOOK_URL"`.
  The next `main` run deploys the new tag again.
- Data: `git revert` the commit on `main`.
