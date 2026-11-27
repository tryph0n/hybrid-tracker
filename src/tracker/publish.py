"""Publish the tables of `tracker build` to the two public Hugging Face datasets.

Run through the `tracker` command: `uv run tracker publish <family> [--build build]`,
after `tracker build` (and `tracker quality`). The `open` family goes to the
dataset of the `open` sources, the `nc` family to the NonCommercial one.

Each dataset gets one atomic commit: the files of `<build>/<family>/`, a card
generated from the source registry and from `cards/versions-<family>.md`, and
the deletion of the legacy per-source CSV files. The commit is then read back
from the Hub and each file is compared byte for byte (SHA-256) with the local
file. The version tag `v<SCHEMA_VERSION>` is created when it does not exist. A
tag is never moved, recreated or deleted. When the Hub refuses to create it,
the run warns and writes the tag and the commit in the run summary: the owner
creates the tag by hand.
"""

import hashlib
import logging
import tempfile
from pathlib import Path
from typing import Any

from tracker import actors, provenance
from tracker.build import CURATION_DIR, family_sources
from tracker.errors import UserError
from tracker.quality import write_summary
from tracker.schema import SCHEMA_VERSION
from tracker.sources import FAMILIES, REFERENCES, SOURCES

logger = logging.getLogger(__name__)

DATASETS = {
    "open": "sam-bot/europe-hybrid-incidents",
    "nc": "sam-bot/europe-hybrid-incidents-nc",
}
"""Hugging Face dataset of each licence family."""

LICENCES = {"open": "cc-by-4.0", "nc": "cc-by-nc-4.0"}
"""Licence keyword of the card of each family."""

LICENCE_LINKS = {
    "open": ("CC BY 4.0", "https://creativecommons.org/licenses/by/4.0/"),
    "nc": ("CC BY-NC 4.0", "https://creativecommons.org/licenses/by-nc/4.0/"),
}

LEGACY_FILES = {"open": ("eodi.csv", "leiden.csv"), "nc": ()}
"""Files that earlier versions of a dataset held on `main`. They stay readable
at the tags of those versions."""

CARDS_DIR = Path(__file__).resolve().parents[2] / "cards"
CARD_FILE = "README.md"
VERSIONS_HEADING = "## Versions"

TITLES = {
    "open": "Europe Hybrid Incidents",
    "nc": "Europe Hybrid Incidents (NonCommercial)",
}

INTRO = {
    "open": (
        "Drone incidents, sabotage and other hybrid operations in Europe since "
        "2022, compiled from open datasets into one event table."
    ),
    "nc": (
        "Drone incidents, sabotage and other hybrid operations in Europe since "
        "2022, compiled from open and NonCommercial datasets into one event "
        "table. It holds every row of the [open dataset]"
        "(https://huggingface.co/datasets/sam-bot/europe-hybrid-incidents) "
        "and the rows of the sources whose licence forbids commercial use."
    ),
}


def versions_path(family: str) -> Path:
    """Path of the file that holds the "Versions" section of one family."""
    return CARDS_DIR / f"versions-{family}.md"


def credits(family: str) -> list[str]:
    """Credit lines of the sources and reference datasets of one family.

    An `open` source or reference is credited in both cards, because the `nc`
    dataset holds its rows too. An `nc` one is credited in the `nc` card only.
    """
    names = family_sources(family)
    lines = [
        f"- {SOURCES[name].citation} {SOURCES[name].licence}. <{SOURCES[name].homepage}>"
        for name in names
    ]
    for reference in REFERENCES.values():
        if family == "nc" or reference.family == family:
            lines.append(
                f"- {reference.citation} {reference.licence}. <{reference.homepage}> "
                f"([licence]({reference.licence_url}))"
            )
    return lines


def configs(files: set[str]) -> str:
    """The `configs` block of the card, for the Parquet files that are present.

    `events.csv` is not listed: the viewer would show the events twice.
    """
    entries = [("events", "events.parquet"), ("sources", "sources.parquet")]
    if "quality.parquet" in files:
        entries.append(("quality", "quality.parquet"))
    if "threat_actors.parquet" in files:
        entries.append(("threat_actors", "threat_actors.parquet"))
    lines = ["configs:"]
    for position, (name, path) in enumerate(entries):
        lines.append(f"  - config_name: {name}")
        if position == 0:
            lines.append("    default: true")
        lines.extend(
            ["    data_files:", "      - split: train", f"        path: {path}"]
        )
    return "\n".join(lines)


