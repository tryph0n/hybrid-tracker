# 0003. Storage layout, licence families and access

Status: Accepted.
Date: 2026-11-25.

## Context

The daily bot commits fill the git history, and git has no room for news
volumes. The next tickets of this milestone write to Hugging Face stores, so
the names, folders and access of those stores are fixed first. Each payload is
kept as fetched, so that a mapping fix can be replayed and an upstream edit
leaves a trace.

A source under a NonCommercial licence cannot be merged into a CC BY table, so
the public data is split in two datasets.

Facts that the decision depends on:

- One folder of a Hugging Face repository holds at most 10,000 entries
  ([storage limits](https://huggingface.co/docs/hub/storage-limits)).
- A git-backed repository keeps deleted files in its history, and deleting a
  file does not free its storage. A free account has 100 GB of private
  storage, history included.
- A revision name with `/` must be URL-encoded in an `hf://` path
  ([HfFileSystem guide](https://huggingface.co/docs/huggingface_hub/guides/hf_file_system)).
- A Trusted Publisher gives a workflow run a token with write access to one
  repository for 60 minutes. The claims `repository`, `branch` and `workflow`
  (the file name) must match exactly. The job needs `id-token: write`, and
  each step sets `HF_OIDC_RESOURCE=datasets/<namespace>/<name>`
  ([Trusted Publishers](https://huggingface.co/docs/hub/trusted-publishers)).
- A publisher token is scoped to an existing repository, so a run cannot
  create a store. Managing publishers needs the Write role on the repository.
- The documentation does not say if Trusted Publishers work on a private
  repository, or if one repository can hold two publisher entries. The probe
  run below tested both: they work.

## Decision

We keep the data in three Hugging Face stores, in the namespace of the current
dataset (`sam-bot`), and we sign in from CI with a Trusted Publisher on each
store, with no stored secret.

### Stores

| Store | Visibility | Licence | Holds |
| --- | --- | --- | --- |
| `hybrid-tracker-raw` | Private | None: never published | Payloads exactly as fetched, news article metadata, the clean table of each source |
| `europe-hybrid-incidents` | Public | CC BY 4.0 | Events from CC BY, CC0, public-domain and open-government sources, and events that the project extracts |
| `europe-hybrid-incidents-nc` | Public | CC BY-NC 4.0 | All of the above, plus events from NonCommercial sources |

The raw store is private and has no licence, because it holds third-party
payloads that the project does not republish. Signed out, the page of the NC
dataset opens and the page of the raw store does not.

### Licence families

- `open`: the sources of the CC BY dataset.
- `nc`: NonCommercial sources.

Each registry entry carries its family, because the family decides which
public dataset gets the rows: `open` rows go to both public datasets, `nc`
rows go to the NC dataset only.

### Folder layout

One folder holds at most 10,000 entries, so the raw store is sharded by date.

- Payloads: `raw/<source>/<YYYY>/<MM>/<DD>/<file>`, as fetched.
- News article metadata: `news/<source>/<YYYY>/<MM>/<DD>/<file>`.
- Clean tables: `clean/<source>.parquet`, one file per source.

Each public dataset keeps its published files at the root of the repository,
as the current dataset does.

### File names of the published tables

`events.parquet`, `events.csv`, `sources.parquet`, `quality.parquet`, and the
dataset card `README.md`. The two public datasets use the same names.

### Versions of the public datasets

- A schema change creates the tag `v<SCHEMA_VERSION>`. A daily run moves only
  `main`. Tag names have no `/`.
- A tag is never moved or recreated, because nothing disappears in v2 and a
  reader who pins a tag must get the same data. The tag `v1.0.0` of the
  dataset v2 release (#37) holds the data before schema v2.
- Each dataset card lists the dataset versions in a "Versions" section. Its
  source is in the repository, because the card travels with the data: this
  section is the dataset history that users see. The release 1.0.0 (#87)
  relies on it. `CHANGELOG.md` is the code changelog, which the changelog,
  version and first tag ticket (#26) keeps by hand: it lists code versions
  only.

### History of the raw store

The raw store keeps its history: no squash and no purge. The history holds the
payloads of a source that stops, and the trace of each upstream edit.

### Article text

Article text is never stored. A git-backed repository keeps deleted files in
its history, so a retention period would not be real. If text must be kept one
day, the alternative is a Storage Bucket, where "deletions are immediate and
permanent" ([Storage Buckets](https://huggingface.co/docs/hub/storage-buckets)).

### Access

Access is keyless, through a Trusted Publisher on each store: repository
`tryph0n/hybrid-tracker`, branch `main`, workflow `pipeline.yml`. The fallback
is one fine-grained token with write access to one store only. The "CI/CD
identity" account is not used: it is read-only and cannot read private
repositories.

Probe: a temporary workflow (`storage-probe.yml`, run from the branch of the
pull request) wrote one small file to `hybrid-tracker-raw` with no stored
secret: [run 45636190013](https://github.com/tryph0n/hybrid-tracker/actions/runs/45636190013), upload [commit 3b43e1a](https://huggingface.co/datasets/sam-bot/hybrid-tracker-raw/commit/3b43e1ae3d07a27793f9c4571bc9dae27c617979).
The store is private, so Trusted Publishers work on a private repository. The
store held the permanent entry and the temporary entry of the probe at the same
time, so one repository holds two publisher entries.

## Alternatives rejected

- Keep the data in git. The daily bot commits fill the history, and git has no
  room for news volumes.
- One public dataset for all sources. A NonCommercial source cannot be merged
  into a CC BY table, so the whole dataset would have to be NonCommercial.
- A flat folder for the raw payloads. One folder holds at most 10,000 entries.
- Store article text with a retention period. A git-backed repository keeps
  deleted files in its history, so the deletion would not be real.
- A fine-grained token as the main access. A secret can leak and must be
  rotated. It stays as the fallback only.
- A Storage Bucket for the raw store. Not needed while no article text is
  stored; it is the alternative if text must be kept.

## Consequences

- The next tickets of the milestone can write to the stores with no secret in
  the repository.
- The data run needs `id-token: write` and must run the file `pipeline.yml` on
  `main`. Renaming the workflow file or the branch breaks the access until the
  publisher entries are changed.
- A run from a fork or a pull request gets no OIDC token, so it cannot write
  to a store.
- The raw store only grows: deleting a file does not free its storage, and the
  limit is 100 GB of private storage. Review the decision when the raw store
  reaches half of it, or if Hugging Face changes its limits.
- Review the access if Trusted Publishers stop working on a private
  repository: the fallback token then applies.
- Each registry entry needs a family, and the code that fills the public
  datasets must follow it.
