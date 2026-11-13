"""새 POWER 보드(ESR_DSP) Modbus RTU 통신 계층.

규격: docs/power-board-dsp-modbus.md
  - RS485, 9600bps 8E1, 슬레이브 주소 1
  - FC03/FC04/FC06/FC10, CRC-16/Modbus(하위 바이트 먼저), float32 ABCD
  - 요청 간 최소 5ms, 응답 제한 100ms, 요청 프레임은 한 번의 write 로 전송

이 모듈은 프레임 송수신과 레지스터 인코딩만 담당한다. 운전 정책은 ERS_Power_Control.py.
여러 스레드(asyncio.to_thread 작업, 통신 유지 스레드)가 공유하므로 모든 요청은 잠금으로 직렬화한다.
pyserial 만 사용한다. Python 3.9 호환.
"""

import logging
import math
import struct
import threading
import time
from dataclasses import dataclass

import serial

BAUDRATE = 9600
FRAME_GAP_SEC = 0.005        # t3.5(4.011ms) 보다 크게
RESPONSE_TIMEOUT_SEC = 0.1

# 레지스터 주소 (0 기준 PDU 주소)
FC04_COUNT = 16              # 측정 영역
FC03_COUNT = 8               # 설정 영역 0~7 (주소 8 은 명령 전용, 읽으면 항상 0)
REG_SECONDS = 6
REG_RUN = 7
REG_TRIP_RESET = 8

STATE_STOP = 0
STATE_RUN = 1
STATE_TRIP = 2
STATE_NAMES = {STATE_STOP: "STOP", STATE_RUN: "RUN", STATE_TRIP: "TRIP"}

TRIP_NONE = 0
TRIP_NAMES = {0: "NONE", 1: "TIMER1_INT_ERROR", 2: "ADC_INT_ERROR",
              3: "OVER_CURRENT", 65535: "UNKNOWN"}

EXCEPTION_NAMES = {1: "ILLEGAL_FUNCTION", 2: "ILLEGAL_ADDRESS",
                   3: "ILLEGAL_VALUE", 4: "STATE_REJECTED"}


class DspCommError(Exception):
    """응답 없음, CRC 오류, 형식 오류 등 통신 실패."""


class DspException(Exception):
    """DSP 가 Modbus 예외 응답을 보냄."""

    def __init__(self, function, code):
        self.function = function
        self.code = code
        super().__init__("DSP exception {:02X} ({}) for FC{:02X}".format(
            code, EXCEPTION_NAMES.get(code, "UNKNOWN"), function))


@dataclass(frozen=True)
class Measurements:
    """FC04 측정 영역."""
    input_voltage: float     # 0~1  DCPT1 LPF [V]
    voltage: float           # 2~3  DCPT2 LPF, 출력 전압 [V]
    current: float           # 4~5  DCCT2 LPF, 출력 전류 [A]
    state: int               # 6    0=STOP 1=RUN 2=TRIP
    trip: int                # 7    트립 원인
    oc_trip: int             # 8    과전류 트립 여부
    elapsed: int             # 9    운전 경과 [s]
    vbat: float              # 10~11 [V]
    p12v: float              # 12~13 [V]
    peak: float              # 14~15 RUN 중 출력 전류 최댓값 [A]

    @property
    def state_name(self):
        return STATE_NAMES.get(self.state, "UNKNOWN({})".format(self.state))

    @property
    def trip_name(self):
        return TRIP_NAMES.get(self.trip, "UNKNOWN({})".format(self.trip))

    def summary(self):
        return ("state={} trip={} out={:.3f}V {:.4f}A peak={:.4f}A in={:.3f}V elapsed={}s "
                "vbat={:.3f}V p12v={:.3f}V").format(
            self.state_name, self.trip_name, self.voltage, self.current, self.peak,
            self.input_voltage, self.elapsed, self.vbat, self.p12v)


@dataclass(frozen=True)
class Settings:
    """FC03 설정 영역 0~7."""
    voltage: float           # 0~1 출력 전압 지령 [V]
    current: float           # 2~3 출력 전류 지령 [A]
    oc: float                # 4~5 OC 임계값 [A]
    seconds: int             # 6   운전 제한 시간 [s]
    run: int                 # 7   현재 RUN 여부


def crc16(data):
    value = 0xFFFF
    for byte in data:
        value ^= byte
        for _ in range(8):
            value = (value >> 1) ^ (0xA001 if value & 1 else 0)
    return struct.pack("<H", value)


def hexs(data):
    return " ".join("{:02X}".format(b) for b in data)


