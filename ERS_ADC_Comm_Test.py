from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from typing import Any, Dict, Optional, Tuple

from ERS_ADC_Control import (
    BYTES_PER_SAMPLE,
    DEFAULT_BAUDRATE,
    DEFAULT_BUSY_POLL_INTERVAL_SEC,
    DEFAULT_BUSY_TIMEOUT_SEC,
    DEFAULT_PORT,
    DEFAULT_RANGE_READ_CHUNK_SAMPLES,
    DEFAULT_RANGE_READ_RETRY_ATTEMPTS,
    DEFAULT_READ_CHUNK_SAMPLES,
    DEFAULT_READ_EMPTY_RETRY_LIMIT,
    DEFAULT_TIMEOUT,
    DEFAULT_WRITE_TIMEOUT,
    adc_controller,
)
from ERS_ADC_Protocol import ResponseCode, describe_response


def setup_logger(level_name: str) -> logging.Logger:
    logger = logging.getLogger("ERS_ADC_Comm_Test")
    logger.handlers.clear()
    logger.propagate = False

    level = getattr(logging, level_name.upper(), logging.INFO)
    logger.setLevel(level)

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    logger.addHandler(handler)
    return logger


def load_adc_settings(path: Optional[str]) -> Dict[str, Any]:
    if not path:
        return {}

    if not os.path.exists(path):
        return {}

    with open(path, "r", encoding="utf-8") as file:
        data = json.load(file)

    adc_settings = dict(data.get("adc", {}))
    if "port" not in adc_settings and "comport" in adc_settings:
        adc_settings["port"] = adc_settings["comport"]
    return adc_settings


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "ADC serial communication test. Repeats CONNECT, SETUP, START, "
            "STATUS polling, and optionally ranged ADC data retrieval."
        )
    )

    parser.add_argument(
        "--settings",
        default="settings.json",
        help="Optional settings JSON path used for ADC defaults. Default: %(default)s",
    )
    parser.add_argument("--port", default=None, help="ADC serial port")
    parser.add_argument("--baudrate", type=int, default=None, help="ADC serial baudrate")
    parser.add_argument("--timeout", type=float, default=None, help="Read timeout seconds")
    parser.add_argument(
        "--write-timeout",
        type=float,
        default=None,
        help="Write timeout seconds",
    )

    parser.add_argument("--loops", type=int, default=10, help="Test loop count")
    parser.add_argument(
        "--reopen-each-loop",
        action="store_true",
        default=True,
        help="Close and reopen the serial port for every loop. This is the default.",
    )
    parser.add_argument(
        "--single-connection",
        action="store_false",
        dest="reopen_each_loop",
        help="Open the serial port once and reuse it for all loops.",
    )
    parser.add_argument(
        "--loop-delay",
        type=float,
        default=0.5,
        help="Delay between loops in seconds",
    )
    parser.add_argument(
        "--stop-on-error",
        action="store_true",
        help="Stop immediately after the first failed loop",
    )

    parser.add_argument("--pattern", type=int, default=0, help="SETUP pattern")
    parser.add_argument("--on-samples", type=int, default=480, help="SETUP ON samples")
    parser.add_argument("--off-samples", type=int, default=48, help="SETUP OFF samples")
    parser.add_argument("--cycles", type=int, default=1, help="SETUP cycle count")
    parser.add_argument(
        "--read-data",
        action="store_true",
        help="Read ADC data with START_TRANSMISSION_RANGE after STATUS OK",
    )
    parser.add_argument(
        "--compare-all-range",
        action="store_true",
        help="Read the same measurement with ALL and RANGE, then compare raw bytes",
    )
    parser.add_argument(
        "--force-close-range-at-sample",
        type=int,
        default=None,
        help=(
            "Fault injection: close the serial port after sending the RANGE "
            "chunk that contains this sample index"
        ),
    )
    parser.add_argument(
        "--output",
        default="test.dat",
        help="RANGE output binary file path. Default: %(default)s",
    )
    parser.add_argument(
        "--csv-output",
        default="test.csv",
        help="RANGE output CSV file path. Default: %(default)s",
    )
    parser.add_argument(
        "--all-output",
        default="test_all.dat",
        help="ALL output binary file path used with --compare-all-range. Default: %(default)s",
    )
    parser.add_argument(
        "--all-csv-output",
        default="test_all.csv",
        help="ALL output CSV file path used with --compare-all-range. Default: %(default)s",
    )
    parser.add_argument(
        "--read-chunk-samples",
        type=int,
        default=DEFAULT_READ_CHUNK_SAMPLES,
        help="Serial read chunk size in samples for ALL mode",
    )
    parser.add_argument(
        "--range-chunk-samples",
        type=int,
        default=DEFAULT_RANGE_READ_CHUNK_SAMPLES,
        help="START_TRANSMISSION_RANGE chunk size in samples",
    )
    parser.add_argument(
        "--range-retry-attempts",
        type=int,
        default=DEFAULT_RANGE_READ_RETRY_ATTEMPTS,
        help="Retry attempts per ranged data chunk",
    )
    parser.add_argument(
        "--read-empty-retry-limit",
        type=int,
        default=DEFAULT_READ_EMPTY_RETRY_LIMIT,
        help="Consecutive empty serial reads allowed before a chunk attempt fails",
    )
    parser.add_argument(
        "--setup-retries",
        type=int,
        default=1,
        help="SETUP retry attempts per loop",
    )
    parser.add_argument(
        "--setup-retry-delay",
        type=float,
        default=0.2,
        help="SETUP retry delay seconds",
    )

    parser.add_argument(
        "--status-timeout",
        type=float,
        default=DEFAULT_BUSY_TIMEOUT_SEC,
        help="Max seconds to poll STATUS after START",
    )
    parser.add_argument(
        "--status-interval",
        type=float,
        default=DEFAULT_BUSY_POLL_INTERVAL_SEC,
        help="STATUS polling interval seconds",
    )
    parser.add_argument(
        "--reset-before",
        action="store_true",
        help="Send SOFTWARE_RESET once before the test loops",
    )
    parser.add_argument(
        "--reset-delay",
        type=float,
        default=1.0,
        help="Delay after SOFTWARE_RESET seconds",
    )
    parser.add_argument(
        "--abort-on-fail",
        action="store_true",
        help="Try ABORT_MEASUREMENT after a failed loop",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Console log level",
    )
    parser.add_argument(
        "--traceback",
        action="store_true",
        help="Print traceback for failed loops",
    )

    return parser


