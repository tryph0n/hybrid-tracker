"""The `tracker` command: one subcommand per action."""

import argparse
import logging
from collections.abc import Sequence

from tracker import ingest
from tracker.sources import SOURCES


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
