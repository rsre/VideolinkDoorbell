"""Compatibility access to the independently packaged native protocol module."""

if __package__:
    from .videolink_client import native_talk as _native_talk
else:  # Standalone developer tools add this directory to sys.path.
    from videolink_client import native_talk as _native_talk


def __getattr__(name: str):
    """Keep developer probes compatible with the extracted implementation."""
    return getattr(_native_talk, name)
