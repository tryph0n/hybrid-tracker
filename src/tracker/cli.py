"""The `tracker` command: one subcommand per action."""

import argparse
import logging
from collections.abc import Sequence
from pathlib import Path

from tracker import build, ingest, publish, quality
from tracker.errors import UserError
from tracker.sources import FAMILIES, REFERENCES, SOURCES
from tracker.store import StoreError, open_store


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
        choices=[*SOURCES, *REFERENCES, *ingest.TARGETS],
        help="a source or reference dataset, every `live` source, every `static` "
        "source with the reference datasets, or `check` to validate the stored "
        "files without a download",
    )
    ingest_parser.add_argument(
        "--if-missing",
        action="store_true",
        help="skip a source that already has a clean table in the raw store",
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
    quality_cmd = commands.add_parser(
        "quality",
        help="write the quality table and the run summary, and fail on a stale source",
    )
    quality_cmd.add_argument(
        "--out",
        type=Path,
        default=Path("build"),
        help="folder of `tracker build` that gets quality.parquet (default: build)",
    )
    publish_cmd = commands.add_parser(
        "publish",
        help="publish one licence family to its public dataset, read it back and tag the version",
    )
    publish_cmd.add_argument(
        "family", choices=FAMILIES, help="the licence family to publish"
    )
    publish_cmd.add_argument(
        "--build",
        type=Path,
        default=Path("build"),
        help="folder of `tracker build` (default: build)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    """Run the command.

    Args:
        argv: The arguments after the command name. Default: `sys.argv`.

    Raises:
        SystemExit: The arguments are invalid, `--help` was given, or a command
            stopped on a known problem (`UserError` or `StoreError`): the code
            is 1 and the message goes to standard error with no traceback.
    """
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = build_parser().parse_args(argv)
    try:
        if args.command == "ingest":
            failures = ingest.run(args.target, args.if_missing)
            if failures:
                quality.write_summary(ingest.failure_summary(failures))
        elif args.command == "rebuild":
            ingest.rebuild(args.source, open_store(), args.allow_shrink)
        elif args.command == "build":
            left_out = build.build(open_store(), args.out)
            if left_out:
                quality.write_summary(build.left_out_summary(left_out))
        elif args.command == "quality":
            quality.run(open_store(), args.out)
        elif args.command == "publish":
            publish.run(args.family, args.build)
    except (UserError, StoreError) as error:
        raise SystemExit(str(error)) from None
