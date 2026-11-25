"""The `tracker` command: one subcommand per action."""

import argparse
import logging
from collections.abc import Sequence
from pathlib import Path

from tracker import build, ingest
from tracker.sources import SOURCES
from tracker.store import open_store


def build_parser() -> argparse.ArgumentParser:
    """Build the parser of the `tracker` command."""
    parser = argparse.ArgumentParser(
        prog="tracker", description="Build the data of the hybrid tracker."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    ingest_parser = commands.add_parser(
        "ingest",
        help="download sources, keep their payloads and write clean tables",
    )
    ingest_parser.add_argument(
        "target",
        choices=[*SOURCES, *ingest.TARGETS],
        help="a source, every `live` or `static` source, or `check` to validate "
        "the stored files without a download",
    )
    rebuild_parser = commands.add_parser(
        "rebuild",
        help="write the clean tables again from the stored payloads, with no download",
    )
    rebuild_parser.add_argument(
        "source",
        nargs="?",
        choices=list(SOURCES),
        help="a source; every source when omitted",
    )
    rebuild_parser.add_argument(
        "--allow-shrink",
        action="store_true",
        help="write the table even if it loses more than 5%% of the stored events",
    )
    build_cmd = commands.add_parser(
        "build",
        help="write the published tables of each licence family from the clean tables",
    )
    build_cmd.add_argument(
        "--out",
        type=Path,
        default=Path("build"),
        help="folder that gets one subfolder per family (default: build)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    """Run the command.

    Args:
        argv: The arguments after the command name. Default: `sys.argv`.

    Raises:
        SystemExit: The arguments are invalid, or `--help` was given.
    """
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = build_parser().parse_args(argv)
    if args.command == "ingest":
        ingest.run(args.target)
    elif args.command == "rebuild":
        ingest.rebuild(args.source, open_store(), args.allow_shrink)
    elif args.command == "build":
        build.build(open_store(), args.out)
