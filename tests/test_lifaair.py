"""Decode LIFAair LM05 frames captured from a real device.

Bytes are payload offsets 27..57 from firmware 10026, polled on 2026-11-17.
"""

from __future__ import annotations

import asyncio

import pytest

from broadlink_async.exceptions import StorageError
from broadlink_async.purifier import FanMode, lifaair


def payload(raw_hex: str) -> bytes:
    """Return a GET_STATE body with captured bytes placed at offset 27."""
    data = bytearray(58)
    data[27:] = bytes.fromhex(raw_hex)
    return bytes(data)


def device() -> lifaair:
    """Return a lifaair instance that does not touch the network."""
    return lifaair(("127.0.0.1", 80), b"\x00" * 6, 0x4EC2)


def run(coro):
    return asyncio.run(coro)


# Station up, purifier not yet online. 14:31:15.
OFFLINE_BOOT = (
    "00 00 00 02 90 01 00 00 09 00 02 00 02 00 03 00 "
    "00 00 00 00 00 00 00 00 00 00 00 00 01 00 c0"
)
# Unpaired after the pairing window. 15:11:10.
OFFLINE_UNPAIRED = (
    "00 00 00 02 ab 03 00 00 09 00 01 00 00 00 01 00 "
    "00 00 00 00 00 00 00 00 00 00 00 00 01 00 c0"
)
# First online frame after each reconnection: fan_state 0xA5, climate 00 00 00.
RECONNECT_ZEROS = (
    (
        "00 00 00 02 b5 03 00 00 0a 00 01 00 00 00 02 00 "
        "00 00 00 00 00 00 00 00 00 00 00 00 a5 2b c0",
        949,
        43,
    ),
    (
        "00 00 00 02 ee 03 00 00 0a 00 00 00 00 00 01 00 "
        "00 00 00 00 00 00 00 00 00 00 00 00 a5 2b c0",
        1006,
        43,
    ),
    (
        "00 00 00 02 90 01 00 00 0a 00 00 00 01 00 02 00 "
        "00 00 00 00 00 00 00 00 00 00 00 00 a5 14 c0",
        400,
        20,
    ),
    (
        "00 00 00 02 a8 03 00 00 0a 00 00 00 00 00 01 00 "
        "00 00 00 00 00 00 00 00 00 00 00 00 a5 2b c0",
        936,
        43,
    ),
)
# Next frame after the 14:44:57 placeholder. 14:45:01.
AFTER_RECONNECT = (
    "08 01 16 02 be 03 00 00 09 00 01 00 00 00 01 00 "
    "00 00 00 00 00 00 00 00 00 00 00 00 a5 2b c0"
)
# CO2 still 400, climate already valid. 15:01:50.
CO2_WARMUP = (
    "0a 01 16 02 90 01 00 00 09 00 00 00 02 00 03 00 "
    "00 00 00 00 00 00 00 00 00 00 00 00 a5 14 c0"
)
# Spray peak. 14:33:05.
SPRAY = (
    "0a 01 16 02 9f 03 00 00 09 00 0c 00 27 00 80 00 "
    "00 00 00 00 00 00 00 00 00 00 00 00 a5 0d c0"
)


def decode(raw_hex: str) -> dict:
    """Decode one captured frame."""
    return device()._decode_state(payload(raw_hex))


def test_offline_purifier_climate_and_mode_are_none():
    """Unplugged and unpaired both report 0x01 with zero climate bytes."""
    for raw_hex, co2 in ((OFFLINE_BOOT, 400), (OFFLINE_UNPAIRED, 939)):
        state = decode(raw_hex)
        assert state["temperature"] is None
        assert state["humidity"] is None
        assert state["fan_mode"] is None
        assert state["fan_speed"] is None
        assert state["purifier_online"] is False
        assert state["co2"] == co2
        assert state["formaldehyde"] == 0.0
        assert state["pm2_5"] is not None


def test_reconnect_placeholder_zeros_are_none():
    """Online fan_state with 00 00 00 climate is not 0.0 deg C / 0 %."""
    for raw_hex, co2, speed in RECONNECT_ZEROS:
        state = decode(raw_hex)
        assert state["temperature"] is None
        assert state["humidity"] is None
        assert state["fan_mode"] == FanMode.AUTO
        assert state["fan_speed"] == speed
        assert state["purifier_online"] is True
        assert state["co2"] == co2
        assert state["formaldehyde"] == 0.0


def test_real_climate_after_placeholder():
    """The frame after the placeholder carries the real reading."""
    state = decode(AFTER_RECONNECT)
    assert state["temperature"] == 26.4
    assert state["humidity"] == 22
    assert state["co2"] == 958
    assert state["fan_mode"] == FanMode.AUTO
    assert state["fan_speed"] == 43
    assert state["purifier_online"] is True


