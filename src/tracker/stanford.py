"""Stanford GNSS interference (rfi.stanford.edu): daily heatmaps and context table.

Licence: CC BY-NC 4.0. One file per day, `jamming/YYYY/MM/DD/heatmap.json`,
for the whole world. The server sends only its own certificate, not the
intermediate "InCommon RSA Server CA 2", so the fetch trusts the copy that this
package holds (`CA_FILE`) and always checks TLS. The server compresses only
with brotli, so the fetch asks for it and keeps the body as sent.

The raw store keeps the brotli bytes, but its manifest holds the SHA-256 of the
decoded file. So `ingest.lineage` gets the decoded files (`decoded_files`), not
the stored bytes, and the `sha256` of the lineage is the hash of the decoded
file, not of the file at `path`.
"""

import functools
import http.client
import io
import json
import logging
import ssl
import time
from collections.abc import Callable, Collection, Mapping, Sequence
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import brotli
import h3
import pandas as pd

from tracker.errors import UserError
from tracker.store import Payload, Store

logger = logging.getLogger(__name__)

SOURCE = "stanford"
HOST = "waas-nas.stanford.edu"
HEATMAP_PATH = "/data/jamming/{day:%Y/%m/%d}/heatmap.json"
CA_FILE = Path(__file__).parent / "certs" / "incommon-rsa-server-ca-2.pem"
HEADERS = {
    "User-Agent": "hybrid-tracker (open-data dashboard, daily refresh)",
    "Accept-Encoding": "br",
}
TIMEOUT = 60
RECHECK_DAYS = 7
FIRST_RUN_LOOKBACK = 14
MAX_PAYLOADS_PER_SAVE = 50
LAT_RANGE = (34.0, 72.0)
LON_RANGE = (-25.0, 45.0)
COLUMNS = [
    "day",
    "h3_index",
    "low_quality_count",
    "total_aircraft_count",
    "low_quality_percent",
]


def tls_context(extra_ca: Path = CA_FILE) -> ssl.SSLContext:
    """TLS context that checks the server, with `extra_ca` added to the system roots.

    The addition is for this context only, not for the system.

    Args:
        extra_ca: A PEM file with the intermediate certificate to trust.

    Returns:
        A context that verifies the certificate and the host name.
    """
    context = ssl.create_default_context()
    context.load_verify_locations(cafile=str(extra_ca))
    return context


def payload_name(day: date) -> str:
    """File name of the payload of one day.

    One name per day, so that the store compares a day fetched again with the
    same day.
    """
    return f"heatmap-{day:%Y-%m-%d}.json.br"


def make_payload(
    day: date, url: str, status: int, body: bytes, encoding: str
) -> Payload:
    """Build the payload of one day from the answer of the server.

    The body is kept in brotli as the server sent it. The store compares the
    decoded file, so the same file compressed another way is not a change.

    Args:
        day: The day of the file.
        url: The address that was fetched.
        status: The HTTP status.
        body: The body as received.
        encoding: The `Content-Encoding` header: "br", or empty for a file sent
            uncompressed, which is compressed here without loss.

    Returns:
        The payload, with the decoded file in `decoded`.

    Raises:
        OSError: The encoding is neither brotli nor empty.
    """
    if encoding == "br":
        decoded = brotli.decompress(body)
    elif encoding in ("", "identity"):
        decoded = body
        body = brotli.compress(decoded)
    else:
        raise OSError(f"{url}: unexpected Content-Encoding {encoding!r}")
    return Payload(SOURCE, payload_name(day), body, url, status, decoded=decoded)


def fetch_days(
    days: Sequence[date],
    context: ssl.SSLContext | None = None,
    host: str = HOST,
    port: int = 443,
    skip_missing: bool = False,
) -> list[Payload]:
    """Fetch the daily heatmaps of `days` over one TLS connection.

    Args:
        days: The days to fetch.
        context: The TLS context. Default: `tls_context()`.
        host: The server.
        port: The server port.
        skip_missing: Leave out a day that the server answers with 404 (not
            published yet) instead of raising.

    Returns:
        One payload per day, in the order of `days` (without the missing days
        when `skip_missing` is set).

    Raises:
        OSError: A TLS check failed (`ssl.SSLCertVerificationError`), a request
            failed, or the status of a day is not 200 (a day not published yet
            gives 404, unless `skip_missing` is set).
    """
    started = time.monotonic()
    connection = http.client.HTTPSConnection(
        host, port, timeout=TIMEOUT, context=context or tls_context()
    )
    payloads = []
    try:
        for day in days:
            path = HEATMAP_PATH.format(day=day)
            url = f"https://{host}{path}"
            connection.request("GET", path, headers=HEADERS)
            response = connection.getresponse()
            body = response.read()
            if response.status == 404 and skip_missing:
                logger.info("stanford: %s not published yet", day)
                continue
            if response.status != 200:
                raise OSError(f"{url}: HTTP {response.status}")
            payloads.append(
                make_payload(
                    day,
                    url,
                    response.status,
                    body,
                    response.getheader("Content-Encoding", ""),
                )
            )
    finally:
        connection.close()
    logger.info(
        "stanford: %d of %d day(s) fetched in %.1f s",
        len(payloads),
        len(days),
        time.monotonic() - started,
    )
    return payloads


