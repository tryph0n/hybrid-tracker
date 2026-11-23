"""The image tag hashes the files the Dockerfile copies."""

import re

from conftest import ROOT


def copied_by_dockerfile() -> set[str]:
    """Sources of the COPY lines, plus the Dockerfile itself."""
    lines = (ROOT / "Dockerfile").read_text(encoding="utf-8").splitlines()
    paths = {"Dockerfile"}
    for line in lines:
        if line.startswith("COPY "):
            paths.update(path.rstrip("/") for path in line.split()[1:-1])
    return paths


def hashed_by_workflow() -> set[str]:
    text = (ROOT / ".github" / "workflows" / "pipeline.yml").read_text(encoding="utf-8")
    match = re.search(r"git ls-tree HEAD ([^|]+)\|", text)
    assert match, "no git ls-tree line in pipeline.yml"
    return set(match.group(1).split())


def test_image_tag_hashes_the_copied_files():
    assert hashed_by_workflow() == copied_by_dockerfile()
