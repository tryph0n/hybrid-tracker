"""The Stanford fetch: TLS check, brotli payloads and the raw store."""

import functools
import hashlib
import http.server
import ssl
import subprocess
import threading
from datetime import date, timedelta
from types import SimpleNamespace

import brotli
import pandas as pd
import pytest
from conftest import FIXTURES

from tracker import clock, stanford
from tracker.errors import UserError
from tracker.ingest import lineage
from tracker.sources import SOURCES
from tracker.store import LocalBackend, Store

DAY = date(2026, 11, 22)
FIXTURE = (FIXTURES / "stanford_heatmap.json").read_bytes()
URL = "https://waas-nas.stanford.edu/data/jamming/2026/11/22/heatmap.json"
INTERMEDIATE_SHA256 = "87e01cc4dd0c9d92a3dbd49092ff13f9cd387445cdc57e5b984e1b7721b5b029"


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    now = pd.Timestamp("2026-11-24 04:17:00", tz="UTC")
    monkeypatch.setattr(clock, "utc_now", lambda: now)


@pytest.fixture
def local(tmp_path):
    return Store(LocalBackend(tmp_path))


def payload_from(body: bytes, encoding: str = "br"):
    return stanford.make_payload(DAY, URL, 200, body, encoding)


@pytest.fixture
def tls_server(tmp_path):
    """A local HTTPS server with a throwaway certificate, serving the fixture in brotli."""
    cert = tmp_path / "server.pem"
    key = tmp_path / "server.key"
    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
            "-keyout", str(key), "-out", str(cert), "-days", "1",
            "-subj", "/CN=localhost",
            "-addext", "subjectAltName=DNS:localhost",
            "-addext", "basicConstraints=critical,CA:TRUE",
        ],
        check=True,
        capture_output=True,
    )  # fmt: skip
    seen: list[str] = []
    connections: list[str] = []
    answer: dict = {"status": 200, "files": None, "paths": []}

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def setup(self):
            connections.append(self.client_address[0])
            super().setup()

        def do_GET(self):
            seen.append(self.headers["Accept-Encoding"])
            answer["paths"].append(self.path)
            status = answer["status"]
            content = FIXTURE
            if answer["files"] is not None:
                content = answer["files"].get(self.path)
                status = 200 if content is not None else 404
            body = brotli.compress(content or b"")
            self.send_response(status)
            self.send_header("Content-Encoding", "br")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(cert, key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_address[1], cert, seen, connections, answer
    server.shutdown()
    thread.join()
    server.server_close()


def test_the_store_keeps_the_day_in_brotli_and_the_decoded_file_is_the_fixture(
    local, tmp_path
):
    line = local.save(payload_from(brotli.compress(FIXTURE)))
    kept = (tmp_path / line["path"]).read_bytes()
    assert line["path"].endswith("heatmap-2026-11-22.json.br")
    assert len(kept) < len(FIXTURE)
    assert brotli.decompress(kept) == FIXTURE
    assert line["sha256"] == hashlib.sha256(FIXTURE).hexdigest()


def test_the_same_day_compressed_another_way_adds_no_payload(local):
    first = brotli.compress(FIXTURE, quality=11)
    other = brotli.compress(FIXTURE, quality=2)
    assert first != other
    stored = local.save(payload_from(first))
    again = local.save(payload_from(other))
    assert stored["stored"] and not again["stored"]
    assert again["path"] == stored["path"]
    assert len(local.payloads(stanford.SOURCE)) == 1


def test_a_changed_day_adds_a_payload_and_other_days_do_not_collide(local):
    local.save(payload_from(brotli.compress(FIXTURE)))
    changed = FIXTURE.replace(b'"lowQualityCount":3', b'"lowQualityCount":4')
    assert local.save(payload_from(brotli.compress(changed)))["stored"]
    next_day = stanford.make_payload(
        date(2026, 11, 23), URL, 200, brotli.compress(FIXTURE), "br"
    )
    assert local.save(next_day)["stored"]
    names = [line["name"] for line in local.payloads(stanford.SOURCE)]
    assert names == [
        "heatmap-2026-11-22.json.br",
        "heatmap-2026-11-22.json.br",
        "heatmap-2026-11-23.json.br",
    ]


def test_a_file_sent_uncompressed_is_kept_in_brotli_with_the_same_decoded_file(local):
    payload = payload_from(FIXTURE, "")
    assert brotli.decompress(payload.content) == FIXTURE
    stored = local.save(payload_from(brotli.compress(FIXTURE)))
    assert not local.save(payload)["stored"]
    assert stored["sha256"] == hashlib.sha256(FIXTURE).hexdigest()


def test_an_unknown_content_encoding_is_refused():
    with pytest.raises(OSError, match="gzip"):
        payload_from(b"x", "gzip")


def test_the_repository_holds_the_intermediate_certificate():
    der = ssl.PEM_cert_to_DER_cert(stanford.CA_FILE.read_text())
    assert hashlib.sha256(der).hexdigest() == INTERMEDIATE_SHA256
    subjects = [
        dict(item[0] for item in certificate["subject"])
        for certificate in stanford.tls_context().get_ca_certs()
    ]
    assert any(s.get("commonName") == "InCommon RSA Server CA 2" for s in subjects)


@pytest.mark.enable_socket
def test_the_fetch_fails_against_a_server_whose_certificate_is_not_trusted(tls_server):
    port, *_ = tls_server
    with pytest.raises(ssl.SSLCertVerificationError):
        stanford.fetch_days([DAY], stanford.tls_context(), "localhost", port)


@pytest.mark.enable_socket
def test_the_fetch_asks_for_brotli_and_returns_the_decoded_file_once_trusted(
    tls_server,
):
    port, cert, seen, *_ = tls_server
    context = stanford.tls_context(cert)
    (payload,) = stanford.fetch_days([DAY], context, "localhost", port)
    assert seen == ["br"]
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname
    assert payload.decoded == FIXTURE
    assert brotli.decompress(payload.content) == FIXTURE
    assert payload.name == "heatmap-2026-11-22.json.br"


@pytest.mark.enable_socket
def test_the_fetch_of_two_days_uses_one_connection(tls_server):
    port, cert, seen, connections, _ = tls_server
    days = [DAY, date(2026, 11, 23)]
    payloads = stanford.fetch_days(days, stanford.tls_context(cert), "localhost", port)
    assert [p.name for p in payloads] == [
        "heatmap-2026-11-22.json.br",
        "heatmap-2026-11-23.json.br",
    ]
    assert len(seen) == 2
    assert len(connections) == 1


@pytest.mark.enable_socket
def test_the_fetch_raises_oserror_when_the_server_answers_404(tls_server):
    port, cert, _, _, answer = tls_server
    answer["status"] = 404
    with pytest.raises(OSError, match="HTTP 404"):
        stanford.fetch_days([DAY], stanford.tls_context(cert), "localhost", port)


FIRST = date(2026, 11, 20)


def heatmap_path(day: date) -> str:
    return stanford.HEATMAP_PATH.format(day=day)


def served(days: dict[date, bytes]) -> dict[str, bytes]:
    return {heatmap_path(day): content for day, content in days.items()}


def changed(low: int) -> bytes:
    return FIXTURE.replace(b'"lowQualityCount":3', f'"lowQualityCount":{low}'.encode())


def seed(store, days: dict[date, bytes]) -> None:
    for day, content in days.items():
        store.save(stanford.make_payload(day, URL, 200, brotli.compress(content), "br"))


def run(port, cert, store, today: date):
    fetch = functools.partial(
        stanford.fetch_days,
        context=stanford.tls_context(cert),
        host="localhost",
        port=port,
        skip_missing=True,
    )
    return stanford.catch_up(store, today, fetch)


@pytest.mark.enable_socket
def test_the_catch_up_fetches_missing_days_and_the_stored_ones_again(tls_server, local):
    port, cert, _, _, answer = tls_server
    days = [FIRST + timedelta(days=n) for n in range(5)]
    seed(local, {days[0]: FIXTURE, days[2]: FIXTURE})
    answer["files"] = served({day: FIXTURE for day in days[:4]})
    lines = run(port, cert, local, days[4])
    assert answer["paths"] == [heatmap_path(day) for day in days]
    assert {line["name"]: line["stored"] for line in lines} == {
        "heatmap-2026-11-20.json.br": False,
        "heatmap-2026-11-21.json.br": True,
        "heatmap-2026-11-22.json.br": False,
        "heatmap-2026-11-23.json.br": True,
    }
    answer["paths"].clear()
    answer["files"] = served({day: FIXTURE for day in days})
    lines = run(port, cert, local, days[4])
    assert heatmap_path(days[4]) in answer["paths"]
    assert [line["stored"] for line in lines] == [False] * 4 + [True]
    assert stanford.stored_days(local) == days


@pytest.mark.enable_socket
def test_the_first_run_stores_only_the_newest_day_and_the_table_starts_on_it(
    tls_server, local
):
    port, cert, _, _, answer = tls_server
    days = [FIRST + timedelta(days=n) for n in range(4)]
    answer["files"] = served({day: FIXTURE for day in days})
    run(port, cert, local, FIRST + timedelta(days=4))
    assert answer["paths"] == [
        heatmap_path(days[3] + timedelta(days=1)),
        heatmap_path(days[3]),
    ]
    assert stanford.stored_days(local) == [days[3]]
    table = stanford.context_table(stanford.decoded_files(local))
    assert set(table["day"]) == {days[3]}


@pytest.mark.enable_socket
def test_the_recheck_covers_the_last_seven_days_and_the_table_takes_the_new_file(
    tls_server, local
):
    port, cert, _, _, answer = tls_server
    days = [FIRST + timedelta(days=n) for n in range(8)]
    seed(local, {day: FIXTURE for day in days})
    answer["files"] = served({day: FIXTURE for day in days} | {days[3]: changed(4)})
    lines = run(port, cert, local, days[7] + timedelta(days=1))
    requested = [heatmap_path(day) for day in days[1:]]
    assert answer["paths"] == [*requested, heatmap_path(days[7] + timedelta(days=1))]
    assert [line["name"] for line in lines if line["stored"]] == [
        "heatmap-2026-11-23.json.br"
    ]
    table = stanford.context_table(stanford.decoded_files(local))
    on_day = table[(table["day"] == days[3]) & (table["h3_index"] == "841f91dffffffff")]
    assert on_day["low_quality_count"].tolist() == [4]


def test_the_table_reads_the_fixture_with_no_network():
    table = stanford.context_table({"heatmap-2026-11-22.json.br": FIXTURE})
    assert list(table.columns) == stanford.COLUMNS
    assert len(table) == 4


def test_the_table_keeps_the_cells_whose_centre_is_in_the_area_only():
    table = stanford.context_table({"heatmap-2026-11-22.json.br": FIXTURE})
    assert "84381a1ffffffff" not in set(table["h3_index"])
    assert "841f91dffffffff" in set(table["h3_index"])


def test_the_table_has_the_counts_and_the_percentage_and_keeps_a_one_aircraft_cell():
    table = stanford.context_table({"heatmap-2026-11-22.json.br": FIXTURE})
    cell = table[table["h3_index"] == "841f91dffffffff"].iloc[0]
    assert (cell["low_quality_count"], cell["total_aircraft_count"]) == (3, 120)
    assert cell["low_quality_percent"] == pytest.approx(2.5)
    one = table[table["total_aircraft_count"] == 1]
    assert one["h3_index"].tolist() == ["841fa81ffffffff"]


@pytest.mark.parametrize(
    ("old", "new", "reason"),
    [
        (
            b'"totalAircraftCount":48',
            b'"totalAircraftCount":0',
            "totalAircraftCount is 0",
        ),
        (b'"lowQualityCount":17', b'"lowQualityCount":65', "above totalAircraftCount"),
    ],
)
def test_a_row_with_no_aircraft_or_too_many_low_quality_ones_stops_the_table(
    old, new, reason
):
    with pytest.raises(UserError, match=f"2026-11-22 cell 841f.*{reason}"):
        stanford.context_table(
            {"heatmap-2026-11-22.json.br": FIXTURE.replace(old, new)}
        )


def test_the_lineage_takes_the_decoded_files_and_refuses_the_stored_bytes(
    local, monkeypatch, tmp_path
):
    monkeypatch.setitem(
        SOURCES, stanford.SOURCE, SimpleNamespace(updated_by_source=lambda files: None)
    )
    seed(local, {DAY: FIXTURE})
    seed(local, {DAY: changed(4)})
    files = stanford.decoded_files(local)
    record = lineage(stanford.SOURCE, files, local, rows=4)
    (entry,) = record["payloads"]
    assert entry["sha256"] == hashlib.sha256(changed(4)).hexdigest()
    assert entry["path"] == local.payloads(stanford.SOURCE)[-1]["path"]
    stored = (tmp_path / entry["path"]).read_bytes()
    with pytest.raises(UserError, match="not in the manifest"):
        lineage(stanford.SOURCE, {"heatmap-2026-11-22.json.br": stored}, local, rows=4)
