#!/usr/bin/env python3
"""Print every native-talk profile advertised by a Reolink camera."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "custom_components" / "videolink_doorbell"))

from native_talk import NativeTalkSession  # noqa: E402


async def probe(args: argparse.Namespace) -> int:
    username = args.username or os.environ.get("VIDEOLINK_USERNAME") or input("Username: ")
    password = args.password or os.environ.get("VIDEOLINK_PASSWORD") or getpass.getpass("Password: ")
    session = NativeTalkSession(
        args.host,
        username,
        password,
        channel=args.channel,
        trace=None,
        max_encryption=args.max_encryption,
    )
    try:
        await session.login()
        selected = await session.talk_ability()
        profiles = session.last_talk_ability_profiles
        print(f"Native talk profiles: {len(profiles)}")
        for index, profile in enumerate(profiles, 1):
            selected_marker = " (selected)" if profile == selected else ""
            print(
                f"{index}. type={profile.audio_type} "
                f"rate={profile.sample_rate} Hz "
                f"precision={profile.sample_precision} bit "
                f"frame={profile.length_per_encoder} samples "
                f"track={profile.sound_track} "
                f"duplex={profile.duplex} "
                f"mode={profile.audio_stream_mode}{selected_marker}"
            )
        if session.last_talk_ability_xml is not None:
            print("TalkAbility XML:")
            print(session.last_talk_ability_xml.decode("utf-8", errors="replace"))
        return 0
    finally:
        await session.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.environ.get("VIDEOLINK_HOST", "192.168.1.40"))
    parser.add_argument("--username")
    parser.add_argument("--password")
    parser.add_argument("--channel", type=int, default=0)
    parser.add_argument("--max-encryption", type=lambda value: int(value, 0), default=0xDC12)
    args = parser.parse_args()
    try:
        return asyncio.run(probe(args))
    except Exception as err:
        print(f"native talk profile probe failed: {type(err).__name__}: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