def curated_countries(lines: list[actors.Curation]) -> list[str]:
    """The "Curated countries" section: one table row per curated country.

    Returns:
        Lines of Markdown, or an empty list when no line has a country.
    """
    rows = [line for line in lines if line.country]
    if not rows:
        return []
    table = [
        "| Group | Country | Reserve (words of the source) | Sources |",
        "| --- | --- | --- | --- |",
    ]
    for line in sorted(rows, key=lambda row: row.actor.casefold()):
        table.append(
            f"| {line.actor} | {line.country} | "
            f'"{line.country_quote}" | '
            f"[{line.country_publisher}]({line.country_url}), "
            f"[{line.stance_publisher}]({line.stance_url}) |"
        )
    return [
        "## Curated countries",
        "",
        (
            "These countries come from the curation of the project "
            "(`curation/actors.csv`), not from the MISP galaxy. The quoted "
            "words give the strength of each source: the first link is the page "
            "that links the group to the country, the second is the page that "
            "calls the group pro-Russian. Columns `country_url`, "
            "`country_publisher`, `country_quote`, `stance_url`, "
            "`stance_publisher` and `stance_quote` hold the same fields."
        ),
        "",
        *table,
        "",
    ]


def render_card(
    family: str,
    files: set[str],
    versions: str | None = None,
    curation: list[actors.Curation] | None = None,
) -> str:
    """The dataset card of one family.

    Args:
        family: One of `FAMILIES`.
        files: Names of the files that the commit holds. A config is declared
            only when its file is in the set.
        versions: The "Versions" section. Default: the file of the family in
            `cards/`.
        curation: The lines of the curated countries. Default: `curation/actors.csv`.
            Used only for the open card with `threat_actors.parquet` in `files`.

    Returns:
        The card, front matter first.

    Raises:
        UserError: The "Versions" section does not start with the heading
            `## Versions`.
    """
    if versions is None:
        versions = versions_path(family).read_text(encoding="utf-8")
    if not versions.lstrip().startswith(VERSIONS_HEADING):
        raise UserError(f"The Versions section of {family} must start with a heading")
    if family != "open":
        files = files - {"threat_actors.parquet"}
    if curation is None and "threat_actors.parquet" in files:
        curation = actors.read_curation(CURATION_DIR / "actors.csv")
    curated = (
        curated_countries(curation or []) if "threat_actors.parquet" in files else []
    )
    licence, licence_url = LICENCE_LINKS[family]
    front_matter = "\n".join(
        [
            f"license: {LICENCES[family]}",
            f"pretty_name: {TITLES[family]}",
            "language:",
            "  - en",
            configs(files),
        ]
    )
    body = [
        f"# {TITLES[family]}",
        "",
        INTRO[family],
        "",
        (
            "Tables: `events` (one row per event, all sources), `sources` (one row "
            "per source), `quality` (one row per source and per run, when "
            "present) and, in the open dataset, `threat_actors` (one row per "
            "threat group, with its aliases and country). The data is updated "
            "by the pipeline of the project."
        ),
        "",
        "- Code and pipeline: [github.com/tryph0n/hybrid-tracker](https://github.com/tryph0n/hybrid-tracker)",
        "- Live dashboard: [hybrid-tracker.onrender.com](https://hybrid-tracker.onrender.com)",
        "- Columns and values: [data dictionary](https://hybrid-tracker.readthedocs.io/en/latest/data-dictionary.html)",
        "",
        "## Sources and licence",
        "",
        (
            "Modified: unified schema. The events of each source are mapped to one "
            f"common set of columns. The data is distributed under [{licence}]"
            f"({licence_url}). Reuse must credit the original sources and the "
            "reference data:"
        ),
        "",
        *credits(family),
        "",
        (
            "Approximate coordinates: in the published tables, an event with a "
            "place and no coordinates gets those of the town of that name in its "
            "country, with `geo_precision` `place`, matched by name and country."
        ),
        "",
        *curated,
        versions.strip(),
        "",
    ]
    return f"---\n{front_matter}\n---\n\n" + "\n".join(body)


def digest(content: bytes) -> str:
    """SHA-256 of a file, in hexadecimal."""
    return hashlib.sha256(content).hexdigest()


