# ERS_ADC_Control.py
# Python 3.9.2 compatible

from __future__ import annotations

import argparse
import logging
import os
import time
from datetime import datetime
from typing import Optional

import serial

from parser import (
    MEASURE_BLOCK_SIZE,
    convert_measure_stream_to_legacy_payload,
    write_legacy_payload_csv,
)

from ERS_ADC_Protocol import (
    ResponseCode,
    build_setup_command,
    build_query_state_command,
    build_start_measurement_command,
    build_abort_measurement_command,
    build_start_transmission_all_command,
    build_start_transmission_range_command,
    build_abort_transmission_command,
    build_software_reset_command,
    parse_response_code,
    describe_response,
)


DEFAULT_PORT = "/dev/ttyACM0"
DEFAULT_BAUDRATE = 115200
DEFAULT_TIMEOUT = 0.5
DEFAULT_WRITE_TIMEOUT = 0.5

DEFAULT_LOG_DIR = "./log"

# ADC sample binary format:
# time(2) + fb(2) + values(12 * 4) + ts(4) + null1(4) + null2(4)
# total = 64 bytes/sample
BYTES_PER_SAMPLE = 64

DEFAULT_READ_CHUNK_SAMPLES = 200

DEFAULT_BUSY_TIMEOUT_SEC = 10.0
DEFAULT_BUSY_POLL_INTERVAL_SEC = 1.0

if MEASURE_BLOCK_SIZE != BYTES_PER_SAMPLE:
    raise RuntimeError(
        "ADC parser block size mismatch: "
        f"parser={MEASURE_BLOCK_SIZE}, control={BYTES_PER_SAMPLE}"
    )


def parse(data: bytes) -> bytes:
    """
    Parse or transform received ADC binary data before saving.

    Convert 64-byte measurement blocks into the legacy 52-byte save format.
    """
    return convert_measure_stream_to_legacy_payload(data)


