"""The image tag hashes the files the Dockerfile reads."""

import re

from conftest import ROOT


def build_lines() -> list[str]:
    """Dockerfile instructions, with continuation lines joined."""
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    return text.replace("\\\n", " ").splitlines()


def read_by_dockerfile() -> set[str]:
    """Sources of the COPY lines and of the bind mounts, plus the Dockerfile.

    A COPY from another stage or image reads no file of the repository, and
    flags such as --chown are not paths.
    """
    paths = {"Dockerfile"}
    for line in build_lines():
        if line.startswith("COPY "):
            words = line.split()[1:]
            if any(word.startswith("--from=") for word in words):
                continue
            files = [word for word in words if not word.startswith("--")]
            paths.update(path.rstrip("/") for path in files[:-1])
        elif line.startswith("RUN "):
            paths.update(re.findall(r"type=bind,source=([^,\s]+)", line))
    return paths


def hashed_by_workflow() -> set[str]:
    text = (ROOT / ".github" / "workflows" / "pipeline.yml").read_text(encoding="utf-8")
    match = re.search(r"git ls-tree HEAD ([^|]+)\|", text)
    assert match, "no git ls-tree line in pipeline.yml"
    return set(match.group(1).split())


def test_image_tag_hashes_the_files_the_build_reads():
    assert hashed_by_workflow() == read_by_dockerfile()


def test_image_tag_and_build_carry_the_snapshot_revision():
    text = (ROOT / ".github" / "workflows" / "pipeline.yml").read_text(encoding="utf-8")
    assert '"$files $snapshot_revision" | sha256sum' in text
    args = re.findall(
        r"SNAPSHOT_REVISION=\$\{\{ steps.image.outputs.snapshot_revision", text
    )
    assert len(args) == 2, "the scan build and the push build must both pass it"
    dockerfile = "\n".join(build_lines())
    assert "ARG SNAPSHOT_REVISION" in dockerfile
    assert "resolve/${SNAPSHOT_REVISION}/events.parquet" in dockerfile