def make_controller(args: argparse.Namespace, logger: logging.Logger) -> adc_controller:
    return adc_controller(
        port=args.port,
        baudrate=args.baudrate,
        timeout=args.timeout,
        write_timeout=args.write_timeout,
        setup_retry_attempts=args.setup_retries,
        setup_retry_delay_sec=args.setup_retry_delay,
        logger=logger,
    )


def expected_sample_count(args: argparse.Namespace) -> int:
    return 4 * args.cycles * (args.on_samples + args.off_samples)


def first_mismatch_index(left: bytes, right: bytes) -> int:
    for index, (left_byte, right_byte) in enumerate(zip(left, right)):
        if left_byte != right_byte:
            return index
    if len(left) != len(right):
        return min(len(left), len(right))
    return -1


def wait_status(
    ctrl: adc_controller,
    args: argparse.Namespace,
    logger: logging.Logger,
    loop_index: int,
) -> Tuple[ResponseCode, int]:
    deadline = time.monotonic() + args.status_timeout
    query_count = 0

    while True:
        code = ctrl.query_state()
        query_count += 1
        logger.info(
            "loop=%d STATUS #%d: %s - %s",
            loop_index,
            query_count,
            code.name,
            describe_response(code),
        )

        if code != ResponseCode.BUSY:
            return code, query_count

        if time.monotonic() >= deadline:
            raise TimeoutError(
                "STATUS stayed BUSY for %.2f sec, query_count=%d"
                % (args.status_timeout, query_count)
            )

        time.sleep(args.status_interval)


def read_range_test_data(
    ctrl: adc_controller,
    args: argparse.Namespace,
    logger: logging.Logger,
    loop_index: int,
    expected_samples: int,
) -> None:
    logger.info(
        "loop=%d READ_DATA expected_samples=%d range_chunk_samples=%d "
        "range_retry_attempts=%d output=%s csv=%s",
        loop_index,
        expected_samples,
        args.range_chunk_samples,
        args.range_retry_attempts,
        args.output,
        args.csv_output,
    )
    ctrl.reset_input_buffer()
    received_samples = ctrl.read_range_to_file(
        output_file=args.output,
        expected_samples=expected_samples,
        range_chunk_samples=args.range_chunk_samples,
        range_retry_attempts=args.range_retry_attempts,
        read_empty_retry_limit=args.read_empty_retry_limit,
        csv_output_file=args.csv_output,
    )
    if received_samples != expected_samples:
        raise RuntimeError(
            "ADC data sample count mismatch: expected=%d received=%d"
            % (expected_samples, received_samples)
        )
    logger.info(
        "loop=%d DATA_PASS received_samples=%d output=%s csv=%s",
        loop_index,
        received_samples,
        args.output,
        args.csv_output,
    )