Fetcher = Callable[[Sequence[date]], list[Payload]]


def day_of(name: str) -> date:
    """The day of a payload name made by `payload_name`."""
    return date.fromisoformat(name.removeprefix("heatmap-").removesuffix(".json.br"))


def stored_days(store: Store) -> list[date]:
    """The days that the raw store holds, oldest first."""
    return sorted({day_of(line["name"]) for line in store.payloads(SOURCE)})


def days_to_fetch(stored: Sequence[date], today: date) -> list[date]:
    """The days that a run requests, oldest first.

    Args:
        stored: The days in the raw store (not empty).
        today: The day of the run.

    Returns:
        Each day from the first stored day to `today` that is not stored, and
        the last `RECHECK_DAYS` stored days again.
    """
    held = set(stored)
    first = min(held)
    missing = {
        first + timedelta(days=n)
        for n in range((today - first).days + 1)
        if first + timedelta(days=n) not in held
    }
    return sorted(missing | set(sorted(held)[-RECHECK_DAYS:]))


def newest_day(today: date, fetch: Fetcher) -> list[Payload]:
    """Fetch the newest day that the source has, trying from `today` back.

    Raises:
        OSError: No day in the last `FIRST_RUN_LOOKBACK` days is published.
    """
    for back in range(FIRST_RUN_LOOKBACK):
        found = fetch([today - timedelta(days=back)])
        if found:
            return found
    raise OSError(
        f"stanford: no heatmap published in the last {FIRST_RUN_LOOKBACK} days"
    )


def catch_up(
    store: Store, today: date, fetch: Fetcher | None = None
) -> list[dict[str, Any]]:
    """Fetch the missing days and the last stored days again, and save them.

    With an empty store, only the newest published day is fetched (the days
    before it are the history, a separate task). A day that is not published
    yet (404) is left for a later run. The store adds a payload of a day only
    when the decoded file changed.

    Args:
        store: The raw store.
        today: The day of the run.
        fetch: Fetches days and leaves out the missing ones. Default:
            `fetch_days` with `skip_missing`.

    Returns:
        The manifest line of each payload fetched.
    """
    fetch = fetch or functools.partial(fetch_days, skip_missing=True)
    stored = stored_days(store)
    payloads = (
        fetch(days_to_fetch(stored, today)) if stored else newest_day(today, fetch)
    )
    lines: list[dict[str, Any]] = []
    for start in range(0, len(payloads), MAX_PAYLOADS_PER_SAVE):
        lines.extend(store.save_many(payloads[start : start + MAX_PAYLOADS_PER_SAVE]))
    logger.info(
        "stanford: %d payload(s) fetched, %d new",
        len(lines),
        sum(line["stored"] for line in lines),
    )
    return lines


def month_of(day: date) -> str:
    """The calendar month of a day, as `YYYY-MM`."""
    return f"{day:%Y-%m}"


def month_path(month: str) -> str:
    """Path of the month file of the context table in the clean store."""
    return f"clean/{SOURCE}/{month}.parquet"


def fetched_months(lines: Sequence[Mapping[str, Any]]) -> list[str]:
    """The months of the days that a run fetched (the manifest lines of `catch_up`).

    A day whose file did not change is included: an earlier run may have saved
    the raw day and failed before it wrote the month file. `write_months`
    leaves a month whose bytes are the same.
    """
    return sorted({month_of(day_of(line["name"])) for line in lines})


def decoded_files(
    store: Store, months: Collection[str] | None = None
) -> dict[str, bytes]:
    """The newest payload of each stored day, decoded: payload name to file.

    These are the files for `context_table` and for `ingest.lineage`.

    Args:
        store: The raw store.
        months: Read only the days of these months (`YYYY-MM`), so that a run
            does not read each stored day. Default: every stored day.
    """
    files = {}
    for day in stored_days(store):
        if months is not None and month_of(day) not in months:
            continue
        name = payload_name(day)
        content = store.latest(SOURCE, name)
        if content is None:
            raise UserError(f"stanford: {name} is in no manifest of the store")
        files[name] = brotli.decompress(content)
    return files