class adc_controller:
    """
    ERS ADC controller.

    역할:
        - Serial 연결
        - Setup 명령 전송
        - Measurement start / abort
        - Measurement state query
        - Transmission start / abort
        - ADC raw binary data 저장

    중요:
        START_TRANSMISSION_ALL 명령 후에는 응답 1 byte를 읽지 않는다.
        ESP32가 바로 ADC binary data를 보내는 구조이기 때문이다.
    """

    def __init__(
        self,
        port: str = DEFAULT_PORT,
        baudrate: int = DEFAULT_BAUDRATE,
        timeout: float = DEFAULT_TIMEOUT,
        write_timeout: float = DEFAULT_WRITE_TIMEOUT,
        save_csv: bool = False,
        csv_folder: str = DEFAULT_LOG_DIR,
        convert: Optional[bool] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.write_timeout = write_timeout
        self.save_csv = bool(save_csv if convert is None else convert)
        self.csv_folder = csv_folder
        self.logger = logger or logging.getLogger(__name__)
        self.ser: Optional[serial.Serial] = None

    # -------------------------------------------------------------------------
    # Serial open / close
    # -------------------------------------------------------------------------

    def open(self) -> None:
        if self.ser is not None and self.ser.is_open:
            return

        try:
            self.ser = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                timeout=self.timeout,
                write_timeout=self.write_timeout,
            )
            self.logger.info("Serial opened: %s", self.port)

        except serial.SerialException as err:
            self.logger.error("Failed to open serial port %s: %r", self.port, err)
            raise

    def close(self) -> None:
        if self.ser is not None:
            try:
                if self.ser.is_open:
                    self.ser.close()
                    self.logger.info("Serial closed: %s", self.port)
            finally:
                self.ser = None

    def __enter__(self) -> "adc_controller":
        self.open()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def _require_serial(self) -> serial.Serial:
        if self.ser is None or not self.ser.is_open:
            raise RuntimeError("Serial port is not open")
        return self.ser

    # -------------------------------------------------------------------------
    # Response / command helpers
    # -------------------------------------------------------------------------

    def reset_input_buffer(self) -> None:
        ser = self._require_serial()
        ser.reset_input_buffer()
        self.logger.debug("Serial input buffer reset")

    def read_response_code(self, command_name: str = "command") -> ResponseCode:
        ser = self._require_serial()

        data = ser.read(1)
        if data == b"":
            raise TimeoutError("%s: no response from ADC device" % command_name)

        code = parse_response_code(data)

        self.logger.debug(
            "%s response: %s - %s",
            command_name,
            code.name,
            describe_response(code),
        )

        return code

    def send_command(self, packet: bytes, command_name: str) -> ResponseCode:
        """
        명령을 보내고 response code 1 byte를 읽는다.

        SETUP, START_MEASUREMENT, QUERY_STATE, ABORT, RESET 등에 사용.
        """
        ser = self._require_serial()

        try:
            ser.write(packet)
            ser.flush()
        except serial.SerialTimeoutException as err:
            self.logger.error("%s: serial write timeout: %r", command_name, err)
            raise
        except serial.SerialException as err:
            self.logger.error("%s: serial write error: %r", command_name, err)
            raise

        return self.read_response_code(command_name)

    def send_command_expect_ok(self, packet: bytes, command_name: str) -> None:
        code = self.send_command(packet, command_name)

        if code != ResponseCode.OK:
            raise RuntimeError(
                "%s failed: %s - %s"
                % (command_name, code.name, describe_response(code))
            )

    def send_command_no_response(self, packet: bytes, command_name: str) -> None:
        """
        명령을 보내고 response code를 읽지 않는다.

        START_TRANSMISSION_ALL에 사용한다.
        ESP32가 명령 수신 후 바로 ADC binary data를 보내는 구조라면,
        여기서 1 byte를 읽으면 데이터 첫 byte를 잃어버린다.
        """
        ser = self._require_serial()

        try:
            ser.write(packet)
            ser.flush()
        except serial.SerialTimeoutException as err:
            self.logger.error("%s: serial write timeout: %r", command_name, err)
            raise
        except serial.SerialException as err:
            self.logger.error("%s: serial write error: %r", command_name, err)
            raise

        self.logger.debug("%s command sent without reading response", command_name)

    # -------------------------------------------------------------------------
    # ADC protocol commands
    # -------------------------------------------------------------------------

    def setup(
        self,
        pattern: int,
        on_samples: int,
        off_samples: int,
        cycles: int,
    ) -> None:
        packet = build_setup_command(
            pattern=pattern,
            on_duration_samples=on_samples,
            off_duration_samples=off_samples,
            total_cycles=cycles,
        )

        self.send_command_expect_ok(packet, "SETUP")

    def query_state(self) -> ResponseCode:
        packet = build_query_state_command()
        return self.send_command(packet, "QUERY_STATE")

    def start_measurement(self) -> None:
        packet = build_start_measurement_command()
        self.send_command_expect_ok(packet, "START_MEASUREMENT")

    def abort_measurement(self) -> None:
        packet = build_abort_measurement_command()
        self.send_command_expect_ok(packet, "ABORT_MEASUREMENT")

    def start_transmission_all(self) -> None:
        """
        기존 방식: 명령 후 OK 응답을 읽는 방식.
        현재 ESP32 데이터 전송 구조에서는 capture()에서 사용하지 않는다.
        """
        packet = build_start_transmission_all_command()
        self.send_command_expect_ok(packet, "START_TRANSMISSION_ALL")

    def start_transmission_all_no_response(self) -> None:
        """
        데이터 전송 시작.

        PC 테스트 코드 흐름과 동일하게, 명령 전송 후 응답을 읽지 않고
        바로 binary data 수신으로 넘어간다.
        """
        packet = build_start_transmission_all_command()
        self.send_command_no_response(packet, "START_TRANSMISSION_ALL")

    def start_transmission_range(self, starting_seq: int, count: int) -> None:
        """
        Range transmission.

        현재 이 함수는 응답을 읽는 구조로 둔다.
        만약 range transmission도 바로 binary data를 보내는 구조라면
        별도의 no_response 버전을 만들어야 한다.
        """
        packet = build_start_transmission_range_command(starting_seq, count)
        self.send_command_expect_ok(packet, "START_TRANSMISSION_RANGE")

    def abort_transmission(self) -> None:
        packet = build_abort_transmission_command()
        self.send_command_expect_ok(packet, "ABORT_TRANSMISSION")

    def software_reset(self) -> None:
        packet = build_software_reset_command()
        self.send_command_expect_ok(packet, "SOFTWARE_RESET")

    # -------------------------------------------------------------------------
    # Busy wait
    # -------------------------------------------------------------------------

    def wait_until_not_busy(
        self,
        timeout_sec: float = DEFAULT_BUSY_TIMEOUT_SEC,
        poll_interval_sec: float = DEFAULT_BUSY_POLL_INTERVAL_SEC,
    ) -> ResponseCode:
        """
        QUERY_STATE를 반복해서 ADC가 BUSY가 아닐 때까지 기다린다.

        ESP32 firmware rule:
            - BUSY: measurement is still running
            - not BUSY: data can be requested
        """
        start_time = time.time()
        query_count = 0

        while True:
            code = self.query_state()
            query_count += 1

            if code != ResponseCode.BUSY:
                self.logger.info(
                    "ADC is not busy: %s - %s, query_count=%d",
                    code.name,
                    describe_response(code),
                    query_count,
                )
                return code

            elapsed = time.time() - start_time
            if elapsed >= timeout_sec:
                raise TimeoutError(
                    "ADC is still BUSY after %.2f sec, query_count=%d"
                    % (timeout_sec, query_count)
                )

            time.sleep(poll_interval_sec)

    # -------------------------------------------------------------------------
    # Data read / save
    # -------------------------------------------------------------------------

    def read_exact_to_file(
        self,
        output_file: str,
        expected_samples: int,
        read_chunk_samples: int = DEFAULT_READ_CHUNK_SAMPLES,
    ) -> int:
        """
        ADC binary data를 파일에 저장한다.

        Parameters
        ----------
        output_file:
            저장할 binary 파일 경로

        expected_samples:
            받을 sample 개수

        read_chunk_samples:
            한 번에 읽을 sample 개수

        Returns
        -------
        int
            실제로 수신한 sample 개수
        """
        ser = self._require_serial()

        if expected_samples <= 0:
            raise ValueError("expected_samples must be greater than 0")

        if read_chunk_samples <= 0:
            raise ValueError("read_chunk_samples must be greater than 0")

        expected_bytes = expected_samples * BYTES_PER_SAMPLE
        read_chunk_bytes = read_chunk_samples * BYTES_PER_SAMPLE

        output_dir = os.path.dirname(os.path.abspath(output_file))
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)

        received_bytes = 0
        start_time = time.time()
        last_log_time = start_time

        self.logger.info(
            "Start receiving ADC data: expected_samples=%d, expected_bytes=%d",
            expected_samples,
            expected_bytes,
        )

        raw_data = bytearray()

        while received_bytes < expected_bytes:
            remain_bytes = expected_bytes - received_bytes
            request_bytes = min(read_chunk_bytes, remain_bytes)

            data = ser.read(request_bytes)

            if data == b"":
                self.logger.warning(
                    "Serial read timeout: received_bytes=%d / %d",
                    received_bytes,
                    expected_bytes,
                )
                break

            raw_data.extend(data)
            received_bytes += len(data)

            now = time.time()
            if now - last_log_time >= 1.0:
                self.logger.info(
                    "Receiving... %d / %d bytes",
                    received_bytes,
                    expected_bytes,
                )
                last_log_time = now

        elapsed = time.time() - start_time
        received_samples = received_bytes // BYTES_PER_SAMPLE
        remain_partial_bytes = received_bytes % BYTES_PER_SAMPLE

        self.logger.info(
            "ADC data received: received_samples=%d, received_bytes=%d, elapsed=%.2f sec",
            received_samples,
            received_bytes,
            elapsed,
        )

        if remain_partial_bytes != 0:
            self.logger.warning(
                "Received data has partial sample bytes: %d bytes",
                remain_partial_bytes,
            )

        if received_bytes != expected_bytes:
            missing_bytes = expected_bytes - received_bytes
            message = (
                "ADC data size mismatch: "
                f"expected={expected_bytes} bytes, "
                f"received={received_bytes} bytes, "
                f"missing={missing_bytes} bytes, "
                f"output_file={output_file}"
            )
            self.logger.error(message)
            raise RuntimeError(message)

        parsed_data = parse(bytes(raw_data))

        with open(output_file, "wb") as f:
            f.write(parsed_data)

        self.logger.info(
            "ADC data saved: %s, parsed_bytes=%d",
            output_file,
            len(parsed_data),
        )

        if self.save_csv:
            csv_file = self.make_csv_output_file(output_file)
            try:
                row_count = write_legacy_payload_csv(parsed_data, csv_file)
                self.logger.info(
                    "ADC CSV saved: %s, rows=%d",
                    csv_file,
                    row_count,
                )
            except Exception as err:
                self.logger.error("ADC CSV save failed: %r", err)

        return received_samples

    def make_csv_output_file(self, output_file: str) -> str:
        base_name = os.path.splitext(os.path.basename(output_file))[0]
        return os.path.join(self.csv_folder, base_name + ".csv")

    # -------------------------------------------------------------------------
    # High-level capture sequence
    # -------------------------------------------------------------------------

    def capture(
        self,
        output_file: str,
        pattern: int = 0,
        on_samples: int = 480,
        off_samples: int = 48,
        cycles: int = 1,
        start_transmission: bool = True,
        read_chunk_samples: int = DEFAULT_READ_CHUNK_SAMPLES,
        busy_timeout_sec: float = DEFAULT_BUSY_TIMEOUT_SEC,
        busy_poll_interval_sec: float = DEFAULT_BUSY_POLL_INTERVAL_SEC,
    ) -> int:
        """
        전체 측정 순서 실행.

        순서:
            1. 입력 버퍼 비우기
            2. SETUP
            3. START_MEASUREMENT
            4. QUERY_STATE 반복
               - BUSY이면 계속 대기
               - BUSY가 아니면 측정 완료로 판단
            5. 입력 버퍼 비우기
            6. START_TRANSMISSION_ALL
               - 응답을 읽지 않음
            7. binary data save

        Returns
        -------
        int
            실제 수신 sample 수
        """
        expected_samples = 4 * cycles * (on_samples + off_samples)

        self.logger.info(
            "Capture configuration: pattern=%d, on=%d, off=%d, cycles=%d, expected_samples=%d",
            pattern,
            on_samples,
            off_samples,
            cycles,
            expected_samples,
        )

        # PC 테스트 코드처럼 명령 시작 전 입력 버퍼를 비운다.
        self.reset_input_buffer()

        self.setup(
            pattern=pattern,
            on_samples=on_samples,
            off_samples=off_samples,
            cycles=cycles,
        )

        self.start_measurement()

        self.logger.info("Waiting until ADC measurement is not BUSY...")

        state = self.wait_until_not_busy(
            timeout_sec=busy_timeout_sec,
            poll_interval_sec=busy_poll_interval_sec,
        )

        if state != ResponseCode.OK:
            raise RuntimeError(
                "ADC measurement did not finish normally: %s - %s"
                % (state.name, describe_response(state))
            )

        if start_transmission:
            # PC 테스트 코드처럼 데이터 전송 직전에 입력 버퍼를 비운다.
            # 이전 QUERY_STATE 응답 잔여물이 있을 가능성을 제거한다.
            self.reset_input_buffer()

            # 중요:
            # START_TRANSMISSION_ALL 후에는 response 1 byte를 읽지 않는다.
            # 바로 ADC binary data가 시작된다.
            self.start_transmission_all_no_response()

        received_samples = self.read_exact_to_file(
            output_file=output_file,
            expected_samples=expected_samples,
            read_chunk_samples=read_chunk_samples,
        )

        return received_samples


