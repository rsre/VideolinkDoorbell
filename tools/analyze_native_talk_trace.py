#!/usr/bin/env python3
"""Summarize an official-app Baichuan talk relay trace."""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
import re
import statistics


LINE = re.compile(
    r"^(?P<timestamp>\d+(?:\.\d+)?) session=(?P<session>\d+) "
    r"direction=(?P<direction>\w+) bytes=(?P<bytes>\d+)$"
)
PREFIX = re.compile(
    r"^(?P<timestamp>\d+(?:\.\d+)?) session=(?P<session>\d+) "
    r"bytes=(?P<bytes>\d+) prefix=(?P<prefix>[0-9a-f]+)$"
)


def analyze(trace_path: Path, prefix_path: Path | None = None) -> int:
    audio: list[tuple[float, int, int]] = []
    for line in trace_path.read_text(encoding="utf-8").splitlines():
        match = LINE.match(line)
        if match and match["direction"] == "client_to_camera":
            size = int(match["bytes"])
            if size >= 600:
                audio.append((float(match["timestamp"]), int(match["session"]), size))
    if not audio:
        print("No client-to-camera audio writes found")
        return 1

    by_size = Counter(size for _, _, size in audio)
    intervals = []
    for session in {session for _, session, _ in audio}:
        session_audio = [item for item in audio if item[1] == session]
        intervals.extend(
            right[0] - left[0]
            for left, right in zip(session_audio, session_audio[1:])
            if right[0] - left[0] < 0.2
        )
    print(f"Audio writes: {len(audio)}")
    print(f"Sessions: {len({session for _, session, _ in audio})}")
    print(f"Write sizes: {dict(sorted(by_size.items()))}")
    if intervals:
        print(f"Cadence mean: {statistics.mean(intervals) * 1000:.1f} ms")
        print(f"Cadence median: {statistics.median(intervals) * 1000:.1f} ms")

    if prefix_path is not None and prefix_path.exists():
        prefixes = []
        for line in prefix_path.read_text(encoding="utf-8").splitlines():
            match = PREFIX.match(line)
            if match:
                prefixes.append((int(match["bytes"]), match["prefix"]))
        if prefixes:
            print(f"Captured prefixes: {len(prefixes)}")
            print(f"Prefix lengths: {sorted(Counter(len(prefix) // 2 for _, prefix in prefixes))}")
            print(f"Distinct 24-byte headers: {len({prefix[:48] for _, prefix in prefixes})}")
            print(f"First header: {prefixes[0][1][:48]}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trace", type=Path)
    parser.add_argument("--prefixes", type=Path)
    args = parser.parse_args()
    return analyze(args.trace, args.prefixes)


if __name__ == "__main__":
    raise SystemExit(main())
