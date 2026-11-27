"""The Stanford fetch: TLS check, brotli payloads and the raw store."""

import hashlib
import http.server
import ssl
import subprocess
import threading
from datetime import date

import brotli
import pandas as pd
import pytest
from conftest import FIXTURES

from tracker import clock, stanford
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

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(self.headers["Accept-Encoding"])
            body = brotli.compress(FIXTURE)
            self.send_response(200)
            self.send_header("Content-Encoding", "br")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    context.load_cert_chain(cert, key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_address[1], cert, seen
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
    port, _, _ = tls_server
    with pytest.raises(ssl.SSLCertVerificationError):
        stanford.fetch_days([DAY], stanford.tls_context(), "localhost", port)


@pytest.mark.enable_socket
def test_the_fetch_asks_for_brotli_and_returns_the_decoded_file_once_trusted(
    tls_server,
):
    port, cert, seen = tls_server
    context = stanford.tls_context(cert)
    (payload,) = stanford.fetch_days([DAY], context, "localhost", port)
    assert seen == ["br"]
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname
    assert payload.decoded == FIXTURE
    assert brotli.decompress(payload.content) == FIXTURE
    assert payload.name == "heatmap-2026-11-22.json.br"