# -----------------------------------------------------------------------------
# Logging
# -----------------------------------------------------------------------------

def setup_logger(log_level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger("ERS_ADC_Control")
    logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    if logger.handlers:
        return logger

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    return logger


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="ERS ADC control and realtime binary save tool"
    )

    parser.add_argument(
        "--port",
        default=DEFAULT_PORT,
        help="Serial port. Default: %(default)s",
    )

    parser.add_argument(
        "--baudrate",
        type=int,
        default=DEFAULT_BAUDRATE,
        help="Serial baudrate. Default: %(default)s",
    )

    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help="Serial read timeout. Default: %(default)s sec",
    )

    parser.add_argument(
        "--write-timeout",
        type=float,
        default=DEFAULT_WRITE_TIMEOUT,
        help="Serial write timeout. Default: %(default)s sec",
    )

    parser.add_argument(
        "--pattern",
        type=int,
        default=0,
        help="ADC bridge/pattern value. Default: %(default)s",
    )

    parser.add_argument(
        "--on-samples",
        type=int,
        default=480,
        help="ON duration samples. Default: %(default)s",
    )

    parser.add_argument(
        "--off-samples",
        type=int,
        default=48,
        help="OFF duration samples. Default: %(default)s",
    )

    parser.add_argument(
        "--cycles",
        type=int,
        default=1,
        help="Total cycles. Default: %(default)s",
    )

    parser.add_argument(
        "--output",
        default=None,
        help="Output binary file path. Default: ./log/adc_YYMMDD_HHMMSS.bin",
    )

    parser.add_argument(
        "--read-chunk-samples",
        type=int,
        default=DEFAULT_READ_CHUNK_SAMPLES,
        help="Read chunk size in samples. Default: %(default)s",
    )

    parser.add_argument(
        "--busy-timeout",
        type=float,
        default=DEFAULT_BUSY_TIMEOUT_SEC,
        help="Timeout while waiting for ADC BUSY to clear. Default: %(default)s sec",
    )

    parser.add_argument(
        "--busy-poll-interval",
        type=float,
        default=DEFAULT_BUSY_POLL_INTERVAL_SEC,
        help="Polling interval while ADC is BUSY. Default: %(default)s sec",
    )

    parser.add_argument(
        "--query",
        action="store_true",
        help="Only query ADC state and exit",
    )

    parser.add_argument(
        "--reset",
        action="store_true",
        help="Send software reset command and exit",
    )

    parser.add_argument(
        "--abort-measurement",
        action="store_true",
        help="Send abort measurement command and exit",
    )

    parser.add_argument(
        "--abort-transmission",
        action="store_true",
        help="Send abort transmission command and exit",
    )

    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Log level. Default: %(default)s",
    )

    return parser


