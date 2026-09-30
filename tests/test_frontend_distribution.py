"""Verify the card can ship independently and the HACS copy stays current."""

import hashlib
from zipfile import ZipFile

import pytest

from tools import build_frontend


def test_standalone_archive_contains_only_card_distribution(tmp_path):
    version = build_frontend.card_version()
    archive = build_frontend.build(tmp_path, version)
    assert archive.name == f"videolink-doorbell-card-{version}.zip"
    with ZipFile(archive) as package:
        assert set(package.namelist()) == {"videolink-doorbell.js", "README.md", "LICENSE", "SHA256SUMS"}
        assert package.read("videolink-doorbell.js") == build_frontend.SOURCE.read_bytes()
        for checksum in package.read("SHA256SUMS").decode().splitlines():
            digest, name = checksum.split("  ", 1)
            assert hashlib.sha256(package.read(name)).hexdigest() == digest


@pytest.mark.parametrize("stale_part", ["javascript", "cache_version"])
def test_bundle_check_rejects_stale_code_and_cache_version(tmp_path, monkeypatch, stale_part):
    version = build_frontend.card_version()
    bundle = tmp_path / "videolink-doorbell.js"
    bundle.write_bytes(build_frontend.SOURCE.read_bytes() if stale_part == "cache_version" else b"stale code")
    installer = tmp_path / "hacs_frontend.py"
    installer.write_text(f'CARD_VERSION = "{"0.0.0" if stale_part == "cache_version" else version}"\n')
    monkeypatch.setattr(build_frontend, "BUNDLE", bundle)
    monkeypatch.setattr(build_frontend, "INSTALLER", installer)
    with pytest.raises(ValueError, match="HACS card bundle is stale"):
        build_frontend.check_bundle(version)
    build_frontend.sync_bundle(version)
    build_frontend.check_bundle(version)
