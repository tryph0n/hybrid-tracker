# Changelog

All notable changes to this project are written in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
The version follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
The version number is set by hand in `pyproject.toml` only.

## [Unreleased]

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
