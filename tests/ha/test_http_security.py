"""Real TLS redirect and decoded HTTP size boundaries against local servers."""

import ipaddress
import ssl
import zlib
from datetime import UTC, datetime, timedelta

import pytest
from aiohttp import ClientSession, TCPConnector, web
from aiohttp.test_utils import TestServer
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

pytest.importorskip("pytest_homeassistant_custom_component")
from custom_components.videolink_doorbell.api import VideolinkClient, VideolinkError


@pytest.fixture
def tls_contexts(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(UTC)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([
            x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
        .sign(key, hashes.SHA256()))
    cert_path, key_path = tmp_path / "cert.pem", tmp_path / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server.load_cert_chain(cert_path, key_path)
    return server, ssl.create_default_context(cafile=str(cert_path))


@pytest.mark.parametrize("status", [307, 308])
@pytest.mark.parametrize("destination_tls", [False, True])
async def test_trusted_tls_login_does_not_redirect_password(tls_contexts, status, destination_tls, socket_enabled):
    server_tls, client_tls = tls_contexts
    received = []
    async def sink(request):
        received.append(await request.read())
        return web.json_response([])
    target_app = web.Application()
    target_app.router.add_post("/sink", sink)
    target = TestServer(target_app, scheme="https" if destination_tls else "http")
    await target.start_server(ssl=server_tls if destination_tls else None)
    async def redirect(_request):
        return web.Response(status=status, headers={"Location": str(target.make_url("/sink"))})
    app = web.Application()
    app.router.add_post("/cgi-bin/api.cgi", redirect)
    source = TestServer(app, scheme="https")
    await source.start_server(ssl=server_tls)
    try:
        async with ClientSession(connector=TCPConnector(ssl=client_tls)) as session:
            client = VideolinkClient(session, "127.0.0.1", "user", "dummy-password",
                port=source.port, verify_ssl=True)
            with pytest.raises(VideolinkError, match="redirects"):
                await client.login()
        assert received == []
    finally:
        await source.close()
        await target.close()


@pytest.mark.parametrize("snapshot", [False, True])
async def test_compressed_decoded_response_is_bounded(snapshot, socket_enabled):
    async def oversized(_request):
        return web.Response(body=zlib.compress(b"x" * 10000), headers={"Content-Encoding": "deflate"})
    app = web.Application()
    app.router.add_route("*", "/cgi-bin/api.cgi", oversized)
    async with TestServer(app) as server, ClientSession() as session:
        class Client(VideolinkClient):
            @property
            def base_url(self):
                return str(server.make_url("/")).rstrip("/")
        client = Client(session, "127.0.0.1", "user", "dummy")
        client.MAX_JSON_BYTES = client.MAX_SNAPSHOT_BYTES = 100
        client._token = "dummy-token"
        client._token_expires = datetime.now(UTC) + timedelta(hours=1)
        with pytest.raises(VideolinkError, match="size limit"):
            await (client.snapshot(0) if snapshot else client.device_info())
