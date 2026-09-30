"""Decrypt one official-app talk extension from private local PCAPs.

The login and talk captures must come from the same TCP connection. The camera
password is read from the terminal without echo and is never written to disk.
"""

from __future__ import annotations

import argparse
import getpass
import os
import re
import subprocess
import sys
from pathlib import Path
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "custom_components" / "videolink_doorbell"))

from native_talk import (
    aes_cfb_decrypt,
    bc_encrypt,
    make_aes_key,
    parse_baichuan_header,
    split_baichuan_message,
)


def packets(path: Path, display_filter: str):
    result = subprocess.run(
        [
            "tshark", "-r", str(path), "-Y", display_filter,
            "-T", "fields", "-e", "tcp.srcport", "-e", "tcp.dstport",
            "-e", "tcp.payload",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    for line in result.stdout.splitlines():
        fields = line.split("\t")
        if len(fields) != 3 or not fields[2]:
            continue
        yield int(fields[0]), int(fields[1]), bytes.fromhex(fields[2].replace(":", ""))


def talk_extension(path: Path) -> tuple[int, bytes]:
    for source_port, _, data in packets(path, "tcp.dstport == 9000 && tcp.len > 0"):
        try:
            header = parse_baichuan_header(data)
            if header.message_id != 202 or len(data) != header.header_length + header.body_length:
                continue
            _, extension, media = split_baichuan_message(data)
        except ValueError:
            continue
        if len(extension) == 131 and len(media) == 528:
            return source_port, extension
    raise ValueError("no complete 683-byte official-app talk frame found")


def login_nonce(path: Path, app_port: int) -> str:
    display_filter = f"tcp.srcport == 9000 && tcp.dstport == {app_port} && tcp.len < 600"
    for _, _, data in packets(path, display_filter):
        try:
            header = parse_baichuan_header(data)
            if header.message_id != 1 or len(data) != header.header_length + header.body_length:
                continue
            _, _, payload = split_baichuan_message(data)
        except ValueError:
            continue
        for candidate in (payload, bc_encrypt(0, payload)):
            match = re.search(rb"<nonce>([^<]+)</nonce>", candidate)
            if match is not None:
                return match.group(1).decode("ascii")
    raise ValueError("no login nonce found for the talk connection")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--login-pcap", type=Path, required=True)
    parser.add_argument("--talk-pcap", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    app_port, extension = talk_extension(args.talk_pcap)
    nonce = login_nonce(args.login_pcap, app_port)
    password = getpass.getpass("Camera password (input hidden): ")
    try:
        decrypted = aes_cfb_decrypt(make_aes_key(password, nonce), extension)
    finally:
        del password
    if not decrypted.startswith(b"<?xml"):
        raise ValueError("extension did not decrypt as XML; check the password and PCAP pair")
    ElementTree.fromstring(decrypted)

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(args.output, flags, 0o600)
    with os.fdopen(descriptor, "wb") as output:
        output.write(decrypted)
    print(f"Decrypted {len(decrypted)} extension bytes to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
