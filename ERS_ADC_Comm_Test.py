from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from typing import Any, Dict

from ERS_ADC_Control import (
    DEFAULT_BAUDRATE,
    DEFAULT_BUSY_POLL_INTERVAL_SEC,
    DEFAULT_BUSY_TIMEOUT_SEC,
    DEFAULT_PORT,
    DEFAULT_SAMPLE_RATE_HZ,
    DEFAULT_TIMEOUT,
    DEFAULT_WRITE_TIMEOUT,
    adc_controller,
)


DEFAULT_TASK_TYPE = "DC"
DEFAULT_ON_TIME_MS = 500.0
DEFAULT_OFF_TIME_MS = 50.0
DEFAULT_STACKS = 1
DEFAULT_OUTPUT_DIR = "./log"
DEFAULT_FILE_NAME_BASE = "adc_comm_test"


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


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Test the same ADC capture path used by ERS_Main.py. "
            "The script opens the ADC port, calls adc.capture(), "
            "and closes the port for each loop."
        )
    )
    parser.add_argument(
        "--port",
        default=DEFAULT_PORT,
        help="ADC serial port. Default: %(default)s",
    )
    parser.add_argument(
        "--loops",
        type=int,
        default=1,
        help="Capture loop count. Default: %(default)s",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.5,
        help="Delay between loops in seconds. Default: %(default)s",
    )
    parser.add_argument(
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        help="Output folder. Default: %(default)s",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Console log level. Default: %(default)s",
    )
    return parser


def build_adc_settings(args: argparse.Namespace) -> Dict[str, Any]:
    return {
        "port": args.port,
        "baudrate": DEFAULT_BAUDRATE,
        "timeout": DEFAULT_TIMEOUT,
        "write_timeout": DEFAULT_WRITE_TIMEOUT,
    }


def build_adc_param() -> Dict[str, Any]:
    if DEFAULT_TASK_TYPE == "IP":
        pattern = 2
    elif DEFAULT_TASK_TYPE == "DC":
        pattern = 1
    else:
        pattern = 0

    spms = DEFAULT_SAMPLE_RATE_HZ / 1000
    on_samples = int(DEFAULT_ON_TIME_MS * spms)
    off_samples = int(DEFAULT_OFF_TIME_MS * spms)
    cycles = int(DEFAULT_STACKS)

    expected_samples = 4 * cycles * (on_samples + off_samples)
    expected_duration_sec = expected_samples / DEFAULT_SAMPLE_RATE_HZ

    return {
        "pattern": pattern,
        "on_samples": on_samples,
        "off_samples": off_samples,
        "cycles": cycles,
        "sample_rate_hz": DEFAULT_SAMPLE_RATE_HZ,
        "busy_timeout_sec": max(
            DEFAULT_BUSY_TIMEOUT_SEC,
            expected_duration_sec + 5.0,
        ),
        "busy_poll_interval_sec": DEFAULT_BUSY_POLL_INTERVAL_SEC,
    }


def expected_sample_count(adc_param: Dict[str, Any]) -> int:
    return (
        4
        * int(adc_param["cycles"])
        * (int(adc_param["on_samples"]) + int(adc_param["off_samples"]))
    )


def make_output_file(args: argparse.Namespace, loop_index: int) -> str:
    os.makedirs(args.output_dir, exist_ok=True)
    return os.path.join(
        args.output_dir,
        "%s-%03d.dat" % (DEFAULT_FILE_NAME_BASE, loop_index),
    )


def run_capture_loop(
    adc_settings: Dict[str, Any],
    adc_param: Dict[str, Any],
    args: argparse.Namespace,
    logger: logging.Logger,
    loop_index: int,
) -> bool:
    output_file = make_output_file(args, loop_index)
    expected_samples = expected_sample_count(adc_param)

    logger.info(
        "loop=%d ADC_CAPTURE_START output=%s expected_samples=%d",
        loop_index,
        output_file,
        expected_samples,
    )

    try:
        with adc_controller(**adc_settings, logger=logger) as adc:
            received_samples = adc.capture(
                output_file=output_file,
                **adc_param,
            )
    except Exception as err:
        logger.error(
            "loop=%d ADC_CAPTURE_FAIL: %r",
            loop_index,
            err,
            exc_info=True,
        )
        return False

    if received_samples != expected_samples:
        logger.error(
            "loop=%d ADC_SAMPLE_COUNT_FAIL expected=%d received=%d",
            loop_index,
            expected_samples,
            received_samples,
        )
        return False

    logger.info(
        "loop=%d ADC_CAPTURE_PASS received_samples=%d output=%s",
        loop_index,
        received_samples,
        output_file,
    )
    return True


def validate_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if args.loops < 1:
        parser.error("--loops must be >= 1")
    if args.delay < 0:
        parser.error("--delay must be >= 0")


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()
    validate_args(parser, args)

    logger = setup_logger(args.log_level)

    adc_settings = build_adc_settings(args)
    adc_param = build_adc_param()

    logger.info("ADC capture test started")
    logger.info("loops=%d output_dir=%s", args.loops, args.output_dir)
    logger.info(
        "capture_defaults=task_type=%s on_time_ms=%.3f off_time_ms=%.3f "
        "stacks=%d sample_rate=%.3f busy_poll_interval=%.3f",
        DEFAULT_TASK_TYPE,
        DEFAULT_ON_TIME_MS,
        DEFAULT_OFF_TIME_MS,
        DEFAULT_STACKS,
        DEFAULT_SAMPLE_RATE_HZ,
        DEFAULT_BUSY_POLL_INTERVAL_SEC,
    )
    logger.info("adc_settings=%s", adc_settings)
    logger.info("adc_param=%s", adc_param)

    failures = 0
    try:
        for loop_index in range(1, args.loops + 1):
            if not run_capture_loop(
                adc_settings=adc_settings,
                adc_param=adc_param,
                args=args,
                logger=logger,
                loop_index=loop_index,
            ):
                failures += 1

            if loop_index < args.loops:
                time.sleep(args.delay)
    except KeyboardInterrupt:
        logger.warning("KeyboardInterrupt")
        return 130

    passed = args.loops - failures
    logger.info(
        "ADC capture test finished: requested=%d passed=%d failed=%d",
        args.loops,
        passed,
        failures,
    )
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
