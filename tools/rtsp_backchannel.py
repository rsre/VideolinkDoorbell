"""Minimal Reolink RTSP backchannel sender used by the audio testbed."""

from __future__ import annotations

import base64
import hashlib
import re
import secrets
import socket
import struct
import time
from urllib.parse import urlparse


class RtspBackchannel:
    """Open Reolink track3 and send PCMU RTP over TCP interleaved channel 0."""

    def __init__(self, url: str, username: str, password: str) -> None:
        parsed = urlparse(url)
        self.host = parsed.hostname or ""
        self.port = parsed.port or 554
        self.base_url = f"rtsp://{self.host}:{self.port}{parsed.path or '/'}"
        self.backchannel_url = self.base_url.rstrip("/") + "/track3"
        self.username = username
        self.password = password
        self.sock: socket.socket | None = None
        self._receive_buffer = bytearray()
        self.cseq = 1
        self.session: str | None = None
        self.auth_type: str | None = None
        self.auth: dict[str, str] = {}
        self.sequence = secrets.randbelow(65536)
        self.timestamp = secrets.randbelow(2**32)
        self.ssrc = secrets.randbelow(2**32)
        self.codec = "PCMU"
        self.sample_rate = 8000
        self.samples_per_packet = 160
        self.payload_type = 0

    def connect(self) -> None:
        self.sock = socket.create_connection((self.host, self.port), timeout=5)
        self.sock.settimeout(None)

    def _recv_exact(self, size: int) -> bytes:
        assert self.sock is not None
        while len(self._receive_buffer) < size:
            chunk = self.sock.recv(max(4096, size - len(self._receive_buffer)))
            if not chunk:
                raise ConnectionError("RTSP connection closed")
            self._receive_buffer.extend(chunk)
        data = bytes(self._receive_buffer[:size])
        del self._receive_buffer[:size]
        return data

    def _read_response(self) -> tuple[int, dict[str, str], bytes]:
        while True:
            first = self._recv_exact(1)
            if first == b"$":
                header = self._recv_exact(3)
                self._recv_exact(int.from_bytes(header[1:3], "big"))
                continue

            # The first byte was already consumed.  Restore it, then continue collecting the
            # RTSP header, while retaining any following response body or
            # interleaved frame in _receive_buffer for the next read.
            self._receive_buffer[:0] = first
            while b"\r\n\r\n" not in self._receive_buffer:
                self._receive_buffer.extend(self._recv_exact(4096))
            separator = self._receive_buffer.index(b"\r\n\r\n")
            header_data = bytes(self._receive_buffer[:separator])
            del self._receive_buffer[:separator + 4]
            lines = header_data.decode("utf-8", errors="replace").split("\r\n")
            status = int(lines[0].split()[1])
            headers: dict[str, str] = {}
            for line in lines[1:]:
                if ":" in line:
                    key, value = line.split(":", 1)
                    headers[key.lower()] = value.strip()
            length = int(headers.get("content-length", "0"))
            body = self._recv_exact(length)
            return status, headers, body

    def _parse_challenge(self, value: str) -> bool:
        if value.lower().startswith("basic"):
            self.auth_type = "basic"
            return True
        if not value.lower().startswith("digest"):
            return False
        self.auth_type = "digest"
        self.auth = {
            match.group(1).lower(): match.group(3) or match.group(4)
            for match in re.finditer(r'(\w+)=("([^"]*)"|([^,\s]+))', value[len("Digest "):])
        }
        return True

    def _authorization(self, method: str, uri: str) -> str | None:
        if self.auth_type == "basic":
            token = base64.b64encode(f"{self.username}:{self.password}".encode()).decode()
            return f"Basic {token}"
        if self.auth_type != "digest":
            return None
        realm = self.auth.get("realm", "")
        nonce = self.auth.get("nonce", "")
        if self.auth.get("algorithm", "MD5").upper() != "MD5":
            raise RuntimeError("only MD5 RTSP digest authentication is supported")
        md5 = lambda value: hashlib.md5(value.encode()).hexdigest()
        ha1 = md5(f"{self.username}:{realm}:{self.password}")
        ha2 = md5(f"{method}:{uri}")
        parts = [f'username="{self.username}"', f'realm="{realm}"',
                 f'nonce="{nonce}"', f'uri="{uri}"']
        qop = self.auth.get("qop")
        if qop:
            qop_value = "auth" if "auth" in qop.split(",") else qop.split(",")[0].strip()
            nc = "00000001"
            cnonce = secrets.token_hex(8)
            response = md5(f"{ha1}:{nonce}:{nc}:{cnonce}:{qop_value}:{ha2}")
            parts.extend([f"qop={qop_value}", f"nc={nc}", f'cnonce="{cnonce}"'])
        else:
            response = md5(f"{ha1}:{nonce}:{ha2}")
        parts.append(f'response="{response}"')
        if self.auth.get("opaque"):
            parts.append(f'opaque="{self.auth["opaque"]}"')
        return "Digest " + ", ".join(parts)

    def _request(self, method: str, uri: str, headers: dict[str, str] | None = None,
                 retry_auth: bool = True) -> tuple[int, dict[str, str], bytes]:
        if self.sock is None:
            raise RuntimeError("RTSP socket is not connected")
        request_headers = {"CSeq": str(self.cseq), "User-Agent": "Python-Reolink-Talkback/1.0"}
        request_headers.update(headers or {})
        if self.session:
            request_headers.setdefault("Session", self.session)
        authorization = self._authorization(method, uri)
        if authorization:
            request_headers["Authorization"] = authorization
        payload = f"{method} {uri} RTSP/1.0\r\n" + "".join(
            f"{key}: {value}\r\n" for key, value in request_headers.items()
        ) + "\r\n"
        self.sock.sendall(payload.encode())
        status, response_headers, body = self._read_response()
        self.cseq += 1
        if status == 401 and retry_auth and self._parse_challenge(response_headers.get("www-authenticate", "")):
            return self._request(method, uri, headers, retry_auth=False)
        if response_headers.get("session"):
            self.session = response_headers["session"].split(";", 1)[0].strip()
        return status, response_headers, body

    def open(self) -> None:
        self.connect()
        status, _, _ = self._request("OPTIONS", self.base_url)
        if status >= 300:
            raise RuntimeError(f"RTSP OPTIONS failed with HTTP {status}")
        status, _, _ = self._request(
            "DESCRIBE", self.base_url,
            {"Accept": "application/sdp", "Require": "www.onvif.org/ver20/backchannel"},
        )
        if status != 200:
            raise RuntimeError(f"RTSP DESCRIBE failed with HTTP {status}")
        status, _, _ = self._request(
            "SETUP", self.backchannel_url,
            {"Transport": "RTP/AVP/TCP;unicast;interleaved=0-1"},
        )
        if status != 200:
            raise RuntimeError(f"RTSP SETUP track3 failed with HTTP {status}")
        status, _, _ = self._request("PLAY", self.base_url)
        if status != 200:
            raise RuntimeError(f"RTSP PLAY failed with HTTP {status}")
        # Handshake timeouts protect the benchmark from a stalled camera;
        # streaming itself must remain blocking while RTP is sent.
        assert self.sock is not None
        self.sock.settimeout(None)

    def describe(self) -> str:
        """Fetch the camera SDP, including its ONVIF backchannel tracks."""
        self.connect()
        try:
            status, _, _ = self._request("OPTIONS", self.base_url)
            if status >= 300:
                raise RuntimeError(f"RTSP OPTIONS failed with HTTP {status}")
            status, _, body = self._request(
                "DESCRIBE", self.base_url,
                {"Accept": "application/sdp", "Require": "www.onvif.org/ver20/backchannel"},
            )
            if status != 200:
                raise RuntimeError(f"RTSP DESCRIBE failed with HTTP {status}")
            return body.decode("utf-8", errors="replace")
        finally:
            if self.sock is not None:
                self.sock.close()
                self.sock = None

    @staticmethod
    def _mulaw(sample: int) -> int:
        sign = 0x80 if sample < 0 else 0
        value = min(abs(sample), 32635) + 132
        exponent = max(0, min(7, value.bit_length() - 6))
        mantissa = (value >> (exponent + 3)) & 0x0F
        return (~(sign | (exponent << 4) | mantissa)) & 0xFF

    def send_pcm16(self, samples: list[int], sample_rate: int) -> float:
        if self.sock is None:
            raise RuntimeError("RTSP backchannel is not open")
        if sample_rate != 8000:
            raise ValueError("Reolink track3 expects PCMU at 8000 Hz")
        encoded = bytes(self._mulaw(sample) for sample in samples)
        rtp = struct.pack(">BBHII", 0x80, self.payload_type, self.sequence,
                          self.timestamp, self.ssrc) + encoded
        self.sock.sendall(b"$\x00" + struct.pack(">H", len(rtp)) + rtp)
        sent = time.monotonic()
        self.sequence = (self.sequence + 1) & 0xFFFF
        self.timestamp = (self.timestamp + len(samples)) & 0xFFFFFFFF
        return sent

    def close(self) -> None:
        if self.sock is not None:
            sock = self.sock
            try:
                # The camera may continue interleaving RTP while handling
                # TEARDOWN.  Do not let cleanup block the benchmark forever.
                sock.settimeout(1.0)
                self._request("TEARDOWN", self.base_url)
            except (OSError, RuntimeError, ConnectionError, TimeoutError):
                pass
            sock.close()
            self.sock = None
