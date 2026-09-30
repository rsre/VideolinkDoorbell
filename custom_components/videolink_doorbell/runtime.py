"""Resources owned by a Videolink config entry."""

from dataclasses import dataclass

from .api import DeviceInfo, VideolinkClient


@dataclass(slots=True)
class VideolinkRuntime:
    """Client and identity validated before platform setup."""

    client: VideolinkClient
    device_info: DeviceInfo
