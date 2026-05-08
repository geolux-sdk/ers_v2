from __future__ import annotations

"""Parsing helpers for 64-byte measurement blocks."""

import struct
from dataclasses import dataclass
from os import PathLike
from typing import Iterator, Union

LITTLE_ENDIAN = "<"
ADC_ROWS = 3
ADC_COLS = 4
ADC_SAMPLE_COUNT = ADC_ROWS * ADC_COLS
MEASURE_BLOCK_FORMAT = f"{LITTLE_ENDIAN}hH12I3I"
MEASURE_BLOCK_SIZE = struct.calcsize(MEASURE_BLOCK_FORMAT)
MEASURE_PAYLOAD_FORMAT = f"{LITTLE_ENDIAN}hH{ADC_SAMPLE_COUNT}II"
MEASURE_PAYLOAD_SIZE = struct.calcsize(MEASURE_PAYLOAD_FORMAT)

if MEASURE_BLOCK_SIZE != 64:
    raise RuntimeError(f"Unexpected measure block size: {MEASURE_BLOCK_SIZE}")

if MEASURE_PAYLOAD_SIZE != 56:
    raise RuntimeError(f"Unexpected measure payload size: {MEASURE_PAYLOAD_SIZE}")


@dataclass(frozen=True)
class MeasureData:
    """Parsed representation of measure_data_t."""

    bridge_state: int
    reserved: int
    adc_data: list[list[int]]
    seq_num: int
    padding: tuple[int, int]
    raw: bytes


def parse_measure_block(raw: bytes) -> MeasureData:
    """Parse a single 64-byte measurement block."""
    if len(raw) != MEASURE_BLOCK_SIZE:
        raise ValueError(
            f"Measure block must be exactly {MEASURE_BLOCK_SIZE} bytes, got {len(raw)}"
        )

    unpacked = struct.unpack(MEASURE_BLOCK_FORMAT, raw)
    bridge_state = unpacked[0]
    reserved = unpacked[1]
    adc_flat = list(unpacked[2 : 2 + ADC_SAMPLE_COUNT])
    seq_num = unpacked[2 + ADC_SAMPLE_COUNT]
    padding = (
        unpacked[3 + ADC_SAMPLE_COUNT],
        unpacked[4 + ADC_SAMPLE_COUNT],
    )

    adc_data = [
        adc_flat[row_index * ADC_COLS : (row_index + 1) * ADC_COLS]
        for row_index in range(ADC_ROWS)
    ]

    return MeasureData(
        bridge_state=bridge_state,
        reserved=reserved,
        adc_data=adc_data,
        seq_num=seq_num,
        padding=padding,
        raw=bytes(raw),
    )


def parse_measure_stream(raw: bytes) -> list[MeasureData]:
    """Parse a byte stream containing an integral number of 64-byte blocks."""
    if len(raw) % MEASURE_BLOCK_SIZE != 0:
        raise ValueError(
            "Measurement stream length must be a multiple of "
            f"{MEASURE_BLOCK_SIZE}, got {len(raw)}"
        )

    return [
        parse_measure_block(raw[offset : offset + MEASURE_BLOCK_SIZE])
        for offset in range(0, len(raw), MEASURE_BLOCK_SIZE)
    ]


def serialize_measure_block_without_padding(block: MeasureData) -> bytes:
    """Serialize one measurement block without the trailing padding fields."""
    adc_flat = [
        value
        for row in block.adc_data
        for value in row
    ]

    if len(adc_flat) != ADC_SAMPLE_COUNT:
        raise ValueError(
            f"ADC data must contain {ADC_SAMPLE_COUNT} values, got {len(adc_flat)}"
        )

    return struct.pack(
        MEASURE_PAYLOAD_FORMAT,
        block.bridge_state,
        block.reserved,
        *adc_flat,
        block.seq_num,
    )


def strip_measure_stream_padding(raw: bytes) -> bytes:
    """Return measurement stream bytes with each block's padding removed."""
    return b"".join(
        serialize_measure_block_without_padding(block)
        for block in parse_measure_stream(raw)
    )


def iter_measure_blocks_from_file(path: Union[str, PathLike]) -> Iterator[MeasureData]:
    """Yield parsed measurement blocks from a binary file."""
    with open(path, "rb") as handle:
        while True:
            raw = handle.read(MEASURE_BLOCK_SIZE)
            if not raw:
                return
            if len(raw) != MEASURE_BLOCK_SIZE:
                raise ValueError(
                    "Binary file ended with a partial measurement block: "
                    f"{len(raw)} bytes"
                )
            yield parse_measure_block(raw)