def decode_measurements(data):
    if len(data) != FC04_COUNT * 2:
        raise DspCommError("FC04 data length {} (expected 32)".format(len(data)))
    vin, vout, iout = struct.unpack(">fff", data[0:12])
    state, trip, oc, elapsed = struct.unpack(">HHHH", data[12:20])
    vbat, p12v, peak = struct.unpack(">fff", data[20:32])
    return Measurements(vin, vout, iout, state, trip, oc, elapsed, vbat, p12v, peak)


def decode_settings(data):
    if len(data) != FC03_COUNT * 2:
        raise DspCommError("FC03 data length {} (expected 16)".format(len(data)))
    voltage, current, oc = struct.unpack(">fff", data[0:12])
    seconds, run = struct.unpack(">HH", data[12:16])
    return Settings(voltage, current, oc, seconds, run)


def encode_settings(voltage, current, oc, seconds):
    for name, value in (("voltage", voltage), ("current", current), ("oc", oc)):
        if not math.isfinite(value):
            raise ValueError("{} is not finite: {}".format(name, value))
    return struct.pack(">fffH", float(voltage), float(current), float(oc), int(seconds))


def _expected_length(resp):
    if len(resp) < 2:
        return None
    if resp[1] & 0x80:
        return 5
    if resp[1] in (3, 4):
        return 5 + resp[2] if len(resp) >= 3 else None
    return 8


class DspModbusClient:
    def __init__(self, port, address=1, logger=None):
        self.port = port
        self.address = address
        self.logger = logger or logging.getLogger(__name__)
        self._lock = threading.Lock()
        self._last_end = 0.0
        self.ser = serial.Serial(port, BAUDRATE, bytesize=8, parity=serial.PARITY_EVEN,
                                 stopbits=1, timeout=0.01, write_timeout=0.1)
        self.logger.info("DSP Modbus opened: %s @ %d 8E1, addr %d", port, BAUDRATE, address)

    def close(self):
        with self._lock:
            if self.ser.is_open:
                self.ser.close()
                self.logger.info("DSP Modbus closed: %s", self.port)

    def exchange(self, pdu):
        """PDU 를 보내고 검증된 응답 프레임을 돌려준다. 실패하면 DspCommError/DspException."""
        with self._lock:
            return self._exchange_locked(list(pdu))

    def _exchange_locked(self, pdu):
        wait = FRAME_GAP_SEC - (time.monotonic() - self._last_end)
        if wait > 0:
            time.sleep(wait)
        body = bytes([self.address] + pdu)
        packet = body + crc16(body)
        resp = bytearray()
        try:
            self.ser.reset_input_buffer()
            self.ser.write(packet)
            deadline = time.monotonic() + RESPONSE_TIMEOUT_SEC
            while time.monotonic() < deadline:
                need = _expected_length(resp)
                if need is not None and len(resp) >= need:
                    break
                resp.extend(self.ser.read(need - len(resp) if need else 1))
        except serial.SerialException as err:
            raise DspCommError("serial error: {!r}".format(err)) from err
        finally:
            self._last_end = time.monotonic()

        self.logger.debug("DSP TX %s / RX %s", hexs(packet), hexs(resp) or "-")
        need = _expected_length(resp)
        if need is None or len(resp) < need:
            raise DspCommError("no or incomplete response ({}B) for {}".format(len(resp), hexs(packet)))
        resp = bytes(resp[:need])
        if resp[-2:] != crc16(resp[:-2]):
            raise DspCommError("CRC error: {}".format(hexs(resp)))
        if resp[0] != self.address or resp[1] & 0x7F != pdu[0]:
            raise DspCommError("address/function mismatch: {}".format(hexs(resp)))
        if resp[1] & 0x80:
            raise DspException(pdu[0], resp[2])
        if pdu[0] in (6, 16) and resp[2:6] != bytes(pdu[1:5]):
            raise DspCommError("write echo mismatch: {}".format(hexs(resp)))
        return resp

    # --- 읽기 ---------------------------------------------------------------

    def read_measurements(self):
        resp = self.exchange([4, 0, 0, 0, FC04_COUNT])
        return decode_measurements(resp[3:-2])

    def read_settings(self):
        resp = self.exchange([3, 0, 0, 0, FC03_COUNT])
        return decode_settings(resp[3:-2])

    # --- 쓰기 ---------------------------------------------------------------

    def write_settings(self, voltage, current, oc, seconds):
        """FC10 시작 0, 개수 7: 전압·전류·OC·제한시간을 원자적으로 적용. STOP 에서만 허용."""
        data = encode_settings(voltage, current, oc, seconds)
        self.exchange([16, 0, 0, 0, 7, len(data)] + list(data))

    def write_run(self, run):
        self.exchange([6, 0, REG_RUN, 0, 1 if run else 0])

    def write_trip_reset(self):
        self.exchange([6, 0, REG_TRIP_RESET, 0, 1])
