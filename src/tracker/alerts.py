"""One alert issue per source, kept open while the source has a cause.

`tracker quality` writes `alerts.json`: each source with a cause maps to the list
of its causes. `plan` compares it with the open alert issues, and `sync` runs the
plan with the `gh` command. A source gets one open issue, titled with its name. A
run in which a cause remains comments on that issue. The issue closes on the
first run in which the source has no cause at all.
"""

import json
import logging
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from tracker.errors import UserError

logger = logging.getLogger(__name__)

LABEL = "source-alert"
TITLE_PREFIX = "Source alert: "

Gh = Callable[[Sequence[str]], str]


@dataclass(frozen=True)
class Action:
    """One step on the alert issues: `open` a new one, `comment` on an open one, or
    `close` one, for a source, with its causes in this run (empty for `close`) and
    the number of the open issue (None for `open`).
    """

    kind: Literal["open", "comment", "close"]
    source: str
    causes: tuple[str, ...]
    number: int | None = None


def title(source: str) -> str:
    """The title of the alert issue of a source."""
    return f"{TITLE_PREFIX}{source}"


def plan(
    alerts: Mapping[str, Sequence[str]], open_issues: Sequence[Mapping[str, Any]]
) -> list[Action]:
    """Decide what to do with the alert issues.

    Args:
        alerts: Each source with at least one cause, mapped to its causes.
        open_issues: The open issues with the alert label, each with `number` and
            `title`.

    Returns:
        The actions: open an issue for a source with a cause and no open issue,
        comment on the open issue of a source that still has a cause, and close
        the open issue of a source with no cause. An open issue whose title is
        not an alert title is left alone.
    """
    numbers: dict[str, int] = {}
    for issue in open_issues:
        if issue["title"].startswith(TITLE_PREFIX):
            source = issue["title"].removeprefix(TITLE_PREFIX)
            numbers.setdefault(source, issue["number"])
    actions: list[Action] = []
    for source, found in sorted(alerts.items()):
        if found:
            number = numbers.get(source)
            if number is None:
                actions.append(Action("open", source, tuple(found)))
            else:
                actions.append(Action("comment", source, tuple(found), number))
    actions += [
        Action("close", source, (), number)
        for source, number in sorted(numbers.items())
        if not alerts.get(source)
    ]
    return actions


def body(action: Action, run_url: str) -> str:
    """The text of the issue, of the comment or of the closing comment."""
    if action.kind == "close":
        return f"No cause remains for this source in the run {run_url}."
    causes = "\n".join(f"- {cause}" for cause in action.causes)
    if action.kind == "comment":
        return f"The run {run_url} still finds a cause for this source:\n\n{causes}"
    return (
        f"The source `{action.source}` has a cause in the run {run_url}:\n\n{causes}"
        "\n\nSee the runbook in the maintainer guide. This issue closes on the "
        "first run in which the source has no cause."
    )


def sync(alerts_path: Path, run_url: str, gh: Gh) -> list[Action]:
    """Open, comment on or close the alert issues for one run.

    Args:
        alerts_path: The `alerts.json` of `tracker quality`.
        run_url: Link of the run, written in each issue and comment.
        gh: Runs `gh` with the given arguments and returns its output.

    Returns:
        The actions that were done.

    Raises:
        UserError: The alerts file does not exist.
    """
    try:
        text = alerts_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise UserError(
            f"no alerts file at {alerts_path}, run `tracker quality`"
        ) from None
    alerts = json.loads(text)
    gh(
        ["label", "create", LABEL, "--force", "--color", "D93F0B"]
        + ["--description", "A source has a cause that needs a look"]
    )
    listed = gh(
        ["issue", "list", "--label", LABEL, "--state", "open", "--limit", "200"]
        + ["--json", "number,title"]
    )
    actions = plan(alerts, json.loads(listed))
    for action in actions:
        text = body(action, run_url)
        if action.kind == "open":
            gh(
                ["issue", "create", "--label", LABEL, "--title", title(action.source)]
                + ["--body", text]
            )
        elif action.kind == "comment":
            gh(["issue", "comment", str(action.number), "--body", text])
        else:
            gh(["issue", "close", str(action.number), "--comment", text])
        logger.info("%s the alert issue of %s", action.kind, action.source)
    return actions


def run_gh(arguments: Sequence[str]) -> str:
    """Run `gh` and return its output, raise when it fails."""
    done = subprocess.run(
        ["gh", *arguments], check=True, capture_output=True, text=True
    )
    return done.stdout
