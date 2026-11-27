"""Stanford GNSS interference (rfi.stanford.edu): fetch of the daily heatmaps.

Licence: CC BY-NC 4.0. One file per day, `jamming/YYYY/MM/DD/heatmap.json`,
for the whole world. The server sends only its own certificate, not the
intermediate "InCommon RSA Server CA 2", so the fetch trusts the copy that this
package holds (`CA_FILE`) and always checks TLS. The server compresses only
with brotli, so the fetch asks for it and keeps the body as sent.
"""

import http.client
import logging
import ssl
import time
from collections.abc import Sequence
from datetime import date
from pathlib import Path

import brotli

from tracker.store import Payload

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
) -> list[Payload]:
    """Fetch the daily heatmaps of `days` over one TLS connection.

    Args:
        days: The days to fetch.
        context: The TLS context. Default: `tls_context()`.
        host: The server.
        port: The server port.

    Returns:
        One payload per day, in the order of `days`.

    Raises:
        OSError: A TLS check failed (`ssl.SSLCertVerificationError`), a request
            failed, or the status of a day is not 200 (a day not published yet
            gives 404).
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
        "stanford: %d day(s) fetched in %.1f s", len(days), time.monotonic() - started
    )
    return payloads
