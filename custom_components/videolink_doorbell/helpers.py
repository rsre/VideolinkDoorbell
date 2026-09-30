"""Shared stable registry identifiers."""


def device_identifier(unique_id: str, channel: int) -> str:
    """Return the physical-device ID from a per-channel entry identity."""
    suffix = f"_channel_{channel}"
    if not unique_id.endswith(suffix):
        raise ValueError(f"Invalid Videolink config-entry unique ID: {unique_id}")
    return unique_id[: -len(suffix)]
