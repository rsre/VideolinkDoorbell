"""Compatibility imports for the independently packaged camera client."""

if __package__:
    from .videolink_client.api import (
        DeviceInfo,
        VideolinkAuthError,
        VideolinkClient,
        VideolinkConnectionError,
        VideolinkError,
        VideolinkInvalidHostError,
    )
else:  # Standalone developer tools add this directory to sys.path.
    from videolink_client.api import (
        DeviceInfo,
        VideolinkAuthError,
        VideolinkClient,
        VideolinkConnectionError,
        VideolinkError,
        VideolinkInvalidHostError,
    )

__all__ = [
    "DeviceInfo",
    "VideolinkAuthError",
    "VideolinkClient",
    "VideolinkConnectionError",
    "VideolinkError",
    "VideolinkInvalidHostError",
]
