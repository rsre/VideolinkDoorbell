"""Build the standalone dashboard card and maintain the HACS vendored copy."""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
SOURCE = FRONTEND / "videolink-doorbell.js"
BUNDLE = ROOT / "custom_components/videolink_doorbell/frontend/videolink-doorbell.js"
INSTALLER = ROOT / "custom_components/videolink_doorbell/hacs_frontend.py"
_INSTALLER_VERSION = re.compile(r'^CARD_VERSION = "[^"]+"$', re.MULTILINE)


def card_version() -> str:
    """Read the card's own version without importing the Home Assistant backend."""
    match = re.search(r'^const CARD_VERSION = "([^"]+)";', SOURCE.read_text(), re.MULTILINE)
    if match is None or not re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?", match[1]):
        raise ValueError("The dashboard card needs a valid CARD_VERSION")
    return match[1]


def check_bundle(version: str) -> None:
    """Prevent shipping stale frontend code or an unchanged cache version in HACS."""
    match = _INSTALLER_VERSION.search(INSTALLER.read_text())
    if (
        BUNDLE.read_bytes() != SOURCE.read_bytes()
        or match is None
        or match[0] != f'CARD_VERSION = "{version}"'
    ):
        raise ValueError("HACS card bundle is stale; run python tools/build_frontend.py --sync-bundle")


def sync_bundle(version: str) -> None:
    """Vendor the independent frontend for the existing HACS distribution."""
    installer, count = _INSTALLER_VERSION.subn(f'CARD_VERSION = "{version}"', INSTALLER.read_text())
    if count != 1:
        raise ValueError("Unable to locate the HACS installer's CARD_VERSION")
    shutil.copyfile(SOURCE, BUNDLE)
    INSTALLER.write_text(installer)


def build(output_dir: Path, version: str) -> Path:
    """Package only JavaScript, installation instructions, license and checksums."""
    output_dir.mkdir(parents=True, exist_ok=True)
    files = ["videolink-doorbell.js", "README.md", "LICENSE"]
    for name, source in zip(files, [SOURCE, FRONTEND / "README.md", ROOT / "LICENSE"], strict=True):
        shutil.copyfile(source, output_dir / name)
    checksums = "".join(
        f"{hashlib.sha256((output_dir / name).read_bytes()).hexdigest()}  {name}\n" for name in files
    )
    (output_dir / "SHA256SUMS").write_text(checksums)
    archive = output_dir / f"videolink-doorbell-card-{version}.zip"
    with ZipFile(archive, "w", compression=ZIP_DEFLATED) as package:
        for name in [*files, "SHA256SUMS"]:
            package.write(output_dir / name, name)
    return archive


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    bundle_action = parser.add_mutually_exclusive_group()
    bundle_action.add_argument("--check-bundle", action="store_true")
    bundle_action.add_argument("--sync-bundle", action="store_true")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    try:
        version = card_version()
        if args.check_bundle:
            check_bundle(version)
        elif args.sync_bundle:
            sync_bundle(version)
        if args.output_dir is not None or not (args.check_bundle or args.sync_bundle):
            print(build(args.output_dir or FRONTEND / "dist", version))
    except ValueError as err:
        parser.error(str(err))


if __name__ == "__main__":
    main()
