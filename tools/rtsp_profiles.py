#!/usr/bin/env python3
"""Print all audio/video profiles advertised by a camera's RTSP SDP."""

from __future__ import annotations

import argparse
import getpass
import os
import re
import sys

from rtsp_backchannel import RtspBackchannel


def _profile_lines(sdp: str) -> list[str]:
    profiles: list[str] = []
    for section in sdp.split("m=")[1:]:
        lines = section.splitlines()
        media = lines[0].split()
        if len(media) < 4:
            continue
        kind, port, transport, *payloads = media
        attributes = {line[2:].split(":", 1)[0]: line[2:].split(":", 1)[1]
                      for line in lines[1:] if line.startswith("a=") and ":" in line[2:]}
        direction = next(
            (line[2:] for line in lines[1:] if line[2:] in {"sendonly", "recvonly", "sendrecv", "inactive"}),
            "sendrecv",
        )
        maps = re.findall(r"a=rtpmap:(\d+)\s+([^\s]+)", section, re.IGNORECASE)
        mapping = ", ".join(f"{payload}={codec}" for payload, codec in maps) or "none"
        control = attributes.get("control", "(none)")
        profiles.append(
            f"{kind}: port={port} transport={transport} payloads={','.join(payloads)} "
            f"rtpmap={mapping} direction={direction} control={control}"
        )
    return profiles


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.environ.get("VIDEOLINK_HOST", "192.168.1.40"))
    parser.add_argument("--port", type=int, default=554)
    parser.add_argument("--path", default="/h264Preview_01_main")
    parser.add_argument("--username")
    parser.add_argument("--password")
    args = parser.parse_args()
    username = args.username or os.environ.get("VIDEOLINK_USERNAME") or input("Username: ")
    password = args.password or os.environ.get("VIDEOLINK_PASSWORD") or getpass.getpass("Password: ")
    url = f"rtsp://{args.host}:{args.port}{args.path}"
    client = RtspBackchannel(url, username, password)
    try:
        sdp = client.describe()
        profiles = _profile_lines(sdp)
        print(f"RTSP profiles: {len(profiles)}")
        for index, profile in enumerate(profiles, 1):
            print(f"{index}. {profile}")
        return 0
    except Exception as err:
        print(f"RTSP profile probe failed: {type(err).__name__}: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