def test_co2_warmup_placeholder_is_kept():
    """400 ppm has no separate marker, so it is returned as 400."""
    state = decode(CO2_WARMUP)
    assert state["co2"] == 400
    assert state["temperature"] == 26.6
    assert state["humidity"] == 22
    assert state["tvoc"] == 90
    assert state["pm1"] == 0
    assert state["pm2_5"] == 2
    assert state["pm10"] == 3
    assert state["fan_mode"] == FanMode.AUTO
    assert state["fan_speed"] == 20
    assert state["purifier_online"] is True


def test_spray_frame_keeps_pm_order_and_zero_formaldehyde():
    """PM offsets stay as verified, and a zero HCHO reading stays 0.0."""
    state = decode(SPRAY)
    assert state["temperature"] == 26.6
    assert state["humidity"] == 22
    assert state["co2"] == 927
    assert state["formaldehyde"] == 0.0
    assert state["tvoc"] == 90
    assert state["pm1"] == 12
    assert state["pm2_5"] == 39
    assert state["pm10"] == 128
    assert state["fan_mode"] == FanMode.AUTO
    assert state["fan_speed"] == 13
    assert state["purifier_online"] is True


def test_fan_mode_and_speed_are_reported_independently():
    """Reported mode and speed are not rewritten."""
    cases = (
        # AUTO speed 0, clean air. 14:46:10.
        (
            "09 01 16 02 dc 03 00 00 0a 00 01 00 00 00 01 00 "
            "00 00 00 00 00 00 00 00 00 00 00 00 a5 00 c0",
            FanMode.AUTO,
            0,
        ),
        # OFF reported speed 50 after set_fan_speed(50). 14:50:25.
        (
            "0b 01 16 02 8f 03 00 00 0a 00 00 00 00 00 01 00 "
            "00 00 00 00 00 00 00 00 00 00 00 00 81 32 c0",
            FanMode.OFF,
            50,
        ),
        # NIGHT. 14:50:48.
        (
            "0b 01 16 02 8d 03 00 00 0a 00 01 00 00 00 01 00 "
            "00 00 00 00 00 00 00 00 00 00 00 00 95 0a c0",
            FanMode.NIGHT,
            10,
        ),
        # TURBO. 14:50:58.
        (
            "0b 01 16 02 8b 03 00 00 09 00 01 00 00 00 01 00 "
            "00 00 00 00 00 00 00 00 00 00 00 00 8d 79 c0",
            FanMode.TURBO,
            121,
        ),
        # ANTI_ALLERGY is 0x85 with flags 0x80. 14:50:04.
        (
            "0b 01 16 02 90 03 00 00 09 00 00 00 00 00 02 00 "
            "00 00 00 00 00 00 00 00 00 00 00 00 85 3c 80",
            FanMode.ANTI_ALLERGY,
            60,
        ),
        # MANUAL reported at speed 0. 14:51:18.
        (
            "0b 01 16 02 88 03 00 00 09 00 01 00 00 00 02 00 "
            "00 00 00 00 00 00 00 00 00 00 00 00 85 00 c0",
            FanMode.MANUAL,
            0,
        ),
        # MANUAL speed 50. 14:51:28.
        (
            "0b 01 16 02 89 03 00 00 0a 00 02 00 00 00 02 00 "
            "00 00 00 00 00 00 00 00 00 00 00 00 85 32 c0",
            FanMode.MANUAL,
            50,
        ),
    )
    for raw_hex, mode, speed in cases:
        state = decode(raw_hex)
        assert state["fan_mode"] == mode
        assert state["fan_speed"] == speed
        assert state["purifier_online"] is True
        assert state["temperature"] is not None
        assert state["humidity"] == 22


def response(fan: int, speed: int, flags: int) -> bytes:
    """Return a state payload with a real climate reading and one fan triple."""
    data = bytearray(58)
    data[27:30] = b"\x0b\x01\x16"  # 26.7 C, 22 %
    data[55] = fan
    data[56] = speed
    data[57] = flags
    return bytes(data)


def script(dev: lifaair, calls: list, body: bytes) -> None:
    """Replace _send with a recorder that returns body."""

    async def send(operation, action=0, action_arg=0):
        calls.append((operation, action, action_arg))
        return body

    dev._send = send


