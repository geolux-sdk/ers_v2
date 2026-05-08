from __future__ import annotations

"""Parsing helpers for 64-byte measurement blocks."""

import csv
import os
import struct
from dataclasses import dataclass
from os import PathLike
from typing import Iterator, Union

ADS131A_SCALE_FACTOR = 4.0 / 0x800000
ISOLATED_AMP_GAIN = 3.0
VOLTAGE_GAIN = 50.0
CURRENT_GAIN = 2.0

LITTLE_ENDIAN = "<"
ADC_ROWS = 3
ADC_COLS = 4
ADC_SAMPLE_COUNT = ADC_ROWS * ADC_COLS
ADC_SENSOR_COUNT = 10
MEASURE_BLOCK_FORMAT = f"{LITTLE_ENDIAN}hH{ADC_SAMPLE_COUNT}iI2I"
MEASURE_BLOCK_SIZE = struct.calcsize(MEASURE_BLOCK_FORMAT)
LEGACY_PAYLOAD_FORMAT = f"{LITTLE_ENDIAN}Hh{ADC_SAMPLE_COUNT}i"
LEGACY_PAYLOAD_SIZE = struct.calcsize(LEGACY_PAYLOAD_FORMAT)
LEGACY_CSV_HEADER = (
    ["Time", "FB"]
    + [f"ADC{index}" for index in range(ADC_SENSOR_COUNT)]
    + ["Voltage", "Current"]
)

if MEASURE_BLOCK_SIZE != 64:
    raise RuntimeError(f"Unexpected measure block size: {MEASURE_BLOCK_SIZE}")

if LEGACY_PAYLOAD_SIZE != 52:
    raise RuntimeError(f"Unexpected legacy payload size: {LEGACY_PAYLOAD_SIZE}")


@dataclass(frozen=True)
class MeasureData:
    """Parsed representation of measure_data_t."""

    bridge_state: int
    reserved: int
    adc_data: list[list[int]]
    seq_num: int
    padding: tuple[int, int]
    raw: bytes


@dataclass(frozen=True)
class LegacyMeasureData:
    """Parsed representation of the saved legacy ADC payload."""

    time: int
    bridge_state: int
    values: list[int]
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


def parse_legacy_payload_block(raw: bytes) -> LegacyMeasureData:
    """Parse a single 52-byte legacy ADC payload block."""
    if len(raw) != LEGACY_PAYLOAD_SIZE:
        raise ValueError(
            f"Legacy payload block must be exactly {LEGACY_PAYLOAD_SIZE} bytes, "
            f"got {len(raw)}"
        )

    unpacked = struct.unpack(LEGACY_PAYLOAD_FORMAT, raw)
    return LegacyMeasureData(
        time=unpacked[0],
        bridge_state=unpacked[1],
        values=list(unpacked[2 : 2 + ADC_SAMPLE_COUNT]),
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


def parse_legacy_payload_stream(raw: bytes) -> list[LegacyMeasureData]:
    """Parse a byte stream containing an integral number of 52-byte payloads."""
    if len(raw) % LEGACY_PAYLOAD_SIZE != 0:
        raise ValueError(
            "Legacy payload stream length must be a multiple of "
            f"{LEGACY_PAYLOAD_SIZE}, got {len(raw)}"
        )

    return [
        parse_legacy_payload_block(raw[offset : offset + LEGACY_PAYLOAD_SIZE])
        for offset in range(0, len(raw), LEGACY_PAYLOAD_SIZE)
    ]


def flatten_adc_data(block: MeasureData) -> list[int]:
    """Return one block's 3x4 ADC data as a 12-value row."""
    adc_flat = [value for row in block.adc_data for value in row]

    if len(adc_flat) != ADC_SAMPLE_COUNT:
        raise ValueError(
            f"ADC data must contain {ADC_SAMPLE_COUNT} values, got {len(adc_flat)}"
        )

    return adc_flat


def _median3(first: int, second: int, third: int) -> int:
    values = [first, second, third]
    values.sort()
    return values[1]


def median_correction(adc_rows: list[list[int]]) -> list[list[int]]:
    """
    Apply the legacy 3-sample median correction to each ADC channel.

    This matches scipy.signal.medfilt(..., kernel_size=3) with zero padding.
    """
    if not adc_rows:
        return []

    corrected = [
        [0] * ADC_SAMPLE_COUNT
        for _ in adc_rows
    ]

    for channel_index in range(ADC_SAMPLE_COUNT):
        for sample_index, row in enumerate(adc_rows):
            prev_value = (
                adc_rows[sample_index - 1][channel_index]
                if sample_index > 0
                else 0
            )
            current_value = row[channel_index]
            next_value = (
                adc_rows[sample_index + 1][channel_index]
                if sample_index + 1 < len(adc_rows)
                else 0
            )
            corrected[sample_index][channel_index] = _median3(
                prev_value,
                current_value,
                next_value,
            )

    return corrected


def serialize_legacy_measure_block(block: MeasureData, adc_flat: list[int]) -> bytes:
    """Serialize one block as legacy time/fb/values bytes."""
    if len(adc_flat) != ADC_SAMPLE_COUNT:
        raise ValueError(
            f"ADC data must contain {ADC_SAMPLE_COUNT} values, got {len(adc_flat)}"
        )

    return struct.pack(
        LEGACY_PAYLOAD_FORMAT,
        block.seq_num & 0xFFFF,
        block.bridge_state,
        *adc_flat,
    )


def convert_measure_stream_to_legacy_payload(raw: bytes) -> bytes:
    """
    Return legacy-compatible measurement bytes.

    Output block format:
        time: seq_num low 16 bits
        fb: bridge_state
        values: 12 median-corrected int32 ADC values
    """
    blocks = parse_measure_stream(raw)
    corrected_adc_rows = median_correction([
        flatten_adc_data(block)
        for block in blocks
    ])

    return b"".join(
        serialize_legacy_measure_block(block, corrected_adc_rows[index])
        for index, block in enumerate(blocks)
    )


def scale_legacy_payload_values(values: list[int]) -> list[float]:
    """Scale ADC payload values using the legacy realtime-save conversion."""
    if len(values) != ADC_SAMPLE_COUNT:
        raise ValueError(
            f"ADC values must contain {ADC_SAMPLE_COUNT} values, got {len(values)}"
        )

    scaled = [
        (ISOLATED_AMP_GAIN * ADS131A_SCALE_FACTOR) * value
        for value in values
    ]
    scaled[ADC_SENSOR_COUNT] = scaled[ADC_SENSOR_COUNT] * VOLTAGE_GAIN
    scaled[ADC_SENSOR_COUNT + 1] = (
        scaled[ADC_SENSOR_COUNT + 1] * CURRENT_GAIN * 1000
    )

    return scaled


def legacy_payload_to_csv_rows(raw: bytes) -> Iterator[list[Union[int, float]]]:
    """Yield CSV rows for converted 52-byte ADC payload bytes."""
    for block in parse_legacy_payload_stream(raw):
        yield [
            block.time,
            block.bridge_state,
            *scale_legacy_payload_values(block.values),
        ]


def write_legacy_payload_csv(raw: bytes, path: Union[str, PathLike]) -> int:
    """Write converted ADC payload bytes to CSV and return the written row count."""
    output_dir = os.path.dirname(os.path.abspath(path))
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    row_count = 0
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(LEGACY_CSV_HEADER)

        for row in legacy_payload_to_csv_rows(raw):
            writer.writerow(row)
            row_count += 1

    return row_count


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