def reduce_day(day: date, decoded: bytes) -> pd.DataFrame:
    """The rows of one daily file that lie in the area, with the percentage.

    Args:
        day: The day of the file.
        decoded: The file as the source gives it: an object with the licence
            fields and the rows in its `data` list.

    Returns:
        The columns `COLUMNS`: the cells whose centre is in `LAT_RANGE` and
        `LON_RANGE` (bounds included), with the two counts of the source and
        the percentage of aircraft with low quality.

    Raises:
        UserError: The file is not an object with a `data` list, or a row has
            no aircraft, or more low quality aircraft than aircraft.
    """
    document = json.load(io.BytesIO(decoded))
    if not isinstance(document, dict) or not isinstance(document.get("data"), list):
        raise UserError(f"stanford: {day} file is not an object with a data list")
    frame = pd.DataFrame(
        document["data"],
        columns=["h3Index", "lowQualityCount", "totalAircraftCount"],
    )
    for reason, bad in (
        ("totalAircraftCount is 0", frame["totalAircraftCount"] == 0),
        (
            "lowQualityCount is above totalAircraftCount",
            frame["lowQualityCount"] > frame["totalAircraftCount"],
        ),
    ):
        if bad.any():
            cell = frame["h3Index"][bad].iloc[0]
            raise UserError(f"stanford: {day} cell {cell}: {reason}")
    centres = [h3.cell_to_latlng(cell) for cell in frame["h3Index"]]
    inside = [
        LAT_RANGE[0] <= lat <= LAT_RANGE[1] and LON_RANGE[0] <= lon <= LON_RANGE[1]
        for lat, lon in centres
    ]
    kept = frame[inside]
    return pd.DataFrame(
        {
            "day": day,
            "h3_index": kept["h3Index"],
            "low_quality_count": kept["lowQualityCount"],
            "total_aircraft_count": kept["totalAircraftCount"],
            "low_quality_percent": kept["lowQualityCount"]
            / kept["totalAircraftCount"]
            * 100,
        },
        columns=COLUMNS,
    ).reset_index(drop=True)


def context_table(files: Mapping[str, bytes]) -> pd.DataFrame:
    """The context table: one row per cell in the area and per day.

    Args:
        files: Payload name to decoded file, as `decoded_files` returns.

    Returns:
        The columns `COLUMNS`, oldest day first.
    """
    frames = [reduce_day(day_of(name), files[name]) for name in sorted(files)]
    if not frames:
        return pd.DataFrame(columns=COLUMNS)
    return pd.concat(frames, ignore_index=True)


def write_months(store: Store, months: Collection[str]) -> list[str]:
    """Write the month files of the context table in the clean store.

    Each month is built from the stored days of that month only. A month file
    whose bytes do not change is not written. The files go in one commit.

    Args:
        store: The raw store, which holds the clean store.
        months: The months to build, as `fetched_months` returns them.

    Returns:
        The paths written, sorted.
    """
    written: dict[str, bytes] = {}
    for month in sorted(months):
        table = context_table(decoded_files(store, {month}))
        content = io.BytesIO()
        table.to_parquet(content, index=False)
        path = month_path(month)
        if store.backend.read(path) != content.getvalue():
            written[path] = content.getvalue()
    if written:
        store.backend.commit(
            written, f"clean: {SOURCE} table, {len(written)} month file(s)"
        )
    logger.info("stanford: %d month file(s) written", len(written))
    return sorted(written)


def table_days(store: Store) -> pd.DataFrame | None:
    """The `day` column of the month files in the clean store, None when none exists.

    Reads only that column, for the quality check.
    """
    months = sorted({month_of(day) for day in stored_days(store)})
    columns = []
    for month in months:
        content = store.backend.read(month_path(month))
        if content is not None:
            columns.append(pd.read_parquet(io.BytesIO(content), columns=["day"]))
    return pd.concat(columns, ignore_index=True) if columns else None


def new_day_stored_at(store: Store) -> pd.Timestamp | None:
    """Time of the latest run that stored a day for the first time, None if none did.

    A run that stores a new file of a day already held, or that fetches days
    again with no change, does not count.
    """
    first: dict[str, pd.Timestamp] = {}
    for line in store.payloads(SOURCE):
        fetched = pd.Timestamp(line["fetched_at"])
        first[line["name"]] = min(fetched, first.get(line["name"], fetched))
    return max(first.values()) if first else None