def test_set_fan_mode_sends_one_mode_argument():
    """Each mode command is one SET_FAN_MODE packet."""
    dev = device()
    calls: list = []
    cases = (
        (FanMode.OFF, 1, 0x81, 0x00, 0xC0, FanMode.OFF, 0),
        (FanMode.AUTO, 2, 0xA5, 0x1E, 0xC0, FanMode.AUTO, 30),
        (FanMode.NIGHT, 6, 0x95, 0x0A, 0xC0, FanMode.NIGHT, 10),
        (FanMode.TURBO, 7, 0x8D, 0x79, 0xC0, FanMode.TURBO, 121),
        (FanMode.ANTI_ALLERGY, 11, 0x85, 0x3C, 0x80, FanMode.ANTI_ALLERGY, 60),
    )
    for mode, arg, fan, speed, flags, expect_mode, expect_speed in cases:
        calls.clear()
        script(dev, calls, response(fan, speed, flags))
        state = run(dev.set_fan_mode(mode))
        assert calls == [
            (
                lifaair._Operation.SET_STATE,
                lifaair._Action.SET_FAN_MODE,
                arg,
            )
        ]
        assert state["fan_mode"] == expect_mode
        assert state["fan_speed"] == expect_speed
        assert state["purifier_online"] is True


def test_set_fan_mode_without_a_command_sends_nothing():
    """MANUAL, UNKNOWN and None have no mode argument."""
    dev = device()
    calls: list = []
    script(dev, calls, response(0x85, 50, 0xC0))
    for mode in (FanMode.MANUAL, FanMode.UNKNOWN, None):
        with pytest.raises(ValueError):
            run(dev.set_fan_mode(mode))
    assert calls == []


def test_set_fan_mode_device_error_is_not_retried():
    """Error -5 from the device is StorageError, raised once."""
    dev = device()
    calls: list = []

    async def send(operation, action=0, action_arg=0):
        calls.append((operation, action, action_arg))
        raise StorageError(-5, "The device storage is full")

    dev._send = send
    with pytest.raises(StorageError):
        run(dev.set_fan_mode(FanMode.TURBO))
    assert calls == [
        (
            lifaair._Operation.SET_STATE,
            lifaair._Action.SET_FAN_MODE,
            7,
        )
    ]


def test_set_fan_speed_sends_one_speed_argument():
    """Speed is one SET_FAN_SPEED packet, including 0 and OFF."""
    dev = device()
    calls: list = []
    cases = (
        (70, 0x85, 0x46, 0xC0, FanMode.MANUAL, 70),
        (0, 0x85, 0x00, 0xC0, FanMode.MANUAL, 0),
        (121, 0x85, 0x79, 0xC0, FanMode.MANUAL, 121),
        (50, 0x81, 0x32, 0xC0, FanMode.OFF, 50),
    )
    for speed, fan, raw_speed, flags, mode, expect_speed in cases:
        calls.clear()
        script(dev, calls, response(fan, raw_speed, flags))
        state = run(dev.set_fan_speed(speed))
        assert calls == [
            (
                lifaair._Operation.SET_STATE,
                lifaair._Action.SET_FAN_SPEED,
                speed,
            )
        ]
        assert state["fan_mode"] == mode
        assert state["fan_speed"] == expect_speed
        assert state["purifier_online"] is True


def test_set_fan_speed_rejects_values_that_cannot_be_encoded():
    """Only ints in 0..121 are sent."""
    dev = device()
    calls: list = []
    script(dev, calls, response(0x85, 50, 0xC0))
    for speed in (-1, 122, True, "50"):
        with pytest.raises(ValueError):
            run(dev.set_fan_speed(speed))
    assert calls == []


def test_device_error_minus_5_is_storage_error():
    """A response error of -5 is StorageError, and the command is not resent."""
    dev = device()
    calls: list = []

    async def send_packet(packet_type, packet):
        calls.append((packet_type, bytes(packet)))
        frame = bytearray(0x38)
        frame[0x22:0x24] = (-5 & 0xFFFF).to_bytes(2, "little")
        return bytes(frame)

    dev.send_packet = send_packet
    with pytest.raises(StorageError) as raised:
        run(dev.set_fan_mode(FanMode.TURBO))
    assert raised.value.errno == -5
    assert len(calls) == 1
    assert calls[0][0] == 0x6A


def test_bit0_clear_decodes_as_the_same_mode():
    """0x84, 0xA4 and 0x80 are MANUAL, AUTO and OFF."""
    cases = (
        (0x84, 0x32, 0xC0, FanMode.MANUAL, 50),
        (0xA4, 0x1E, 0xC0, FanMode.AUTO, 30),
        (0x80, 0x00, 0xC0, FanMode.OFF, 0),
    )
    dev = device()
    for fan, speed, flags, mode, expect_speed in cases:
        state = dev._decode_state(response(fan, speed, flags))
        assert state["fan_mode"] == mode
        assert state["fan_speed"] == expect_speed
        assert state["purifier_online"] is True
        assert state["temperature"] == 26.7
        assert state["humidity"] == 22
