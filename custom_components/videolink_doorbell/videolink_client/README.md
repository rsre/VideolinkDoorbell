# Videolink doorbell client

An async CGI and Baichuan protocol client with no dependency on Home Assistant.
It provides camera metadata, snapshots, authenticated stream URLs, native talk,
ADPCM encoding and framed TCP transport.

## Install and use

Until a public release is published, install from this directory:

```sh
python -m pip install ./custom_components/videolink_doorbell/videolink_client
```

```python
from aiohttp import ClientSession
from videolink_client import VideolinkClient

async def read_device():
    async with ClientSession() as session:
        client = VideolinkClient(
            session, "camera.example", "admin", "password", verify_ssl=True
        )
        try:
            return await client.device_info()
        finally:
            await client.native_talk_stop()
```

The caller owns the HTTP session. Network methods have deadlines; callers must
stop native talk when removing a client. Stream URLs contain camera credentials
or temporary tokens and must not be logged. Native talk uses the camera's local
Baichuan service; it does not provide TLS authentication.

## Test and build

From the repository root, run the protocol tests without Home Assistant:

```sh
python -m pip install -r requirements-test.txt
python -m pytest -q tests/test_api.py tests/test_native_talk.py
python -m pip install build
python -m build --outdir /tmp/videolink-client-dist custom_components/videolink_doorbell/videolink_client
```

Both a wheel and source distribution can be published independently. The HACS
integration currently imports this bundled copy through compatibility modules;
this avoids depending on an unpublished package. Before Core submission,
publish the client, pin its release in the integration manifest, and replace
bundled imports with `videolink_client` imports. Public package publication is
not performed by building these artifacts.
