# ERS_ADC_Control.py
# Python 3.9.2 compatible

from __future__ import annotations

import logging
import os
import time
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
DEFAULT_READ_EMPTY_RETRY_LIMIT = 5
DEFAULT_RANGE_READ_CHUNK_SAMPLES = 63
DEFAULT_RANGE_READ_RETRY_ATTEMPTS = 5
DEFAULT_RANGE_READ_RETRY_DELAY_SEC = 0.05

DEFAULT_SAMPLE_RATE_HZ = 2400.0

DEFAULT_BUSY_TIMEOUT_SEC = 10.0
DEFAULT_BUSY_POLL_INTERVAL_SEC = 1.0
DEFAULT_SETUP_RETRY_ATTEMPTS = 5
DEFAULT_SETUP_RETRY_DELAY_SEC = 0.2

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
        setup_retry_attempts: int = DEFAULT_SETUP_RETRY_ATTEMPTS,
        setup_retry_delay_sec: float = DEFAULT_SETUP_RETRY_DELAY_SEC,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.write_timeout = write_timeout
        self.save_csv = bool(save_csv if convert is None else convert)
        self.csv_folder = csv_folder
        self.setup_retry_attempts = max(1, int(setup_retry_attempts))
        self.setup_retry_delay_sec = max(0.0, float(setup_retry_delay_sec))
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
            try:
                self.ser.dtr = True
                self.logger.info("Serial DTR set HIGH: %s", self.port)
            except Exception as err:
                self.logger.warning(
                    "Failed to set Serial DTR HIGH after open %s: %r",
                    self.port,
                    err,
                )

        except serial.SerialException as err:
            self.logger.error("Failed to open serial port %s: %r", self.port, err)
            raise

    def close(self) -> None:
        if self.ser is not None:
            try:
                if self.ser.is_open:
                    try:
                        self.ser.dtr = False
                        self.logger.info("Serial DTR set LOW: %s", self.port)
                    except Exception as err:
                        self.logger.warning(
                            "Failed to set Serial DTR LOW before close %s: %r",
                            self.port,
                            err,
                        )
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

        last_err: Optional[Exception] = None

        for attempt in range(1, self.setup_retry_attempts + 1):
            if attempt > 1:
                time.sleep(self.setup_retry_delay_sec)
                self.reset_input_buffer()

            try:
                self.send_command_expect_ok(packet, "SETUP")
                if attempt > 1:
                    self.logger.info(
                        "SETUP succeeded after retry: attempt=%d/%d",
                        attempt,
                        self.setup_retry_attempts,
                    )
                return
            except Exception as err:
                last_err = err
                if attempt >= self.setup_retry_attempts:
                    break
                self.logger.warning(
                    "SETUP failed: attempt=%d/%d err=%r",
                    attempt,
                    self.setup_retry_attempts,
                    err,
                )

        self.logger.error(
            "SETUP failed after %d attempts: %r",
            self.setup_retry_attempts,
            last_err,
        )
        if last_err is not None:
            raise last_err
        raise RuntimeError("SETUP failed without an exception")

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

    def start_transmission_range_no_response(self, starting_seq: int, count: int) -> None:
        """
        Range transmission without reading a response byte.

        On a valid Command 4 range request, firmware streams binary data
        immediately. Reading a response byte here would consume the first byte
        of the ADC data stream.
        """
        packet = build_start_transmission_range_command(starting_seq, count)
        self.send_command_no_response(packet, "START_TRANSMISSION_RANGE")

    def abort_transmission(self) -> None:
        packet = build_abort_transmission_command()
        self.send_command_no_response(packet, "ABORT_TRANSMISSION")

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
        read_empty_retry_limit: int = DEFAULT_READ_EMPTY_RETRY_LIMIT,
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

        if read_empty_retry_limit < 0:
            raise ValueError("read_empty_retry_limit must be >= 0")

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
        empty_read_count = 0

        while received_bytes < expected_bytes:
            remain_bytes = expected_bytes - received_bytes
            request_bytes = min(read_chunk_bytes, remain_bytes)

            data = ser.read(request_bytes)

            if data == b"":
                empty_read_count += 1
                self.logger.warning(
                    "Serial read timeout: received_bytes=%d / %d, retry=%d/%d",
                    received_bytes,
                    expected_bytes,
                    empty_read_count,
                    read_empty_retry_limit,
                )
                if empty_read_count >= read_empty_retry_limit:
                    break
                continue

            empty_read_count = 0

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

    def _read_serial_exact_bytes(
        self,
        expected_bytes: int,
        read_empty_retry_limit: int,
        context: str,
        max_read_bytes: Optional[int] = None,
    ) -> bytes:
        ser = self._require_serial()

        if expected_bytes <= 0:
            raise ValueError("expected_bytes must be greater than 0")

        if read_empty_retry_limit < 0:
            raise ValueError("read_empty_retry_limit must be >= 0")

        if max_read_bytes is not None and max_read_bytes <= 0:
            raise ValueError("max_read_bytes must be greater than 0")

        data_buffer = bytearray()
        empty_read_count = 0

        while len(data_buffer) < expected_bytes:
            remain_bytes = expected_bytes - len(data_buffer)
            request_bytes = (
                min(max_read_bytes, remain_bytes)
                if max_read_bytes is not None
                else remain_bytes
            )
            data = ser.read(request_bytes)

            if data == b"":
                empty_read_count += 1
                self.logger.warning(
                    "%s serial read timeout: received_bytes=%d / %d, retry=%d/%d",
                    context,
                    len(data_buffer),
                    expected_bytes,
                    empty_read_count,
                    read_empty_retry_limit,
                )
                if empty_read_count >= read_empty_retry_limit:
                    break
                continue

            empty_read_count = 0
            data_buffer.extend(data)

        return bytes(data_buffer)

    def _prepare_range_retry(self, retry_delay_sec: float) -> None:
        try:
            self.reset_input_buffer()
        except Exception as err:
            self.logger.warning("ADC range retry input buffer reset failed: %r", err)

        if retry_delay_sec > 0:
            time.sleep(retry_delay_sec)

    def _save_raw_measure_data(
        self,
        output_file: str,
        raw_data: bytes,
        csv_output_file: Optional[str] = None,
    ) -> int:
        output_dir = os.path.dirname(os.path.abspath(output_file))
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)

        received_bytes = len(raw_data)
        received_samples = received_bytes // BYTES_PER_SAMPLE
        remain_partial_bytes = received_bytes % BYTES_PER_SAMPLE

        if remain_partial_bytes != 0:
            raise RuntimeError(
                "ADC data has partial sample bytes: "
                f"received_bytes={received_bytes}, "
                f"partial_bytes={remain_partial_bytes}, "
                f"output_file={output_file}"
            )

        parsed_data = parse(raw_data)

        with open(output_file, "wb") as f:
            f.write(parsed_data)

        self.logger.info(
            "ADC data saved: %s, parsed_bytes=%d",
            output_file,
            len(parsed_data),
        )

        if csv_output_file is not None:
            row_count = write_legacy_payload_csv(parsed_data, csv_output_file)
            self.logger.info(
                "ADC CSV saved: %s, rows=%d",
                csv_output_file,
                row_count,
            )
        elif self.save_csv:
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

    def save_raw_measure_data(
        self,
        output_file: str,
        raw_data: bytes,
        csv_output_file: Optional[str] = None,
    ) -> int:
        return self._save_raw_measure_data(
            output_file=output_file,
            raw_data=raw_data,
            csv_output_file=csv_output_file,
        )

    def read_all_data(
        self,
        expected_samples: int,
        read_chunk_samples: int = DEFAULT_READ_CHUNK_SAMPLES,
        read_empty_retry_limit: int = DEFAULT_READ_EMPTY_RETRY_LIMIT,
    ) -> bytes:
        if expected_samples <= 0:
            raise ValueError("expected_samples must be greater than 0")

        if read_chunk_samples <= 0:
            raise ValueError("read_chunk_samples must be greater than 0")

        expected_bytes = expected_samples * BYTES_PER_SAMPLE
        read_chunk_bytes = read_chunk_samples * BYTES_PER_SAMPLE
        start_time = time.time()

        self.logger.info(
            "Start receiving ADC data by all: expected_samples=%d, expected_bytes=%d",
            expected_samples,
            expected_bytes,
        )

        self.reset_input_buffer()
        self.start_transmission_all_no_response()

        raw_data = self._read_serial_exact_bytes(
            expected_bytes=expected_bytes,
            read_empty_retry_limit=read_empty_retry_limit,
            context="all",
            max_read_bytes=read_chunk_bytes,
        )

        elapsed = time.time() - start_time
        received_bytes = len(raw_data)
        received_samples = received_bytes // BYTES_PER_SAMPLE

        self.logger.info(
            "ADC all data received: received_samples=%d, received_bytes=%d, elapsed=%.2f sec",
            received_samples,
            received_bytes,
            elapsed,
        )

        if received_bytes != expected_bytes:
            missing_bytes = expected_bytes - received_bytes
            message = (
                "ADC all data size mismatch: "
                f"expected={expected_bytes} bytes, "
                f"received={received_bytes} bytes, "
                f"missing={missing_bytes} bytes"
            )
            self.logger.error(message)
            raise RuntimeError(message)

        return raw_data

    def read_range_data(
        self,
        expected_samples: int,
        range_chunk_samples: int = DEFAULT_RANGE_READ_CHUNK_SAMPLES,
        range_retry_attempts: int = DEFAULT_RANGE_READ_RETRY_ATTEMPTS,
        read_empty_retry_limit: int = DEFAULT_READ_EMPTY_RETRY_LIMIT,
        retry_delay_sec: float = DEFAULT_RANGE_READ_RETRY_DELAY_SEC,
    ) -> bytes:
        if expected_samples <= 0:
            raise ValueError("expected_samples must be greater than 0")

        if range_chunk_samples <= 0:
            raise ValueError("range_chunk_samples must be greater than 0")

        if range_retry_attempts < 1:
            raise ValueError("range_retry_attempts must be >= 1")

        if read_empty_retry_limit < 0:
            raise ValueError("read_empty_retry_limit must be >= 0")

        if retry_delay_sec < 0:
            raise ValueError("retry_delay_sec must be >= 0")

        expected_bytes = expected_samples * BYTES_PER_SAMPLE
        raw_data = bytearray()
        start_time = time.time()
        next_seq = 0

        self.logger.info(
            "Start receiving ADC data by range: expected_samples=%d, "
            "expected_bytes=%d, range_chunk_samples=%d, retry_attempts=%d",
            expected_samples,
            expected_bytes,
            range_chunk_samples,
            range_retry_attempts,
        )

        while next_seq < expected_samples:
            chunk_samples = min(range_chunk_samples, expected_samples - next_seq)
            chunk_bytes = chunk_samples * BYTES_PER_SAMPLE
            chunk_data: Optional[bytes] = None
            last_received_bytes = 0

            for attempt in range(1, range_retry_attempts + 1):
                self.reset_input_buffer()
                self.logger.debug(
                    "START_TRANSMISSION_RANGE seq=%d count=%d attempt=%d/%d",
                    next_seq,
                    chunk_samples,
                    attempt,
                    range_retry_attempts,
                )
                self.start_transmission_range_no_response(
                    starting_seq=next_seq,
                    count=chunk_samples,
                )

                data = self._read_serial_exact_bytes(
                    expected_bytes=chunk_bytes,
                    read_empty_retry_limit=1,
                    context=(
                        "range seq=%d count=%d attempt=%d/%d"
                        % (next_seq, chunk_samples, attempt, range_retry_attempts)
                    ),
                )
                last_received_bytes = len(data)

                if last_received_bytes == chunk_bytes:
                    chunk_data = data
                    break

                missing_bytes = chunk_bytes - last_received_bytes
                self.logger.warning(
                    "ADC range data size mismatch: seq=%d count=%d "
                    "expected=%d received=%d missing=%d attempt=%d/%d",
                    next_seq,
                    chunk_samples,
                    chunk_bytes,
                    last_received_bytes,
                    missing_bytes,
                    attempt,
                    range_retry_attempts,
                )

                if attempt < range_retry_attempts:
                    self._prepare_range_retry(retry_delay_sec)

            if chunk_data is None:
                missing_bytes = chunk_bytes - last_received_bytes
                message = (
                    "ADC range data size mismatch: "
                    f"seq={next_seq}, "
                    f"count={chunk_samples}, "
                    f"expected={chunk_bytes} bytes, "
                    f"received={last_received_bytes} bytes, "
                    f"missing={missing_bytes} bytes, "
                    f"attempts={range_retry_attempts}"
                )
                self.logger.error(message)
                raise RuntimeError(message)

            raw_data.extend(chunk_data)
            next_seq += chunk_samples

            self.logger.debug(
                "ADC range received: samples=%d / %d, bytes=%d / %d",
                next_seq,
                expected_samples,
                len(raw_data),
                expected_bytes,
            )

        elapsed = time.time() - start_time
        self.logger.info(
            "ADC range data received: received_samples=%d, received_bytes=%d, elapsed=%.2f sec",
            len(raw_data) // BYTES_PER_SAMPLE,
            len(raw_data),
            elapsed,
        )

        return bytes(raw_data)

    def read_range_to_file(
        self,
        output_file: str,
        expected_samples: int,
        range_chunk_samples: int = DEFAULT_RANGE_READ_CHUNK_SAMPLES,
        range_retry_attempts: int = DEFAULT_RANGE_READ_RETRY_ATTEMPTS,
        read_empty_retry_limit: int = DEFAULT_READ_EMPTY_RETRY_LIMIT,
        retry_delay_sec: float = DEFAULT_RANGE_READ_RETRY_DELAY_SEC,
        csv_output_file: Optional[str] = None,
    ) -> int:
        try:
            raw_data = self.read_range_data(
                expected_samples=expected_samples,
                range_chunk_samples=range_chunk_samples,
                range_retry_attempts=range_retry_attempts,
                read_empty_retry_limit=read_empty_retry_limit,
                retry_delay_sec=retry_delay_sec,
            )
        except RuntimeError as err:
            raise RuntimeError("%s, output_file=%s" % (err, output_file)) from err

        return self._save_raw_measure_data(
            output_file=output_file,
            raw_data=raw_data,
            csv_output_file=csv_output_file,
        )

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
        read_empty_retry_limit: int = DEFAULT_READ_EMPTY_RETRY_LIMIT,
        range_chunk_samples: int = DEFAULT_RANGE_READ_CHUNK_SAMPLES,
        range_retry_attempts: int = DEFAULT_RANGE_READ_RETRY_ATTEMPTS,
        range_retry_delay_sec: float = DEFAULT_RANGE_READ_RETRY_DELAY_SEC,
        busy_timeout_sec: float = DEFAULT_BUSY_TIMEOUT_SEC,
        busy_poll_interval_sec: float = DEFAULT_BUSY_POLL_INTERVAL_SEC,
        sample_rate_hz: float = DEFAULT_SAMPLE_RATE_HZ,
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
        sample_rate_hz = float(sample_rate_hz)
        if sample_rate_hz <= 0:
            raise ValueError("sample_rate_hz must be greater than 0")

        expected_measurement_sec = expected_samples / sample_rate_hz

        self.logger.info(
            "Capture configuration: pattern=%d, on=%d, off=%d, cycles=%d, expected_samples=%d, sample_rate=%.2f Hz, expected_measurement=%.3f sec",
            pattern,
            on_samples,
            off_samples,
            cycles,
            expected_samples,
            sample_rate_hz,
            expected_measurement_sec,
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
        if expected_measurement_sec > 0:
            self.logger.info(
                "Sleeping for expected ADC measurement time: %.3f sec",
                expected_measurement_sec,
            )
            time.sleep(expected_measurement_sec)

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

            # Select one transmission mode by commenting the other line.
            read_mode = "all"
            # read_mode = "range"

            if read_mode == "all":
                raw_data = self.read_all_data(
                    expected_samples=expected_samples,
                    read_chunk_samples=read_chunk_samples,
                    read_empty_retry_limit=read_empty_retry_limit,
                )
                received_samples = self._save_raw_measure_data(
                    output_file=output_file,
                    raw_data=raw_data,
                )
            elif read_mode == "range":
                # Range transmission streams binary data without an OK response.
                # If one chunk is short, only that range is requested again.
                received_samples = self.read_range_to_file(
                    output_file=output_file,
                    expected_samples=expected_samples,
                    range_chunk_samples=range_chunk_samples,
                    range_retry_attempts=range_retry_attempts,
                    read_empty_retry_limit=read_empty_retry_limit,
                    retry_delay_sec=range_retry_delay_sec,
                )
            else:
                raise ValueError("unsupported ADC read_mode: %s" % read_mode)
        else:
            received_samples = self.read_exact_to_file(
                output_file=output_file,
                expected_samples=expected_samples,
                read_chunk_samples=read_chunk_samples,
                read_empty_retry_limit=read_empty_retry_limit,
            )

        return received_samples