def make_default_output_file() -> str:
    timestamp = datetime.now().strftime("%y%m%d_%H%M%S")
    os.makedirs(DEFAULT_LOG_DIR, exist_ok=True)
    return os.path.join(DEFAULT_LOG_DIR, "adc_%s.bin" % timestamp)


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()

    logger = setup_logger(args.log_level)

    output_file = args.output or make_default_output_file()

    ctrl = adc_controller(
        port=args.port,
        baudrate=args.baudrate,
        timeout=args.timeout,
        write_timeout=args.write_timeout,
        logger=logger,
    )

    try:
        with ctrl:
            if args.query:
                code = ctrl.query_state()
                print(
                    "ADC state response: %s - %s"
                    % (code.name, describe_response(code))
                )
                return 0

            if args.reset:
                ctrl.software_reset()
                print("Software reset command completed")
                return 0

            if args.abort_measurement:
                ctrl.abort_measurement()
                print("Abort measurement command completed")
                return 0

            if args.abort_transmission:
                ctrl.abort_transmission()
                print("Abort transmission command completed")
                return 0

            expected_samples = 4 * args.cycles * (args.on_samples + args.off_samples)
            expected_bytes = expected_samples * BYTES_PER_SAMPLE

            print("--------------------------------------------------")
            print("ERS ADC Capture")
            print("--------------------------------------------------")
            print("Port               :", args.port)
            print("Baudrate           :", args.baudrate)
            print("Pattern            :", args.pattern)
            print("ON samples         :", args.on_samples)
            print("OFF samples        :", args.off_samples)
            print("Cycles             :", args.cycles)
            print("Expected samples   :", expected_samples)
            print("Expected bytes     :", expected_bytes)
            print("Output file        :", output_file)
            print("Busy timeout       :", args.busy_timeout, "sec")
            print("Busy poll interval :", args.busy_poll_interval, "sec")
            print("--------------------------------------------------")

            received_samples = ctrl.capture(
                output_file=output_file,
                pattern=args.pattern,
                on_samples=args.on_samples,
                off_samples=args.off_samples,
                cycles=args.cycles,
                start_transmission=True,
                read_chunk_samples=args.read_chunk_samples,
                busy_timeout_sec=args.busy_timeout,
                busy_poll_interval_sec=args.busy_poll_interval,
            )

            print("--------------------------------------------------")
            print("Capture finished")
            print("Received samples :", received_samples)
            print("Expected samples :", expected_samples)
            print("Output file      :", output_file)
            print("--------------------------------------------------")

            if received_samples != expected_samples:
                print("WARNING: received sample count does not match expected count")
                return 2

            return 0

    except KeyboardInterrupt:
        logger.warning("KeyboardInterrupt received")

        try:
            ctrl.abort_transmission()
        except Exception as err:
            logger.warning("Failed to abort transmission: %r", err)

        try:
            ctrl.abort_measurement()
        except Exception as err:
            logger.warning("Failed to abort measurement: %r", err)

        return 130

    except Exception as err:
        logger.error("ADC control failed: %r", err)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