def compare_all_range_data(
    ctrl: adc_controller,
    args: argparse.Namespace,
    logger: logging.Logger,
    loop_index: int,
    expected_samples: int,
) -> None:
    logger.info(
        "loop=%d COMPARE_ALL_RANGE expected_samples=%d all_output=%s "
        "range_output=%s range_chunk_samples=%d",
        loop_index,
        expected_samples,
        args.all_output,
        args.output,
        args.range_chunk_samples,
    )

    all_raw = ctrl.read_all_data(
        expected_samples=expected_samples,
        read_chunk_samples=args.read_chunk_samples,
        read_empty_retry_limit=args.read_empty_retry_limit,
    )
    range_raw = ctrl.read_range_data(
        expected_samples=expected_samples,
        range_chunk_samples=args.range_chunk_samples,
        range_retry_attempts=args.range_retry_attempts,
        read_empty_retry_limit=args.read_empty_retry_limit,
    )

    all_samples = ctrl.save_raw_measure_data(
        output_file=args.all_output,
        raw_data=all_raw,
        csv_output_file=args.all_csv_output,
    )
    range_samples = ctrl.save_raw_measure_data(
        output_file=args.output,
        raw_data=range_raw,
        csv_output_file=args.csv_output,
    )

    if all_samples != expected_samples or range_samples != expected_samples:
        raise RuntimeError(
            "ADC compare sample count mismatch: expected=%d all=%d range=%d"
            % (expected_samples, all_samples, range_samples)
        )

    mismatch_index = first_mismatch_index(all_raw, range_raw)
    if mismatch_index >= 0:
        all_value = all_raw[mismatch_index] if mismatch_index < len(all_raw) else None
        range_value = (
            range_raw[mismatch_index] if mismatch_index < len(range_raw) else None
        )
        raise RuntimeError(
            "ALL/RANGE data mismatch: first_mismatch_byte=%d "
            "all_value=%s range_value=%s all_bytes=%d range_bytes=%d"
            % (
                mismatch_index,
                "EOF" if all_value is None else "0x%02X" % all_value,
                "EOF" if range_value is None else "0x%02X" % range_value,
                len(all_raw),
                len(range_raw),
            )
        )

    logger.info(
        "loop=%d COMPARE_PASS samples=%d bytes=%d all_output=%s range_output=%s",
        loop_index,
        expected_samples,
        len(all_raw),
        args.all_output,
        args.output,
    )


def force_close_range_test(
    ctrl: adc_controller,
    args: argparse.Namespace,
    logger: logging.Logger,
    loop_index: int,
    expected_samples: int,
) -> None:
    trigger_sample = args.force_close_range_at_sample
    if trigger_sample is None:
        raise ValueError("force close trigger sample is not set")

    if trigger_sample >= expected_samples:
        raise ValueError(
            "force close trigger sample must be less than expected_samples: "
            "trigger=%d expected_samples=%d" % (trigger_sample, expected_samples)
        )

    logger.warning(
        "loop=%d FORCE_CLOSE_RANGE_TEST expected_samples=%d trigger_sample=%d "
        "range_chunk_samples=%d",
        loop_index,
        expected_samples,
        trigger_sample,
        args.range_chunk_samples,
    )

    next_seq = 0
    received_bytes = 0

    while next_seq < expected_samples:
        chunk_samples = min(args.range_chunk_samples, expected_samples - next_seq)
        chunk_bytes = chunk_samples * BYTES_PER_SAMPLE

        ctrl.reset_input_buffer()
        logger.info(
            "loop=%d START_TRANSMISSION_RANGE seq=%d count=%d",
            loop_index,
            next_seq,
            chunk_samples,
        )
        ctrl.start_transmission_range_no_response(
            starting_seq=next_seq,
            count=chunk_samples,
        )

        if next_seq <= trigger_sample < next_seq + chunk_samples:
            logger.warning(
                "loop=%d FORCE_CLOSE serial after RANGE command: "
                "seq=%d count=%d trigger_sample=%d received_bytes=%d",
                loop_index,
                next_seq,
                chunk_samples,
                trigger_sample,
                received_bytes,
            )
            ctrl.close()

        data = ctrl._read_serial_exact_bytes(
            expected_bytes=chunk_bytes,
            read_empty_retry_limit=args.read_empty_retry_limit,
            context=(
                "force-close range seq=%d count=%d"
                % (next_seq, chunk_samples)
            ),
        )

        if len(data) != chunk_bytes:
            raise RuntimeError(
                "force close range data size mismatch: seq=%d count=%d "
                "expected=%d received=%d"
                % (next_seq, chunk_samples, chunk_bytes, len(data))
            )

        received_bytes += len(data)
        next_seq += chunk_samples

    raise RuntimeError(
        "force close trigger was not reached: trigger_sample=%d expected_samples=%d"
        % (trigger_sample, expected_samples)
    )


