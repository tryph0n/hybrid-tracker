"""Code commit and workflow run of a pipeline step, read from the GitHub variables.

The default variables of GitHub Actions name the commit and the run. Outside a
run (a local rebuild, the tests) they are absent and nothing is recorded.
https://docs.github.com/en/actions/reference/workflows-and-actions/variables
"""

import os
from collections.abc import Mapping


def code_commit(env: Mapping[str, str] | None = None) -> str | None:
    """The commit that the workflow runs, or None outside a run."""
    env = os.environ if env is None else env
    return env.get("GITHUB_SHA") or None


def run_url(env: Mapping[str, str] | None = None) -> str | None:
    """The link to the workflow run, or None outside a run."""
    env = os.environ if env is None else env
    server = env.get("GITHUB_SERVER_URL")
    repository = env.get("GITHUB_REPOSITORY")
    run_id = env.get("GITHUB_RUN_ID")
    if not (server and repository and run_id):
        return None
    return f"{server}/{repository}/actions/runs/{run_id}"


def publish_message(schema_version: str, env: Mapping[str, str] | None = None) -> str:
    """The commit message of a dataset publish: the schema, then the code and the run.

    Args:
        schema_version: The schema version that the publish carries.
        env: The environment. Default: the process environment.

    Returns:
        A title line, then a line for the code commit and a line for the run link,
        each only when the environment gives it.
    """
    lines = [f"data: publish schema {schema_version}"]
    commit = code_commit(env)
    url = run_url(env)
    if commit or url:
        lines.append("")
    if commit:
        lines.append(f"Code: {commit}")
    if url:
        lines.append(f"Run: {url}")
    return "\n".join(lines)
