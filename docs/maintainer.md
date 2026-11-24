# Maintainer guide

This page is for the person who runs the project. For the data and the
dashboard, see the [home page](index.md).

## Set up

Use Python 3.13 and a virtual environment. The install line is the one of the
`Docs` job in `.github/workflows/pipeline.yml`:

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
correction. An unknown upstream label also stops the ingest, in the adapter or
in `validate`.
If the drop is a real correction, delete `data/events/<name>.csv`, run
`python -m tracker.ingest <name>`, read the diff and commit.

When a new version of a static source is published, follow the docstring of
its module (`src/tracker/leiden.py`): update the file URL, ingest, read the
diff of the CSV, and update the version in the `citation` of
`src/tracker/sources.py`, in the README credits and in the dataset card
(`data/events/README.md`). If the years change, also update the `title` and the
README source table. Change the pinned version in the docstring too. The `event_id` values are
spreadsheet rows, so they can move in a new version.

## Add a source

1. Check the licence. It must allow redistribution under CC BY 4.0, because
   the stored data is published under CC BY 4.0. Write down the citation.
2. Write `src/tracker/<name>.py`. It defines `fetch()`, which returns a
   DataFrame in the columns of `tracker.schema.COLUMNS`. Put the mapping in a
   `normalize()` function, so tests can run it without network. An unknown
   upstream label must stop the ingest: raise, or leave the cell empty so that
   `validate` rejects it. Start the module docstring with `<Title> - <kind>
   source.`: the architecture page shows that first line. Use the existing
   adapters as examples.
3. Import the module in `src/tracker/sources.py` and add a `Source` entry:
   title, `kind`, homepage, licence, citation, `fetch`. The key is lowercase.
   It is the CSV name, the `source` value, the prefix of `event_id`
   (`<name>:<id>`) and the ingest target. Do not use `check`, `live` or
   `static`.
4. Add any new package to `requirements.txt`, pinned.
5. For a new category, status or link value: add it to `src/tracker/schema.py`
   and to the labels of `src/streamlit_app.py` (for a category, also the two
   colour lists, in the same order). For a status or link value, also update
   the list in `README.md`, "Reading the data".
6. If the source needs a secret, export it locally first. Run
   `python -m tracker.ingest <name>`, then `python -m tracker.ingest check`.
   Commit `data/events/<name>.csv`.
7. Add `tests/fixtures/events/<name>.csv`: a few rows in the stored format. Add
   `tests/test_<name>.py` with a recorded upstream sample in
   `tests/fixtures/<name>_<file>`: the mapping, and the stop on an unknown
   label.
8. Credit the source in `README.md`: a row in the source table, with the
   homepage, and a line in "Licence and credits", with the citation and the
   licence. Add the citation and the homepage to the dataset card,
   `data/events/README.md`. If the scope grows, update the intro of the card.
   If its events start before 2022, also update "since 2022" in the README
   description, the card intro and the app caption (`src/streamlit_app.py`).
   `tests/test_attribution.py` checks the credits.
9. A live source that needs a secret: add `env:` to the "Refresh live
   sources" step and set the secret in the repository.
10. Run `python scripts/architecture.py` and commit `docs/architecture.md`.
11. Run the checks below. After the merge of a live source, run the pipeline
    by hand (see "Run the pipeline by hand"): a pull request does not run the
    refresh.

## Change the schema

