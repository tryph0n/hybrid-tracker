# Changelog

All notable changes to this project are written in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
The version follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
The version number is set by hand in `pyproject.toml` only.

## [Unreleased]

### Added

- Schema 2.6.0: the column `drone_presence` (`confirmed`, `not_confirmed`,
  `not_substantiated`, `ruled_out`) holds the EODI field `presencia_dron`. It is
  empty for Leiden. The build reads a stored clean table that has no such column as
  empty.
- Under the map of the Incidents view, a bar chart shows the incidents with a country and
  no point on the map: one bar for each state of the European list, stacked by domain.
  The part of a bar that comes from incidents that name several countries is hatched.
  The split line points to the bars ("bars below") only when the chart shows, and the
  message for a selection with no point no longer sends the reader to the Countries view.
  A caption under "Most affected countries" says that it counts the incidents with or
  without a point.
- The Sources table of the dashboard has the columns "Downloaded" (minute, UTC) and
  "Updated by source" (day or minute, UTC), read from `quality.parquet`. A source
  with no date, or a missing quality table, shows an empty cell.
- Threat-group lookup (schema 2.3.0): the open dataset has a `threat_actors` table (group,
  aliases, country of origin, suspected state sponsor), reduced from the MISP
  galaxy threat-actor cluster at a pinned commit. `curation/actors.csv` adds the
  groups that the cluster leaves without a country, with the page that links
  the group to it, the page that calls it pro-Russian and the exact words of
  both (columns `country_*` and `stance_*`; the card has a "Curated countries"
  section). A name that two groups
  carry resolves to nothing.
- Clusters and canonical records (schema 2.2.0, record 0004): the published
  tables have the columns `cluster_id`, `n_sources` and `is_canonical`, and
  the dashboard counts incidents, not rows. The curation files are
  `curation/merges.csv` and `curation/splits.csv`. RapidFuzz is a new
  dependency.
- The Events table and the map hover show the drone presence ("Confirmed", "Not
  confirmed", "Not substantiated", "Ruled out"). A published table that has no
  `drone_presence` column shows it empty. The help of the Event status filter says that
  a dismissed event is one that a source or an authority denies.

### Changed

- The `event_status` of an EODI row comes from the EODI field `estado`, no longer
  from the drone presence: an incident with a ruled-out drone is `confirmed`, not
  `dismissed`. The app does not show the new column yet.
- The KPI "Countries", the Countries view and "Most affected countries" count only
  the 43 states of the European list of decision record 0002. The help of the KPI
  names the other states that the incidents name, with their counts.
- The dashboard shows one line for each incident, with the value of each source
  for its domain, category, status, attribution, actors, countries and link.
  The source filter sets the scope, a filter on one source's fields keeps an
  incident when one row passes all of them together, and an incident counts in
  each domain that one of its sources gives it (record 0004, Counts).
- The data left git: `data/events/` is removed, `tracker ingest` writes only to
  the raw store, `tracker ingest check` is removed, and the pipeline no longer
  commits to `main`. The two public datasets hold the data.
- The dashboard reads the published `events.parquet` of the NonCommercial
  dataset from the Hugging Face Hub, cached for one hour, at the revision set by
  `HYBRID_TRACKER_REVISION` (`main` by default). It falls back to a snapshot
  bundled in the image and shows the revision and its date in the footer.
- The adapters and the dashboard use schema v2 (dataset 2.0.0): `domain`,
  `actors`, `attribution` and `attributed_by` replace `russia_link`. See the
  [`Versions` section](data/events/README.md#versions) of the dataset card for
  the mapping and for the tag `v1.0.0` of the old version.

## [0.1.0] - 2026-11-25

First tagged version. It describes the state of the project at that date.

### Added

- Open data on events of the hybrid war against Europe since 2022, stored as
  one CSV file per source in `data/events/`, with a shared schema.
- Ingest of the live source EODI and of the static source Leiden, with
  validation and a guard against a large loss of events.
- A Streamlit dashboard over the stored data.
- A daily pipeline that refreshes the live sources, checks the data and the
  docs, and publishes a Docker image.
- Deploy to Render, with a check that the live app serves the new image, and a
  mirror of the data to a public Hugging Face dataset.
- Alerts: an issue on a failed run and a heartbeat ping on a successful run.
- Documentation on Read the Docs, with a maintainer guide.

[Unreleased]: https://github.com/tryph0n/hybrid-tracker/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/tryph0n/hybrid-tracker/releases/tag/v0.1.0
