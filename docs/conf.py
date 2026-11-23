"""Sphinx configuration, https://www.sphinx-doc.org/en/master/usage/configuration.html"""

import ast
import io
import os
import re
import tokenize
from pathlib import Path

SCHEMA = Path(__file__).resolve().parents[1] / "src" / "tracker" / "schema.py"

project = "Europe Hybrid Incidents Tracker"
author = "Samy Antoine SAAD"
copyright = f"2026, {author}"

extensions = ["autoapi.extension", "myst_parser", "sphinxcontrib.mermaid"]
myst_enable_extensions = ["substitution"]
myst_fence_as_directive = ["mermaid"]
# The sphinxcontrib-mermaid default (11.12.1) cuts long subgraph titles.
mermaid_version = "11.14.0"
exclude_patterns = ["_build"]
# Docstrings use single backticks for code, as Markdown does.
default_role = "literal"
# Keep the straight quotes of docstrings and comments, as in the code.
smartquotes = False

# AutoAPI parses the source without importing it: the docs need none of the
# app dependencies. Only the tracker package: the Streamlit page is a script.
autoapi_dirs = ["../src/tracker"]
autoapi_options = ["members", "undoc-members", "show-module-summary"]

html_theme = "furo"
html_title = project
# Set by Read the Docs; empty in local and CI builds.
html_baseurl = os.environ.get("READTHEDOCS_CANONICAL_URL", "")


def columns_table() -> str:
    """Markdown table of the event columns, read from schema.py without importing it."""
    source = SCHEMA.read_text(encoding="utf-8")
    nodes = {
        node.targets[0].id: node.value
        for node in ast.parse(source).body
        if isinstance(node, ast.Assign)
    }
    required = [element.value for element in nodes["REQUIRED"].elts]
    allowed = {
        key.value: [element.value for element in nodes[value.id].elts]
        for key, value in zip(
            nodes["ALLOWED_VALUES"].keys, nodes["ALLOWED_VALUES"].values
        )
    }
    # Each column is described by the comment on its line.
    comments = {
        token.start[0]: token.string.lstrip("# ")
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type == tokenize.COMMENT
    }

    rows = ["| Column | Required | Allowed values | Description |", "|---|---|---|---|"]
    for element in nodes["COLUMNS"].elts:
        name = element.value
        values = ", ".join(f"`{value}`" for value in allowed.get(name, []))
        # Comments are plain text: escape what Markdown would interpret.
        description = re.sub(
            r"([\\`*_<>|\[\]])", r"\\\1", comments.get(element.lineno, "")
        )
        rows.append(
            f"| `{name}` | {'yes' if name in required else 'no'} | {values} | {description} |"
        )
    return "\n".join(rows)


# Used by data-dictionary.md.
myst_substitutions = {"columns_table": columns_table()}