def read_back(
    api: Any, repo_id: str, oid: str, sent: dict[str, bytes], legacy: list[str]
) -> None:
    """Download the commit `oid` and compare each sent file with it.

    Raises:
        UserError: A file is missing at the commit, differs from the local
            file, or a deleted legacy file is still there.
    """
    present = set(api.list_repo_files(repo_id, repo_type="dataset", revision=oid))
    problems = [f"{name} is still in {repo_id}" for name in legacy if name in present]
    with tempfile.TemporaryDirectory() as cache:
        for name, content in sent.items():
            if name not in present:
                problems.append(f"{name} is missing on {repo_id}")
                continue
            local = api.hf_hub_download(
                repo_id,
                name,
                revision=oid,
                repo_type="dataset",
                cache_dir=cache,
            )
            if digest(Path(local).read_bytes()) != digest(content):
                problems.append(f"{name} differs on {repo_id}")
    if problems:
        raise UserError("Read-back failed: " + "; ".join(problems))


def tag_commit(api: Any, repo_id: str, tag: str, oid: str) -> str:
    """Create the tag on the commit when it does not exist. Never move or delete one.

    Returns:
        One line for the run summary.
    """
    from huggingface_hub.errors import HfHubHTTPError

    try:
        existing = {
            ref.name for ref in api.list_repo_refs(repo_id, repo_type="dataset").tags
        }
    except HfHubHTTPError as exc:
        line = f"Could not list the tags of {repo_id}: {exc}. Create {tag} by hand at {oid}."
        logger.warning(line)
        return line
    if tag in existing:
        line = f"Tag {tag} already exists on {repo_id}, left as it is."
        logger.info(line)
        return line
    try:
        api.create_tag(
            repo_id, tag=tag, revision=oid, repo_type="dataset", exist_ok=False
        )
    except HfHubHTTPError as exc:
        line = (
            f"Could not create the tag {tag} on {repo_id} ({exc}). "
            f"Create it by hand on commit {oid}."
        )
        logger.warning(line)
        return line
    line = f"Tag {tag} created on {repo_id} at {oid}."
    logger.info(line)
    return line


def publish_family(api: Any, family: str, folder: Path) -> list[str]:
    """Commit one family to its dataset, read it back and tag the version.

    Args:
        api: An `HfApi`, or a fake in tests.
        family: One of `FAMILIES`.
        folder: The folder `<build>/<family>` of `tracker build`.

    Returns:
        The lines for the run summary.

    Raises:
        UserError: A required file is missing, or the read-back fails.
    """
    from huggingface_hub import CommitOperationAdd, CommitOperationDelete

    repo_id = DATASETS[family]
    if not folder.is_dir():
        raise UserError(f"{folder / 'events.parquet'} not found, run `tracker build`")
    sent = {
        path.name: path.read_bytes()
        for path in sorted(folder.iterdir())
        if path.is_file()
    }
    for required in ("events.parquet", "sources.parquet"):
        if required not in sent:
            raise UserError(f"{folder / required} not found, run `tracker build`")
    sent[CARD_FILE] = render_card(family, set(sent)).encode("utf-8")

    on_hub = set(api.list_repo_files(repo_id, repo_type="dataset"))
    legacy = [name for name in LEGACY_FILES[family] if name in on_hub]
    operations: list[CommitOperationAdd | CommitOperationDelete] = [
        CommitOperationAdd(path_in_repo=name, path_or_fileobj=content)
        for name, content in sent.items()
    ]
    operations += [CommitOperationDelete(path_in_repo=name) for name in legacy]
    info = api.create_commit(
        repo_id=repo_id,
        repo_type="dataset",
        operations=operations,
        commit_message=provenance.publish_message(SCHEMA_VERSION),
    )
    read_back(api, repo_id, info.oid, sent, legacy)
    logger.info("%s: read-back passed at %s", repo_id, info.oid)
    return [
        f"{repo_id}: {len(sent)} files committed at {info.oid}, read-back passed.",
        tag_commit(api, repo_id, f"v{SCHEMA_VERSION}", info.oid),
    ]


def run(family: str, build: Path, api: Any = None) -> None:
    """Publish one family and write the result in the run summary.

    Args:
        family: One of `FAMILIES`.
        build: The folder of `tracker build`.
        api: An `HfApi`. Default: a new one, which reads `HF_OIDC_RESOURCE`.

    Raises:
        UserError: See `publish_family`.
    """
    if family not in FAMILIES:
        raise UserError(f"Unknown family {family!r}")
    if api is None:
        from huggingface_hub import HfApi

        api = HfApi()
    lines = publish_family(api, family, build / family)
    write_summary("\n".join(["### Hugging Face publish", "", *lines, ""]) + "\n")
