from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from typing import Any, Dict, Optional, Tuple

from ERS_ADC_Control import (
    DEFAULT_BAUDRATE,
    DEFAULT_BUSY_POLL_INTERVAL_SEC,
    DEFAULT_BUSY_TIMEOUT_SEC,
    DEFAULT_PORT,
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
            "and STATUS polling without reading ADC sample data."
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


def maybe_abort(
    ctrl: adc_controller,
    logger: logging.Logger,
    loop_index: int,
) -> None:
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


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()
    apply_settings_defaults(args)
    validate_args(parser, args)

    logger = setup_logger(args.log_level)
    logger.info("ADC communication test started")
    logger.info(
        "settings=%s reopen_each_loop=%s loops=%d timeout=%.3f write_timeout=%.3f",
        args.settings,
        args.reopen_each_loop,
        args.loops,
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