def run_measurement_loop(
    ctrl: adc_controller,
    args: argparse.Namespace,
    logger: logging.Logger,
    loop_index: int,
) -> None:
    logger.info("loop=%d RESET_INPUT_BUFFER", loop_index)
    ctrl.reset_input_buffer()

    logger.info(
        "loop=%d SETUP pattern=%d on=%d off=%d cycles=%d",
        loop_index,
        args.pattern,
        args.on_samples,
        args.off_samples,
        args.cycles,
    )
    ctrl.setup(
        pattern=args.pattern,
        on_samples=args.on_samples,
        off_samples=args.off_samples,
        cycles=args.cycles,
    )

    logger.info("loop=%d START_MEASUREMENT", loop_index)
    ctrl.start_measurement()

    final_state, query_count = wait_status(ctrl, args, logger, loop_index)
    if final_state != ResponseCode.OK:
        raise RuntimeError(
            "Measurement ended with %s - %s, query_count=%d"
            % (final_state.name, describe_response(final_state), query_count)
        )

    logger.info("loop=%d PASS query_count=%d", loop_index, query_count)

    if args.force_close_range_at_sample is not None:
        expected_samples = expected_sample_count(args)
        force_close_range_test(ctrl, args, logger, loop_index, expected_samples)
    elif args.compare_all_range:
        expected_samples = expected_sample_count(args)
        compare_all_range_data(ctrl, args, logger, loop_index, expected_samples)
    elif args.read_data:
        expected_samples = expected_sample_count(args)
        read_range_test_data(ctrl, args, logger, loop_index, expected_samples)


def maybe_abort(
    ctrl: adc_controller,
    logger: logging.Logger,
    loop_index: int,
) -> None:
    try:
        logger.warning("loop=%d ABORT_TRANSMISSION after failure", loop_index)
        ctrl.abort_transmission()
    except Exception as err:
        logger.warning("loop=%d ABORT_TRANSMISSION failed: %r", loop_index, err)

    try:
        logger.warning("loop=%d ABORT_MEASUREMENT after failure", loop_index)
        ctrl.abort_measurement()
    except Exception as err:
        logger.warning("loop=%d ABORT_MEASUREMENT failed: %r", loop_index, err)


def run_single_connection(
    args: argparse.Namespace,
    logger: logging.Logger,
) -> Tuple[int, int]:
    attempted = 0
    failures = 0

    ctrl = make_controller(args, logger)
    logger.info("CONNECT port=%s baudrate=%s", args.port, args.baudrate)

    with ctrl:
        if args.reset_before:
            logger.info("SOFTWARE_RESET")
            ctrl.software_reset()
            time.sleep(args.reset_delay)

        for loop_index in range(1, args.loops + 1):
            attempted += 1
            try:
                run_measurement_loop(ctrl, args, logger, loop_index)
            except Exception as err:
                failures += 1
                logger.error(
                    "loop=%d FAIL: %r",
                    loop_index,
                    err,
                    exc_info=args.traceback,
                )
                if args.abort_on_fail:
                    maybe_abort(ctrl, logger, loop_index)
                if args.stop_on_error:
                    break

            if loop_index < args.loops:
                time.sleep(args.loop_delay)

    return attempted, failures


