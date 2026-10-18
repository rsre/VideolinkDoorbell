"""Read camera serial and UID over CGI without Home Assistant or dependencies."""

from __future__ import annotations

import argparse
import getpass
import json
import socket
import ssl
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import (
    HTTPRedirectHandler,
    HTTPSHandler,
    ProxyHandler,
    Request,
    build_opener,
)


def failure_reason(error: Exception) -> str:
    """Describe transport failures without printing URLs or server-controlled text."""
    if isinstance(error, HTTPError):
        if error.code in (401, 403):
            return f"HTTP {error.code}: authentication or camera access rejected"
        if 300 <= error.code < 400:
            return f"HTTP {error.code}: camera redirected the request; redirects are blocked"
        return f"HTTP {error.code}: camera HTTP request failed"
    reason = error.reason if isinstance(error, URLError) else error
    if isinstance(reason, ssl.SSLCertVerificationError):
        return "HTTPS certificate verification failed; use --insecure for a self-signed camera certificate"
    if isinstance(reason, ssl.SSLError):
        return "TLS handshake failed; verify that this port serves HTTPS"
    if isinstance(reason, socket.gaierror):
        return "hostname resolution failed; check --host or use the camera IP"
    if isinstance(reason, ConnectionRefusedError):
        return "connection refused; check the camera HTTPS port and whether HTTPS is enabled"
    if isinstance(reason, TimeoutError):
        return "connection or response timed out; check camera reachability from this machine"
    if isinstance(reason, OSError):
        return f"network failure (errno={reason.errno}); check routing, firewall and local-network access"
    if isinstance(error, URLError):
        return "URL transport failed; check camera reachability and HTTPS settings"
    return f"invalid or rejected API response ({type(error).__name__})"


class NoRedirects(HTTPRedirectHandler):
    """Keep authenticated requests on the camera origin."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True, help="Camera hostname or IP, without scheme or port")
    parser.add_argument("--port", type=int, default=443)
    parser.add_argument("--username", default="admin")
    parser.add_argument("--insecure", action="store_true", help="Allow the camera's self-signed HTTPS certificate")
    args = parser.parse_args()
    host = f"[{args.host}]" if ":" in args.host and not args.host.startswith("[") else args.host
    origin = f"https://{host}:{args.port}"
    try:
        parsed = urlsplit(origin)
        valid_host = parsed.hostname and parsed.port == args.port
    except ValueError:
        valid_host = False
    if not valid_host or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
        parser.error("Enter a hostname or IP only, with --port for a custom HTTPS port")
    password = getpass.getpass("Camera password: ")
    context = ssl.create_default_context()
    if args.insecure:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    # Match the integration's direct LAN connection instead of using shell proxies.
    opener = build_opener(ProxyHandler({}), NoRedirects(), HTTPSHandler(context=context))
    token = None
    current_command = "Login"

    def command(name: str, param: dict | None = None) -> dict:
        nonlocal current_command
        current_command = name
        query = {"cmd": name}
        if token:
            query["token"] = token
        request = Request(
            f"{origin}/cgi-bin/api.cgi?{urlencode(query)}",
            data=json.dumps([{"cmd": name, "action": 0, "param": param or {}}]).encode(),
            headers={"Content-Type": "application/json"},
        )
        with opener.open(request, timeout=15) as response:
            raw = response.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError("Response exceeds size limit")
        result = json.loads(raw)[0]
        if result.get("code") != 0:
            # Camera error details can echo request data; report codes only.
            raise ValueError(f"{name} rejected (code={result.get('code')}, rspCode={result.get('error', {}).get('rspCode')})")
        return result["value"]

    try:
        login = command("Login", {"User": {"userName": args.username, "password": password}})
        token = login["Token"]["name"]
        serials = []
        for attempt in range(3):
            info = command("GetDevInfo")["DevInfo"]
            serials.append(info.get("serial"))
            print(json.dumps({"read": attempt + 1, "serial": serials[-1], "model": info.get("model"), "firmware": info.get("firmVer")}))
        print(json.dumps({"serial_stable_across_reads": serials[0] == serials[1] == serials[2]}))
        try:
            print(json.dumps({"uid": command("GetP2p")["P2p"].get("uid")}))
        except (ValueError, KeyError, TypeError, IndexError, URLError, OSError) as err:
            print(f"UID lookup failed: {failure_reason(err)}; serial results above are still valid.")
        return 0
    except (ValueError, KeyError, TypeError, IndexError, URLError, OSError) as err:
        print(f"Identity probe failed during {current_command}: {failure_reason(err)}.", file=sys.stderr)
        return 1
    finally:
        if token:
            try:
                command("Logout")
            except Exception:  # noqa: BLE001 - best-effort token cleanup
                print("Logout failed; the temporary camera session will expire.", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