A new or renamed column changes every stored file. Update every adapter, run
`python -m tracker.ingest live` and `python -m tracker.ingest static`
(network needed), and update `tests/fixtures/events/` and the `HEADER` in
`tests/test_ingest.py`. `docs/conf.py` reads
`schema.py` without importing it: keep `COLUMNS`, `REQUIRED` and the lists of
`ALLOWED_VALUES` as literal lists, and one comment on each column line.
A renamed column also changes `src/streamlit_app.py`, and for `event_status`
or `russia_link`, the "Reading the data" section of `README.md`.

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
- The image tag is a hash of the `Dockerfile` and of the files it copies:
  `requirements.txt`, `.streamlit`, `src` and `data`. A change to
  any of them, the dataset card included, gives a new tag. A new `COPY` line in
  the `Dockerfile` must also go in the `git ls-tree` line of the Docker image
  job (`tests/test_image.py` checks it) and in the tag command of
  [Roll back](#roll-back). The same inputs give
  the same tag, and nothing is pushed to Docker Hub. `latest` moves only when a
  new tag is built. After a revert to an earlier tag, `latest` stays on the
  newer image.
- The mirror job uploads `data/events/` to the dataset, then downloads that
  dataset commit and compares each file. A missing or different file fails the
  job.
- Render redeploys the tag of the run on every `main` run, also when the tag
  did not change.
- The Docker image, Deploy and mirror jobs run on `main` only. Each one does
  nothing until its variable or secret is set.

### Run the pipeline by hand

Run `gh workflow run pipeline.yml`, or use Actions, pipeline, Run workflow. On
a branch other than `main`, run `gh workflow run pipeline.yml --ref <branch>`:
the refresh commits the data to that branch.

### Schedule stops after 60 days

In a public repository, GitHub disables a scheduled workflow after 60 days
without activity. Enable it again with `gh workflow enable pipeline.yml`, or in
Actions, pipeline, Enable workflow.

## Settings

In the GitHub repository settings, Secrets and variables, Actions:

| Name | Kind | Value |
|---|---|---|
| `DOCKER_IMAGE` | variable | Public Docker Hub repository, `<user>/<repository>`. The namespace must be the Docker Hub login: the job logs in with it. |
| `DOCKERHUB_TOKEN` | secret | Docker Hub access token with Repo Read & Write scope |
| `RENDER_DEPLOY_HOOK_URL` | secret | Deploy hook of the Render service (Settings, Deploy Hook). The service image must be the repository in `DOCKER_IMAGE`: Render rejects an image from another repository. |
| `HF_DATASET` | variable | `<user>/<dataset>` on Hugging Face |

The Render service is a web service created from the image
`docker.io/<user>/<repository>:latest`. It runs on the free plan, with
`PORT=8501` and the health check path `/_stcore/health`. It sleeps after 15
minutes without visits. Set `DOCKER_IMAGE` and `DOCKERHUB_TOKEN`, and let one
`main` run push the image, before you create the service. Then set
`RENDER_DEPLOY_HOOK_URL`.

The mirror needs no secret. Create the public dataset on Hugging Face first. In
its settings, add a Trusted Publisher with repository `tryph0n/hybrid-tracker`,
branch `main` and workflow `pipeline.yml`. Renaming the repository, the branch
or the workflow file breaks the trust: delete the Trusted Publisher and add it
again with the new values. Then set `HF_DATASET`.

The docs are built on Read the Docs (project `hybrid-tracker`) from the `docs/`
folder, with `.readthedocs.yaml`. It sets `fail_on_warning`, so a warning fails
that build too. The project has pull request previews on.

## Runbook

A failed run shows a red job in Actions. Read the log of that job first.

| Failed job | Likely cause | Action |
|---|---|---|
| Lint | Style error | Run `ruff check --fix . && ruff format .` and commit. |
| Data, refresh | The upstream format changed, an unknown label, the shrink guard, an outage | Old data stays and the later jobs skip. Run `python -m tracker.ingest <name>` locally and read the error. Fix the adapter, or wait for the upstream, then run the pipeline by hand. For a real correction, see [Sources and ingest](#sources-and-ingest). |
| Data, validate or tests | A hand edit of a CSV, or an adapter bug. The error usually names the column. | Find the cause, fix it, push. |
| Data, commit | A push to main during the run changed the same data file, so the rebase stopped; or a branch rule on main blocks the bot | Run the pipeline again. Keep main open to pushes from github-actions[bot]. |
| Docs, architecture | The page does not match the code | Run `python scripts/architecture.py` and commit the page. |
| Docs, build | A docstring or Markdown error, or a changed `schema.py` | Run the `sphinx-build` command above and read the warning. |
| Docker image | A broken `Dockerfile` (the build runs only on `main`), an expired token, a wrong namespace, a Docker Hub outage | For a build error, run `docker build .` locally and fix it. Else check `DOCKERHUB_TOKEN` and `DOCKER_IMAGE`, then run the job again. |
| Deploy to Render | Render rejected the hook call: 404, the hook URL is old or wrong; 400, the image is not in the repository of the Render service; 409, the service is suspended or the workspace cannot deploy | 404: copy the hook from Render (Settings, Deploy Hook) and set the secret again. 400: make `DOCKER_IMAGE` the repository of the Render service. 409: resume the service. Then run the job again. |
| Hugging Face mirror | `invalid_grant`: the Trusted Publisher claims do not match, or `HF_DATASET` names another dataset; or a Hugging Face outage | Check the Trusted Publisher settings, then run the job again. The upload is idempotent. |
| Hugging Face mirror | "Mirror check failed": the downloaded dataset commit does not hold the same bytes as `data/events/`: a file that the upload skipped, or that differs on the Hub | Run the job again, then compare the files on the Hub. |
| Read the Docs | A warning or error in the docs build on Read the Docs. On a pull request it shows as a failed check; for `latest`, see Builds in the Read the Docs project. | `latest` stays on the previous version. Fix the docs and push. |

A green Deploy job means only that Render accepted the deploy. If the dashboard
does not start, read the deploy logs in Render and check `PORT` and the health
check path.

## Rotate a credential

- Docker Hub token: create a new token with Repo Read & Write scope, run
  `gh secret set DOCKERHUB_TOKEN` (it prompts for the value), then delete the
  old token.
- Render hook: in Render, Settings, Deploy Hook, click Regenerate Hook. The old
  URL stops working at once. Copy the new URL and run
  `gh secret set RENDER_DEPLOY_HOOK_URL`.

## Roll back

- App: deploy an earlier image. Copy the hook URL from Render (Settings,
  Deploy Hook), take an earlier tag from Docker Hub, and run:
  `curl -fsS --get --data-urlencode "imgURL=docker.io/<user>/<repository>:<tag>" "<deploy hook URL>"`.
  The tag of a commit is the output of
  `git ls-tree <commit> Dockerfile requirements.txt .streamlit src data | sha256sum | cut -c1-12`.
  This lasts until the next `main` run that reaches the Deploy job, usually
  the daily run at 04:17 UTC. To keep the earlier version, `git revert` the bad change on `main`.
- Data: `git revert` the commit on `main`. For a live source, the next refresh
  fetches the upstream again, so the revert lasts only until the next
  scheduled or manual run.