def run_reopen_each_loop(
    args: argparse.Namespace,
    logger: logging.Logger,
) -> Tuple[int, int]:
    attempted = 0
    failures = 0

    for loop_index in range(1, args.loops + 1):
        attempted += 1
        ctrl = make_controller(args, logger)
        logger.info(
            "loop=%d CONNECT port=%s baudrate=%s",
            loop_index,
            args.port,
            args.baudrate,
        )

        try:
            with ctrl:
                if args.reset_before and loop_index == 1:
                    logger.info("loop=%d SOFTWARE_RESET", loop_index)
                    ctrl.software_reset()
                    time.sleep(args.reset_delay)

                run_measurement_loop(ctrl, args, logger, loop_index)

        except Exception as err:
            failures += 1
            logger.error(
                "loop=%d FAIL: %r",
                loop_index,
                err,
                exc_info=args.traceback,
            )
            if args.abort_on_fail:
                try:
                    if ctrl.ser is None or not ctrl.ser.is_open:
                        ctrl.open()
                    maybe_abort(ctrl, logger, loop_index)
                finally:
                    ctrl.close()
            if args.stop_on_error:
                break

        if loop_index < args.loops:
            time.sleep(args.loop_delay)

    return attempted, failures


def apply_settings_defaults(args: argparse.Namespace) -> None:
    adc_settings = load_adc_settings(args.settings)

    args.port = args.port or adc_settings.get("port", DEFAULT_PORT)
    args.baudrate = (
        args.baudrate
        if args.baudrate is not None
        else int(adc_settings.get("baudrate", DEFAULT_BAUDRATE))
    )
    args.timeout = (
        args.timeout
        if args.timeout is not None
        else float(adc_settings.get("timeout", DEFAULT_TIMEOUT))
    )
    args.write_timeout = (
        args.write_timeout
        if args.write_timeout is not None
        else float(adc_settings.get("write_timeout", DEFAULT_WRITE_TIMEOUT))
    )


def validate_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if args.loops < 1:
        parser.error("--loops must be >= 1")
    if args.status_timeout <= 0:
        parser.error("--status-timeout must be > 0")
    if args.status_interval <= 0:
        parser.error("--status-interval must be > 0")
    if args.loop_delay < 0:
        parser.error("--loop-delay must be >= 0")
    if args.setup_retries < 1:
        parser.error("--setup-retries must be >= 1")
    if args.read_chunk_samples <= 0:
        parser.error("--read-chunk-samples must be > 0")
    if args.range_chunk_samples <= 0:
        parser.error("--range-chunk-samples must be > 0")
    if args.range_retry_attempts < 1:
        parser.error("--range-retry-attempts must be >= 1")
    if args.read_empty_retry_limit < 0:
        parser.error("--read-empty-retry-limit must be >= 0")
    if (
        args.force_close_range_at_sample is not None
        and args.force_close_range_at_sample < 0
    ):
        parser.error("--force-close-range-at-sample must be >= 0")
    if (args.read_data or args.compare_all_range) and not args.output:
        parser.error("--output is required when data output is enabled")
    if (args.read_data or args.compare_all_range) and not args.csv_output:
        parser.error("--csv-output is required when data output is enabled")
    if args.compare_all_range and not args.all_output:
        parser.error("--all-output is required when --compare-all-range is used")
    if args.compare_all_range and not args.all_csv_output:
        parser.error("--all-csv-output is required when --compare-all-range is used")


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()
    apply_settings_defaults(args)
    validate_args(parser, args)

    logger = setup_logger(args.log_level)
    logger.info("ADC communication test started")
    logger.info(
        "settings=%s reopen_each_loop=%s loops=%d read_data=%s "
        "compare_all_range=%s force_close_range_at_sample=%s "
        "timeout=%.3f write_timeout=%.3f",
        args.settings,
        args.reopen_each_loop,
        args.loops,
        args.read_data,
        args.compare_all_range,
        args.force_close_range_at_sample,
        args.timeout,
        args.write_timeout,
    )

    try:
        if args.reopen_each_loop:
            attempted, failures = run_reopen_each_loop(args, logger)
        else:
            attempted, failures = run_single_connection(args, logger)
    except KeyboardInterrupt:
        logger.warning("KeyboardInterrupt")
        return 130

    passed = attempted - failures
    logger.info(
        "ADC communication test finished: requested=%d attempted=%d passed=%d failed=%d",
        args.loops,
        attempted,
        passed,
        failures,
    )
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
