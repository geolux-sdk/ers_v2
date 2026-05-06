from __future__ import annotations

"""
ERS_ADC_Protocol.py

Binary protocol definitions and command builders for the ERS ADC device.

Python compatibility:
    - Raspberry Pi Python 3.9.2 compatible
"""

import struct
from enum import IntEnum
from typing import Union


# -----------------------------------------------------------------------------
# Basic protocol constants
# -----------------------------------------------------------------------------

LITTLE_ENDIAN = "<"

COMMAND_SETUP = 0
COMMAND_QUERY_MEASUREMENT_STATE = 1
COMMAND_START_MEASUREMENT = 2
COMMAND_ABORT_MEASUREMENT = 3
COMMAND_START_TRANSMISSION = 4
COMMAND_ABORT_TRANSMISSION = 5
COMMAND_SOFTWARE_RESET = 10


# -----------------------------------------------------------------------------
# Struct formats
# -----------------------------------------------------------------------------
#
# Setup command:
#   command:              uint8
#   pattern:              uint8
#   reserved:             uint16
#   on_duration_samples:  uint32
#   off_duration_samples: uint32
#   total_cycles:         uint32
#
# Total: 16 bytes
#
# Start transmission range command:
#   command:      uint8
#   padding:      3 bytes
#   starting_seq: uint32
#   count:        uint32
#
# Total: 12 bytes
# -----------------------------------------------------------------------------

BYTE_FORMAT = "{}B".format(LITTLE_ENDIAN)
UINT32_FORMAT = "{}I".format(LITTLE_ENDIAN)
SETUP_COMMAND_FORMAT = "{}BBHIII".format(LITTLE_ENDIAN)
START_TRANSMISSION_RANGE_FORMAT = "{}B3xII".format(LITTLE_ENDIAN)

SETUP_COMMAND_SIZE = struct.calcsize(SETUP_COMMAND_FORMAT)
START_TRANSMISSION_RANGE_SIZE = struct.calcsize(START_TRANSMISSION_RANGE_FORMAT)

if SETUP_COMMAND_SIZE != 16:
    raise RuntimeError("Unexpected setup command size: {}".format(SETUP_COMMAND_SIZE))

if START_TRANSMISSION_RANGE_SIZE != 12:
    raise RuntimeError(
        "Unexpected ranged transmission command size: {}".format(
            START_TRANSMISSION_RANGE_SIZE
        )
    )


# -----------------------------------------------------------------------------
# Response code
# -----------------------------------------------------------------------------

class ResponseCode(IntEnum):
    """
    Device response codes.

    Values are defined by the ADC firmware protocol.
    """

    OK = 0
    BUSY = 1
    TIMEOUT = 2
    FAIL = 4
    NOT_READY = 8


RESPONSE_CODE_VALUES = frozenset(code.value for code in ResponseCode)


def is_response_code_value(value: int) -> bool:
    """
    Return True if value is a known device response code.
    """
    return value in RESPONSE_CODE_VALUES


def describe_response(code: ResponseCode) -> str:
    """
    Return a human-readable response description.
    """
    descriptions = {
        ResponseCode.OK: "operation succeeded",
        ResponseCode.BUSY: "device is busy",
        ResponseCode.TIMEOUT: "device timed out",
        ResponseCode.FAIL: "device reported failure",
        ResponseCode.NOT_READY: "device is not ready",
    }
    return descriptions[code]


# -----------------------------------------------------------------------------
# Basic pack / unpack helpers
# -----------------------------------------------------------------------------

def pack_u8(value: int) -> bytes:
    """
    Pack an unsigned 8-bit integer.
    """
    _validate_uint8(value, name="value")
    return struct.pack(BYTE_FORMAT, value)


def unpack_u8(data: bytes) -> int:
    """
    Unpack a single unsigned 8-bit integer.
    """
    if len(data) != 1:
        raise ValueError("Expected exactly 1 byte, got {}".format(len(data)))

    return struct.unpack(BYTE_FORMAT, data)[0]


def pack_u32(value: int) -> bytes:
    """
    Pack an unsigned 32-bit integer.
    """
    _validate_uint32(value, name="value")
    return struct.pack(UINT32_FORMAT, value)


def unpack_u32(data: bytes) -> int:
    """
    Unpack a single unsigned 32-bit integer.
    """
    if len(data) != 4:
        raise ValueError("Expected exactly 4 bytes, got {}".format(len(data)))

    return struct.unpack(UINT32_FORMAT, data)[0]


# -----------------------------------------------------------------------------
# Response parsing
# -----------------------------------------------------------------------------

def parse_response_code(
    value: Union[int, bytes, bytearray, memoryview]
) -> ResponseCode:
    """
    Convert a raw response byte or integer into ResponseCode.

    Parameters
    ----------
    value:
        int or 1-byte object.

    Returns
    -------
    ResponseCode
    """
    if isinstance(value, int):
        raw_value = value
    else:
        raw_value = unpack_u8(bytes(value))

    if not is_response_code_value(raw_value):
        raise ValueError("Unknown response code: {}".format(raw_value))

    return ResponseCode(raw_value)


