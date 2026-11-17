"""Decode LIFAair LM05 frames captured from a real device.

Bytes are payload offsets 27..57 from firmware 10026, polled on 2026-11-17.
"""

import unittest

from broadlink.purifier import FanMode, lifaair


def payload(raw_hex):
    """Return a GET_STATE body with captured bytes placed at offset 27."""
    data = bytearray(58)
    data[27:] = bytes.fromhex(raw_hex)
    return bytes(data)


def device():
    """Return a lifaair instance that does not touch the network."""
    return lifaair(("127.0.0.1", 80), b"\x00" * 6, 0x4EC2)


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


class LifaairDecodeTest(unittest.TestCase):
    """State decoded from captured frames."""

    def decode(self, raw_hex):
        """Decode one captured frame."""
        return device()._decode_state(payload(raw_hex))

    def test_offline_purifier_climate_and_mode_are_none(self):
        """Unplugged and unpaired both report 0x01 with zero climate bytes."""
        for raw_hex, co2 in ((OFFLINE_BOOT, 400), (OFFLINE_UNPAIRED, 939)):
            state = self.decode(raw_hex)
            self.assertIsNone(state["temperature"])
            self.assertIsNone(state["humidity"])
            self.assertIsNone(state["fan_mode"])
            self.assertEqual(state["fan_speed"], 0)
            self.assertEqual(state["co2"], co2)
            self.assertEqual(state["formaldehyde"], 0.0)

    def test_reconnect_placeholder_zeros_are_none(self):
        """Online fan_state with 00 00 00 climate is not 0.0 deg C / 0 %."""
        for raw_hex, co2, speed in RECONNECT_ZEROS:
            state = self.decode(raw_hex)
            self.assertIsNone(state["temperature"])
            self.assertIsNone(state["humidity"])
            self.assertEqual(state["fan_mode"], FanMode.AUTO)
            self.assertEqual(state["fan_speed"], speed)
            self.assertEqual(state["co2"], co2)
            self.assertEqual(state["formaldehyde"], 0.0)

    def test_real_climate_after_placeholder(self):
        """The frame after the placeholder carries the real reading."""
        state = self.decode(AFTER_RECONNECT)
        self.assertEqual(state["temperature"], 26.4)
        self.assertEqual(state["humidity"], 22)
        self.assertEqual(state["co2"], 958)
        self.assertEqual(state["fan_mode"], FanMode.AUTO)
        self.assertEqual(state["fan_speed"], 43)

    def test_co2_warmup_placeholder_is_kept(self):
        """400 ppm has no separate marker, so it is returned as 400."""
        state = self.decode(CO2_WARMUP)
        self.assertEqual(state["co2"], 400)
        self.assertEqual(state["temperature"], 26.6)
        self.assertEqual(state["humidity"], 22)
        self.assertEqual(state["tvoc"], 90)
        self.assertEqual(state["pm1"], 0)
        self.assertEqual(state["pm2_5"], 2)
        self.assertEqual(state["pm10"], 3)
        self.assertEqual(state["fan_mode"], FanMode.AUTO)
        self.assertEqual(state["fan_speed"], 20)

    def test_spray_frame_keeps_pm_order_and_zero_formaldehyde(self):
        """PM offsets stay as verified, and a zero HCHO reading stays 0.0."""
        state = self.decode(SPRAY)
        self.assertEqual(state["temperature"], 26.6)
        self.assertEqual(state["humidity"], 22)
        self.assertEqual(state["co2"], 927)
        self.assertEqual(state["formaldehyde"], 0.0)
        self.assertEqual(state["tvoc"], 90)
        self.assertEqual(state["pm1"], 12)
        self.assertEqual(state["pm2_5"], 39)
        self.assertEqual(state["pm10"], 128)
        self.assertEqual(state["fan_mode"], FanMode.AUTO)
        self.assertEqual(state["fan_speed"], 13)

    def test_fan_mode_and_speed_are_reported_independently(self):
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
            state = self.decode(raw_hex)
            self.assertEqual(state["fan_mode"], mode)
            self.assertEqual(state["fan_speed"], speed)
            self.assertIsNotNone(state["temperature"])
            self.assertEqual(state["humidity"], 22)


if __name__ == "__main__":
    unittest.main()
