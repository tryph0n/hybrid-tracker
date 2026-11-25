# Maintainer guide

This page is for the person who runs the project. For the data and the
dashboard, see the [home page](index.md).

## Set up

Install [uv](https://docs.astral.sh/uv/). The project is a package
(`pyproject.toml`) with a
lockfile (`uv.lock`). One command installs the package, the runtime
dependencies and the `dev` group, with the versions of the lockfile, in a
virtual environment on Python 3.13:

```bash
uv sync --locked
```

The Docs job and Read the Docs also use the `docs` group. For the docs build,
run `uv sync --locked --group docs`. Run every command below with `uv run`
(for example `uv run pytest`). No environment variable is needed.

One more command installs the Git hooks. They run Ruff (check with `--fix`,
then format) and file checks before each commit, with the same Ruff version as
the lockfile:

```bash
uv run pre-commit install
```

Run all hooks on every file with `uv run pre-commit run --all-files`. When
Dependabot bumps Ruff in `uv.lock`, set the `rev` of `ruff-pre-commit` in
`.pre-commit-config.yaml` to the same version. A test fails if they differ.

## Layout

- `src/tracker/` is the package. It holds the sources, the schema and the
  ingest. It does not import Streamlit.
- Only the app imports Streamlit (`src/streamlit_app.py`). Keep it so: the
  front end must stay easy to replace later.
- `src/streamlit_app.py` is a script, not part of the package. The image copies
  `src/`.

## Sources and ingest

A source is live or static (`Source.kind` in `src/tracker/sources.py`).

- A live source is fetched in full at each run and replaces the stored file.
  The pipeline refreshes it each day.
- A static source is a frozen historical snapshot, pinned to one upstream file
  version. You re-ingest it by hand.

Each source is stored as `data/events/<name>.csv`. Commands, from the
repository root:

```bash
uv run python -m tracker.ingest live      # every live source
uv run python -m tracker.ingest static    # every static source
uv run python -m tracker.ingest <name>    # one source, for example eodi
uv run python -m tracker.ingest check     # validate the stored files, no network
```

`ingest` validates the events before it writes the file. It rejects a refresh
that loses more than 5% of the stored events (`MAX_SHRINK` in
`src/tracker/ingest.py`): an upstream outage is more likely than a real
correction. An unknown upstream label also stops the ingest, in the adapter or
in `validate`.
If the drop is a real correction, delete `data/events/<name>.csv`, run
`uv run python -m tracker.ingest <name>`, read the diff and commit.

When a new version of a static source is published, follow the docstring of
its module (`src/tracker/leiden.py`). For the Leiden dataset:

1. Run `uv run python -m tracker.ingest leiden`. It reads the latest version
   from Harvard Dataverse and stops, with a message, when the licence is not
   "CC BY 4.0" or when the version is not `PINNED_VERSION`. Do not go on if
   the licence changed: the stored data is published under CC BY 4.0.
2. Read the new version and its sheet for new labels. An unknown label or an
   unknown "IncidentDateEnd" text also stops the run: add it to the adapter on
   purpose.
3. Set `PINNED_VERSION` in `src/tracker/leiden.py` and run the ingest again.
4. Read the diff of the CSV, then update the version in the `citation` of
   `src/tracker/sources.py`, in the README credits and in the dataset card
   (`data/events/README.md`). If the years change, also update the `title` and
   the README source table, and the version in the docstring of the module.

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
4. Add any new package with `uv add <package>`, then pin it as `name==version` in
   `pyproject.toml` and run `uv lock`. Commit `pyproject.toml` and `uv.lock`.
5. For a new domain, category, status or attribution value: add it to
   `src/tracker/schema.py` and to the labels of `src/streamlit_app.py` (for a
   domain, also the two colour lists, in the same order, and run the
   colour-blind check again). For a status or attribution value, also update
   the list in `README.md`, "Reading the data".
6. If the source needs a secret, export it locally first. Run
   `uv run python -m tracker.ingest <name>`, then `uv run python -m tracker.ingest check`.
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
10. Run `uv run python scripts/architecture.py` and commit `docs/architecture.md`.
11. Run the checks below. After the merge of a live source, run the pipeline
    by hand (see "Run the pipeline by hand"): a pull request does not run the
    refresh.

## Change the schema

A new or renamed column changes every stored file. Update every adapter, run
`uv run python -m tracker.ingest live` and `uv run python -m tracker.ingest static`
(network needed), and update `tests/fixtures/events/` and the `HEADER` in
`tests/test_ingest.py`. `docs/conf.py` reads
`schema.py` without importing it: keep `COLUMNS`, `REQUIRED` and the lists of
`ALLOWED_VALUES` as literal lists, and one comment on each column line.
A renamed column also changes `src/streamlit_app.py`, and for `event_status`
or `attribution`, the "Reading the data" section of `README.md`.

Raise `SCHEMA_VERSION` in `src/tracker/schema.py`: a removed or renamed column
is a major version. Add an entry with the mapping from the old version to the
`Versions` section of `data/events/README.md`. The mirror job tags the dataset
`v<SCHEMA_VERSION>` on the commit of the first upload with that version. Check
the tag after the first run on `main`: if the job warns that it could not create
the tag, create it by hand (the tag is never moved or recreated):

```bash
hf repos tag create <HF_DATASET> v<SCHEMA_VERSION> --repo-type dataset --revision <upload commit>
```

## Record a decision

A pull request needs a decision record in `docs/decisions/` when it adds:

- a new service,
- a new storage layer,
- a major version of the schema, or
- a new framework.

Copy `docs/decisions/template.md`, fill in the four parts (context, decision,
alternatives rejected, consequences) and add the file to the `toctree` of
`docs/decisions/index.md`. A decision that changes later gets a new record,
and the old record gets the status `Superseded by NNNN`.

## Contribution rules

These rules are for the maintainer's own pull requests, one per issue. Outside
pull requests are not accepted. The pull request template in `.github/`
reminds you of the checks.

- Write code, commits, issues and docs in English.
- Write each pull request title, and so each squash commit, as a
  [Conventional Commit](https://www.conventionalcommits.org/en/v1.0.0/):
  `type(scope): description`.
- Choose the simplest solution that works, one that a junior developer can
  read.
- Add no new dependency without a reason in the pull request.
- Use no emojis.

## Run the checks

In the order of the pipeline:

```bash
uv sync --locked --group docs
uv run ruff check . && uv run ruff format --check .
uv run --locked mypy
uv run python -m tracker.ingest check
uv run pytest
uv run python scripts/architecture.py --check
uv run sphinx-build -W --keep-going -b html docs docs/_build/html
```

`uv run --locked mypy` checks `src/tracker` in strict mode and the app in
standard mode; an ignore needs its error code and a reason on the same line.
`uv run pytest` includes the dashboard, run headless on the stored data and on
samples. The check of `docs/architecture.md` prints the command that fixes it.

To print the branch coverage of `src/tracker` with the missing lines, run
`uv run pytest --cov`. The run fails when the total is under `fail_under` in
`pyproject.toml` (90). The CI run writes the per-file table in its job summary.
`src/streamlit_app.py` is not counted: the app test runs it as a script, so it
is not an imported module.
The `docs/` build runs with `-W`: a warning fails it.

A pull request runs the same checks, and the Docker build check builds the image
and starts it. It does not refresh the data or deploy: a broken adapter fails
on the next scheduled or manual run.

## Pipeline

`.github/workflows/pipeline.yml` runs on each push to `main`, on each pull
request, each day at 04:17 UTC, and by hand. It has seven jobs: Lint, Data, Docs,
Docker build check, Docker image, Deploy to Render and Hugging Face mirror. The
[architecture page](architecture.md) shows how they connect and what each one
does.

- On a scheduled or manual run, Data refreshes the live sources, validates and
  tests the data, and commits the changes to `data/events`. The commit does not
  start a new run: the image and the mirror use that commit in the same run.
- The daily commits come from a bot. Pull before you create a branch.
- The image tag is a hash of the `Dockerfile` and of every file the build
  reads: `README.md`, `pyproject.toml`, `uv.lock`, `.streamlit`, `src` and
  `data`. The packages come from `uv.lock`, so a lockfile change gives a new
  tag. A change to any of them, the dataset card included, gives a new tag.
  A new `COPY` line or a new bind mount in the `Dockerfile` must also go in
  the `git ls-tree` line of the Docker image job (`tests/test_image.py` checks it) and in the tag command of
  [Roll back](#roll-back). The same inputs give
  the same tag, and nothing is pushed to Docker Hub. `latest` moves only when a
  new tag is built. After a revert to an earlier tag, `latest` stays on the
  newer image.
- The mirror job uploads `data/events/` to the dataset, then downloads that
  dataset commit and compares each file. A missing or different file fails the
  job.
- Render redeploys the tag of the run on every `main` run, also when the tag
  did not change. The job then polls
  `/app/static/version.txt` on the public address for 10 minutes at most, until
  it shows that tag. The image writes the file from the build argument
  `IMAGE_TAG`. The poll also wakes a sleeping free service, and the job is
  skipped with the deploy when the secret is not set.
- The Docker build check runs on pull requests only. It builds the image
  without pushing it, starts the container, and waits for `/_stcore/health` to
  answer 200. It needs no secret, and it is not a required check yet.
- On a new tag, the Docker image job builds the image, scans it with Trivy and
  writes the full report in the job summary. A critical finding that has a fix
  fails the job before the push. An unfixed finding does not. The job then
  pushes the tag with a build provenance attestation, and moves `latest` to
  it only after the attestation succeeds. To check it, run
  `gh attestation verify oci://index.docker.io/<DOCKER_IMAGE>:<tag> --repo tryph0n/hybrid-tracker`.
  A tag that is already published and attested is not built again. A
  published tag without a valid attestation, such as a tag from before the
  attestation existed or after a failed attestation step, is built, scanned,
  pushed and attested again from the new run. Run the pipeline again after a
  failed attestation step: `latest` has not moved.
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
| `HEARTBEAT_URL` | secret | Ping address of the heartbeat monitor (see "Alerts"). Until it is set, the Heartbeat job is green and sends nothing. |

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
| Lint | Style error | Run `uv run ruff check --fix . && uv run ruff format .` and commit. |
| Data, refresh | The upstream format changed, an unknown label, the shrink guard, an outage | Old data stays and the later jobs skip. Run `uv run python -m tracker.ingest <name>` locally and read the error. Fix the adapter, or wait for the upstream, then run the pipeline by hand. For a real correction, see [Sources and ingest](#sources-and-ingest). |
| Data, validate or tests | A hand edit of a CSV, or an adapter bug. The error usually names the column. | Find the cause, fix it, push. |
| Data, commit | A push to main during the run changed the same data file, so the rebase stopped; or a branch rule on main blocks the bot | Run the pipeline again. Keep main open to pushes from github-actions[bot]. |
| Docs, architecture | The page does not match the code | Run `uv run python scripts/architecture.py` and commit the page. |
| Docs, build | A docstring or Markdown error, or a changed `schema.py` | Run the `uv run sphinx-build` command above and read the warning. |
| Docker build check | A broken `Dockerfile`, a missing file in a `COPY`, an app that does not start or does not answer on `/_stcore/health` | Read the log: it shows the build error or the container log. Run `docker build .` locally and fix it. |
| Docker image | A broken `Dockerfile` that the check missed, an expired token, a wrong namespace, a Docker Hub outage | For a build error, run `docker build .` locally and fix it. Else check `DOCKERHUB_TOKEN` and `DOCKER_IMAGE`, then run the job again. |
| Deploy to Render | Render rejected the hook call: 404, the hook URL is old or wrong; 400, the image is not in the repository of the Render service; 409, the service is suspended or the workspace cannot deploy. Or the step "Wait for the new image to answer" failed: the app did not serve the tag of the run at `/app/static/version.txt` within 10 minutes, because the new image does not start or Render keeps the old one | 404: copy the hook from Render (Settings, Deploy Hook) and set the secret again. 400: make `DOCKER_IMAGE` the repository of the Render service. 409: resume the service. Wait failure: read the deploy logs in Render, check `PORT` and the health check path, and open the app address to see which tag it serves. Then run the job again. |
| Hugging Face mirror | `invalid_grant`: the Trusted Publisher claims do not match, or `HF_DATASET` names another dataset; or a Hugging Face outage | Check the Trusted Publisher settings, then run the job again. The upload is idempotent. |
| Hugging Face mirror | "Mirror check failed": the downloaded dataset commit does not hold the same bytes as `data/events/`: a file that the upload skipped, or that differs on the Hub | Run the job again, then compare the files on the Hub. |
| Read the Docs | A warning or error in the docs build on Read the Docs. On a pull request it shows as a failed check; for `latest`, see Builds in the Read the Docs project. | `latest` stays on the previous version. Fix the docs and push. |

A green Deploy job means that Render accepted the deploy and that the app
serves the tag of the run. When the secret is not set, the job is green and
checks nothing.

## Alerts

Two signals tell the owner that the data does not refresh.

- A failed scheduled or manual run opens one issue with the label
  `pipeline-failure` and the link of the run. While that issue is open, each
  new failure adds a comment to it. The next successful scheduled run closes
  it. A green manual run does not. A pull request or a push never opens or
  closes it.
- A successful scheduled run pings a heartbeat monitor (an OpenStatus heartbeat
  with a period of 1 day and a grace of 1 day), with the address in
  `HEARTBEAT_URL`. A manual run does not ping. The monitor alerts the owner
  when no ping arrives for 48 hours. A run that GitHub drops or disables fails
  nothing and opens no issue: only this alert shows it.

On a failure issue, open the linked run and follow the table above. On a
heartbeat alert, check Actions: if the schedule is off, see "Schedule stops
after 60 days". If the last scheduled run is green, check that
`HEARTBEAT_URL` is the address of the monitor, then run the pipeline by hand
on `main` and wait for the next scheduled run.

## Rotate a credential

- Docker Hub token: create a new token with Repo Read & Write scope, run
  `gh secret set DOCKERHUB_TOKEN` (it prompts for the value), then delete the
  old token.
- Heartbeat address: in the monitor, create a new heartbeat or rotate its
  address, then run `gh secret set HEARTBEAT_URL`.
- Render hook: in Render, Settings, Deploy Hook, click Regenerate Hook. The old
  URL stops working at once. Copy the new URL and run
  `gh secret set RENDER_DEPLOY_HOOK_URL`.

## Roll back

- App: deploy an earlier image. Copy the hook URL from Render (Settings,
  Deploy Hook), take an earlier tag from Docker Hub, and run:
  `curl -fsS --get --data-urlencode "imgURL=docker.io/<user>/<repository>:<tag>" "<deploy hook URL>"`.
  The tag of a commit is the output of
  `git ls-tree <commit> Dockerfile README.md pyproject.toml uv.lock .streamlit src data | sha256sum | cut -c1-12`.
  This lasts until the next `main` run that reaches the Deploy job, usually
  the daily run at 04:17 UTC. To keep the earlier version, `git revert` the bad change on `main`.
- Data: `git revert` the commit on `main`. For a live source, the next refresh
  fetches the upstream again, so the revert lasts only until the next
  scheduled or manual run.

## Release

The version is written by hand in `pyproject.toml` only. The package reads it
from its metadata (`tracker.__version__`).

1. In one pull request, set `version` in `pyproject.toml`, run `uv lock`, move the `Unreleased` items of `CHANGELOG.md` under `## [X.Y.Z] - date`, and update the links at the end of that file.
2. Squash-merge it with a Conventional Commits title, then pull `main`.
3. Tag the squash commit: `git tag -a vX.Y.Z -m "vX.Y.Z" && git push origin vX.Y.Z`.
4. Publish the release: `gh release create vX.Y.Z --generate-notes`.
5. Check that the release page shows the notes. `.github/release.yml` sets their categories from the labels `enhancement` and `bug`.

## Security

Every action in `.github/workflows` is pinned to a full commit SHA, with the
full version in a comment on the same line, for example
`actions/checkout@<sha> # v7.0.1`. A tag can be moved to other code, a SHA
cannot. Dependabot updates both the SHA and the comment. To add an action, take
the commit SHA of the tag (for an annotated tag, the commit it points to) and
write the version as `# vX.Y.Z`.

These repository settings are on: code scanning default setup (languages
`python` and `actions`), secret scanning with push protection, and private
vulnerability reporting. Code scanning uses the default setup, so do not add a
CodeQL workflow: the two are mutually exclusive. The policy is in
[`SECURITY.md`](https://github.com/tryph0n/hybrid-tracker/blob/main/SECURITY.md)
at the repository root. Keep it outside `docs/`, because Sphinx warns about a
document that is in no `toctree`, and the docs build fails on a warning.
