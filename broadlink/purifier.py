"""Support for air purifiers."""

import enum

from . import exceptions as e
from .device import Device


@enum.unique
class FanMode(enum.IntEnum):
    """Represents mode of the fan."""

    OFF = 0
    AUTO = 1
    NIGHT = 2
    TURBO = 3
    ANTI_ALLERGY = 4
    MANUAL = 5

    UNKNOWN = -1


class lifaair(Device):
    """Controls a Broadlink-based LIFAair air purifier / monitor."""

    TYPE = "LIFAAIR"

    # Fan state byte (payload[55]). 0x01 is the purifier offline or unpaired.
    # On this unit (fw 10026) that is flags 0xC0 and temperature/humidity
    # bytes 00 00 00. Another LM05 instead kept raw 25 and 45 (decoded
    # 2.5 deg C / 45 %) with no purifier paired (mjg59/python-broadlink#807);
    # that is not special-cased. A restart or re-pair returns the purifier
    # itself to AUTO.
    FAN_STATE_TO_MODE = {
        0x81: FanMode.OFF,
        0xA5: FanMode.AUTO,
        0x95: FanMode.NIGHT,
        0x8D: FanMode.TURBO,
        0x85: FanMode.MANUAL,
        0x01: None,  # main unit offline / unpaired
    }

    @enum.unique
    class _Operation(enum.IntEnum):
        SET_STATE = 1
        GET_STATE = 2

    @enum.unique
    class _Action(enum.IntEnum):
        SET_FAN_SPEED = 1
        SET_FAN_MODE = 2

    FAN_MODE_TO_ACTION_ARG = {
        FanMode.OFF: 1,
        FanMode.AUTO: 2,
        FanMode.NIGHT: 6,
        FanMode.TURBO: 7,
        FanMode.ANTI_ALLERGY: 11,
    }

    def set_fan_mode(self, fan_mode: FanMode) -> dict:
        """Set mode of the fan. Returns updated state."""
        if fan_mode == FanMode.MANUAL:
            return self.set_fan_speed(50)

        action_arg = self.FAN_MODE_TO_ACTION_ARG.get(fan_mode)
        if action_arg is not None:
            data = self._send(
                self._Operation.SET_STATE, self._Action.SET_FAN_MODE, action_arg
            )
            return self._decode_state(data)

        return self.get_state()

    def set_fan_speed(self, fan_speed: int) -> dict:
        """Set fan speed (0-121). Returns updated state.

        The device switches to MANUAL, except while OFF: the speed is
        accepted and the fan spins, but the reported mode stays OFF.
        """
        data = self._send(
            self._Operation.SET_STATE, self._Action.SET_FAN_SPEED, fan_speed
        )
        return self._decode_state(data)

    def get_state(self) -> dict:
        """Return the current state of the purifier as a dict.

        CO2, TVOC, formaldehyde and PM come from the station. Temperature,
        humidity and fan mode come from the purifier. Those three are None
        when the purifier is offline. Temperature and humidity are also None
        when bytes 27-29 are placeholder zeros (the first poll after it
        reconnects, while fan_state is already online). Fan mode and fan
        speed are otherwise the values the device reports, including AUTO at
        speed 0 and OFF with a non-zero speed. Fan speed is always an int.

        Format::

            {
                "temperature": 24.5,           # float, deg C, or None
                "humidity": 41,                # int, %, or None
                "co2": 425,                    # int, ppm
                "formaldehyde": 0.03,          # float, mg/m3
                "tvoc": 150,                   # int, ug/m3
                "pm1": 5,                      # int, ug/m3
                "pm2_5": 7,                    # int, ug/m3
                "pm10": 9,                     # int, ug/m3
                "fan_mode": FanMode.AUTO,      # FanMode, or None
                "fan_speed": 50,               # int, 0-121
            }
        """
        data = self._send(self._Operation.GET_STATE)
        return self._decode_state(data)

    def _decode_state(self, data: bytes) -> dict:
        raw = self._decode_state_raw(data)
        fan_mode = self._decode_fan_mode(raw["fan_state"], raw["fan_flags"])
        # Bytes 27-29 are 00 00 00 when the purifier has no climate reading:
        # it is offline, or this is the first poll after it reconnects
        # (fan_state is already online). Not a real 0.0 deg C / 0 %.
        if fan_mode is None or (raw["temperature"] == 0 and raw["humidity"] == 0):
            temperature = None
            humidity = None
        else:
            temperature = raw["temperature"] / 10.0
            humidity = raw["humidity"]
        # Mode and speed are independent. Standby is AUTO with speed 0.
        # OFF may have speed > 0 (the fan still spins). MANUAL at speed 0 stops.
        return {
            "temperature": temperature,
            "humidity": humidity,
            "co2": raw["co2"],
            "formaldehyde": raw["formaldehyde"] / 100.0,
            "tvoc": raw["tvoc"] * 10,
            "pm1": raw["pm1"],
            "pm2_5": raw["pm2_5"],
            "pm10": raw["pm10"],
            "fan_mode": fan_mode,
            "fan_speed": raw["fan_speed"],
        }

    def _decode_state_raw(self, data: bytes) -> dict:
        # PM field order verified against LAM01/LM05 display (PM1 <= PM2.5 <= PM10).
        # Byte 30 is always 0x02. Meaning unknown; not decoded.
        # CO2 is exactly 400 for ~10 s after the station boots. No other byte
        # marks that warm-up, and 400 ppm can be real, so the value is kept.
        return {
            "temperature": data[27] + 256 * data[28],
            "humidity": data[29],
            "co2": data[31] + 256 * data[32],
            "formaldehyde": data[33] + 256 * data[34],
            "tvoc": data[35] + 256 * data[36],
            "pm1": data[37] + 256 * data[38],
            "pm2_5": data[39] + 256 * data[40],
            "pm10": data[41] + 256 * data[42],
            "fan_state": data[55],
            "fan_speed": data[56],
            "fan_flags": data[57],
        }

    def _decode_fan_mode(self, fan_state: int, fan_flags: int):
        # 0x01 is offline, whatever the flags are. Anti-allergy is 0x85 with
        # bit 0x40 clear. This unit sends 0x01 with flags 0xC0.
        if fan_state == 0x01:
            return None
        if fan_flags & 0x40 == 0:
            return FanMode.ANTI_ALLERGY
        return self.FAN_STATE_TO_MODE.get(fan_state, FanMode.UNKNOWN)

    def _send(self, operation: int, action: int = 0, action_arg: int = 0) -> bytes:
        """Send a command to the device."""
        packet = bytearray(26)
        packet[0x02] = 0xA5
        packet[0x03] = 0xA5
        packet[0x04] = 0x5A
        packet[0x05] = 0x5A
        packet[0x08] = operation & 0xFF
        packet[0x0A] = 0x0C
        packet[0x0E] = action & 0xFF
        packet[0x0F] = action_arg & 0xFF

        checksum = sum(packet, 0xBEAF) & 0xFFFF
        packet[0x06] = checksum & 0xFF
        packet[0x07] = checksum >> 8

        packet_len = len(packet) - 2
        packet[0x00] = packet_len & 0xFF
        packet[0x01] = packet_len >> 8

        resp = self.send_packet(0x6A, packet)
        # Seen returning -5 sporadically during power-cycling and pairing; cause unknown.
        e.check_error(resp[0x22:0x24])
        return self.decrypt(resp[0x38:])
