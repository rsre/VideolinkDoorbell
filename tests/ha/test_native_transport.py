"""Real Baichuan transport against a local fake camera."""

import asyncio

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")
from custom_components.videolink_doorbell.videolink_client import native_talk


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,rejected", [(0, False), (1, False), (2, False), (0x12, False), (1, True)])
async def test_real_native_login_and_negotiation_on_loopback(mode, rejected, socket_enabled):
    """Exercise the real TCP framing, login cipher, and negotiated configuration."""
    async def camera(reader, writer):
        client = native_talk.BaichuanTcpClient("127.0.0.1")
        client.reader = reader
        try:
            upgrade, _, _ = await client.receive()
            assert upgrade.message_id == 1
            nonce = "dummy-nonce"
            def reply(message_id, payload, code=200):
                writer.write(native_talk.serialize_talk_message(
                    msg_id=message_id, msg_num=0, channel_id=0, extension=b"",
                    payload=payload, response_code=code))
            reply(1, b"<body><nonce>dummy-nonce</nonce></body>", 0xDD00 | mode)
            await writer.drain()
            login, _, payload = await client.receive()
            assert login.message_id == 1
            clear = native_talk.bc_encrypt(0, payload)
            username, password = native_talk.modern_login_digests("user", "password", nonce)
            assert username.encode() in clear and password.encode() in clear
            reply(1, b"<body/>", 401 if rejected else 200)
            await writer.drain()
            if rejected:
                return
            header, _, _ = await client.receive()
            assert header.message_id == 10
            ability = b'<body><TalkAbility><audioConfig><audioType>adpcm</audioType><sampleRate>16000</sampleRate><lengthPerEncoder>1024</lengthPerEncoder></audioConfig></TalkAbility></body>'
            if mode in (2, 0x12):
                ability = native_talk.aes_cfb_encrypt(native_talk.make_aes_key("password", nonce), ability)
            elif mode == 1:
                ability = native_talk.bc_encrypt(0, ability)
            reply(10, ability)
            await writer.drain()
            header, _, _ = await client.receive()
            assert header.message_id == native_talk.MSG_ID_TALK_CONFIG
            reply(header.message_id, b"")
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()
    server = await asyncio.start_server(camera, "127.0.0.1", 0)
    session = native_talk.NativeTalkSession("127.0.0.1", "user", "password")
    session.client.port = server.sockets[0].getsockname()[1]
    try:
        if rejected:
            with pytest.raises(native_talk.NativeTalkAuthError):
                await session.login()
        else:
            await session.login()
            config = (await session.talk_ability()).to_config(0)
            await session.open_talk(config)
    finally:
        await session.close()
        server.close()
        await server.wait_closed()