def ensure_ok_response(value: Union[int, bytes, bytearray, memoryview]) -> None:
    """
    Raise RuntimeError if the response is not OK.

    This function is useful after sending a command to the device.
    """
    code = parse_response_code(value)

    if code != ResponseCode.OK:
        raise RuntimeError(
            "Device response error: {} - {}".format(
                code.name,
                describe_response(code),
            )
        )


# -----------------------------------------------------------------------------
# Command builders
# -----------------------------------------------------------------------------

def build_setup_command(
    pattern: int,
    on_duration_samples: int,
    off_duration_samples: int,
    total_cycles: int,
) -> bytes:
    """
    Build Command 0: Setup.

    Packet size: 16 bytes
    """
    _validate_uint8(pattern, name="pattern")
    _validate_uint32(on_duration_samples, name="on_duration_samples")
    _validate_uint32(off_duration_samples, name="off_duration_samples")
    _validate_uint32(total_cycles, name="total_cycles")

    return struct.pack(
        SETUP_COMMAND_FORMAT,
        COMMAND_SETUP,
        pattern,
        0,
        on_duration_samples,
        off_duration_samples,
        total_cycles,
    )


def build_query_state_command() -> bytes:
    """
    Build Command 1: Query Measurement State.
    """
    return pack_u8(COMMAND_QUERY_MEASUREMENT_STATE)


def build_start_measurement_command() -> bytes:
    """
    Build Command 2: Start Measurement.
    """
    return pack_u8(COMMAND_START_MEASUREMENT)


def build_abort_measurement_command() -> bytes:
    """
    Build Command 3: Abort Measurement.
    """
    return pack_u8(COMMAND_ABORT_MEASUREMENT)


def build_start_transmission_all_command() -> bytes:
    """
    Build Command 4: Start Transmission for all stored blocks.
    """
    return pack_u8(COMMAND_START_TRANSMISSION)


def build_start_transmission_range_command(starting_seq: int, count: int) -> bytes:
    """
    Build Command 4: Start Transmission for a sequence range.

    Packet size: 12 bytes

    count must be greater than 0.
    """
    _validate_uint32(starting_seq, name="starting_seq")
    _validate_positive_uint32(count, name="count")

    return struct.pack(
        START_TRANSMISSION_RANGE_FORMAT,
        COMMAND_START_TRANSMISSION,
        starting_seq,
        count,
    )


def build_abort_transmission_command() -> bytes:
    """
    Build Command 5: Abort Transmission.
    """
    return pack_u8(COMMAND_ABORT_TRANSMISSION)


def build_software_reset_command() -> bytes:
    """
    Build Command 10: Software Reset.
    """
    return pack_u8(COMMAND_SOFTWARE_RESET)


# -----------------------------------------------------------------------------
# Validation helpers
# -----------------------------------------------------------------------------

def _validate_uint8(value: int, *, name: str) -> None:
    if not 0 <= value <= 0xFF:
        raise ValueError("{} must fit in uint8: {}".format(name, value))


def _validate_uint32(value: int, *, name: str) -> None:
    if not 0 <= value <= 0xFFFFFFFF:
        raise ValueError("{} must fit in uint32: {}".format(name, value))


def _validate_positive_uint32(value: int, *, name: str) -> None:
    if not 1 <= value <= 0xFFFFFFFF:
        raise ValueError("{} must be 1..0xFFFFFFFF: {}".format(name, value))


# -----------------------------------------------------------------------------
# Self test
# -----------------------------------------------------------------------------

def _self_test() -> None:
    """
    Simple local test.

    Run:
        python ERS_ADC_Protocol.py
    """
    setup_packet = build_setup_command(
        pattern=0,
        on_duration_samples=480,
        off_duration_samples=48,
        total_cycles=1,
    )

    range_packet = build_start_transmission_range_command(
        starting_seq=0,
        count=10,
    )

    assert len(setup_packet) == SETUP_COMMAND_SIZE
    assert len(range_packet) == START_TRANSMISSION_RANGE_SIZE

    assert parse_response_code(b"\x00") == ResponseCode.OK
    assert parse_response_code(b"\x01") == ResponseCode.BUSY
    assert parse_response_code(b"\x02") == ResponseCode.TIMEOUT
    assert parse_response_code(b"\x04") == ResponseCode.FAIL
    assert parse_response_code(b"\x08") == ResponseCode.NOT_READY

    ensure_ok_response(b"\x00")

    print("ERS_ADC_Protocol self-test OK")
    print("SETUP_COMMAND_SIZE =", SETUP_COMMAND_SIZE)
    print("START_TRANSMISSION_RANGE_SIZE =", START_TRANSMISSION_RANGE_SIZE)
    print("setup_packet =", setup_packet.hex(" "))
    print("range_packet =", range_packet.hex(" "))


if __name__ == "__main__":
    _self_test()