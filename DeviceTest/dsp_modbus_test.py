"""새 POWER 보드(ESR_DSP) Modbus RTU 통신 테스트.

규격: docs/power-board-dsp-modbus.md (9600bps 8E1, 주소 1, float32 ABCD)
pyserial만 사용한다. Python 3.9 호환.

사용 예 (Pi):
    python3 dsp_modbus_test.py read                 # FC04 측정 + FC03 설정 1회
    python3 dsp_modbus_test.py watch                # FC04 를 1초마다 (Ctrl+C 종료)
    python3 dsp_modbus_test.py set 24 0.5 0.75 30   # FC10: 전압 V, 전류 A, OC A, 제한시간 s
    python3 dsp_modbus_test.py run                  # FC06 주소7 = 1
    python3 dsp_modbus_test.py stop                 # FC06 주소7 = 0
    python3 dsp_modbus_test.py reset                # FC06 주소8 = 1, TRIP 해제
    python3 dsp_modbus_test.py raw "04 00 00 00 10" # 주소와 CRC 를 붙여 그대로 전송
    python3 dsp_modbus_test.py selftest             # 장치 없이 CRC/인코딩 확인

주의:
  - set/run 은 실제로 출력 전압을 낸다. 부하와 배선을 확인한 뒤 사용한다.
  - 쓰기와 RUN 은 자동 재시도하지 않는다(펌웨어 규약).
  - RUN 중 10초 동안 정상 요청이 없으면 DSP 가 스스로 STOP 한다. run 뒤에는 watch 로 유지한다.
"""

import argparse
import math
import struct
import sys
import time

DEFAULT_PORT = "/dev/ttyAMA1"
DEFAULT_ADDR = 1
BAUD = 9600
FRAME_GAP_S = 0.005        # 요청 간 최소 간격 (t3.5 = 4.011ms 보다 크게)
RESPONSE_TIMEOUT_S = 0.1   # 응답 제한 시간

STATE_NAMES = {0: "STOP", 1: "RUN", 2: "TRIP"}
TRIP_NAMES = {0: "없음", 1: "TIMER1 인터럽트 오류", 2: "ADC 인터럽트 오류",
              3: "과전류(OC)", 65535: "알 수 없음"}
EXCEPTION_NAMES = {1: "지원하지 않는 기능", 2: "잘못된 주소",
                   3: "잘못된 값", 4: "현재 상태에서 실행 불가"}
LIMITS = {"voltage": (1.0, 300.0), "current": (0.1, 1.0), "oc": (0.3, 1.5)}


# ---------------------------------------------------------------------------
# 프레임
# ---------------------------------------------------------------------------

def crc16(data):
    value = 0xFFFF
    for byte in data:
        value ^= byte
        for _ in range(8):
            value = (value >> 1) ^ (0xA001 if value & 1 else 0)
    return struct.pack("<H", value)


def hexs(data):
    return " ".join("{:02X}".format(b) for b in data) if data else "(없음)"


class ModbusError(Exception):
    pass


class ModbusException(ModbusError):
    """장치가 예외 응답을 보냄."""

    def __init__(self, function, code):
        self.function = function
        self.code = code
        super().__init__("예외 {:02X} (FC{:02X}): {}".format(
            code, function, EXCEPTION_NAMES.get(code, "장치 오류")))


def expected_length(resp):
    """지금까지 받은 바이트로 전체 응답 길이를 계산. 모르면 None."""
    if len(resp) < 2:
        return None
    if resp[1] & 0x80:
        return 5
    if resp[1] in (3, 4):
        return 5 + resp[2] if len(resp) >= 3 else None
    return 8  # FC06, FC10


# ---------------------------------------------------------------------------
# 클라이언트
# ---------------------------------------------------------------------------

class DspClient:
    def __init__(self, port, address=DEFAULT_ADDR, verbose=True):
        import serial  # selftest 는 pyserial 없이도 돌도록 여기서 import

        self.address = address
        self.verbose = verbose
        self.ser = serial.Serial(port, BAUD, bytesize=8, parity=serial.PARITY_EVEN,
                                 stopbits=1, timeout=0.01, write_timeout=0.1)
        self.last_end = 0.0

    def close(self):
        self.ser.close()

    def exchange(self, pdu):
        wait = FRAME_GAP_S - (time.monotonic() - self.last_end)
        if wait > 0:
            time.sleep(wait)
        self.ser.reset_input_buffer()
        body = bytes([self.address]) + bytes(pdu)
        packet = body + crc16(body)
        if self.verbose:
            print("  TX {}".format(hexs(packet)))
        resp = bytearray()
        try:
            self.ser.write(packet)  # 한 번에 써야 프레임 중간 간격이 생기지 않는다
            deadline = time.monotonic() + RESPONSE_TIMEOUT_S
            while time.monotonic() < deadline:
                need = expected_length(resp)
                if need is not None and len(resp) >= need:
                    break
                resp.extend(self.ser.read(need - len(resp) if need else 1))
        finally:
            self.last_end = time.monotonic()
        if self.verbose:
            print("  RX {}".format(hexs(resp)))

        need = expected_length(resp)
        if need is None or len(resp) < need:
            raise ModbusError("응답 없음 또는 불완전 ({}B, {}ms)".format(
                len(resp), int(RESPONSE_TIMEOUT_S * 1000)))
        resp = bytes(resp[:need])
        if resp[-2:] != crc16(resp[:-2]):
            raise ModbusError("CRC 오류")
        if resp[0] != self.address or resp[1] & 0x7F != pdu[0]:
            raise ModbusError("주소 또는 기능 코드 불일치")
        if resp[1] & 0x80:
            raise ModbusException(pdu[0], resp[2])
        if pdu[0] in (6, 16) and resp[2:6] != bytes(pdu[1:5]):
            raise ModbusError("쓰기 응답 내용 불일치")
        return resp

    # --- 읽기 ---------------------------------------------------------------

    def read_measurements(self):
        resp = self.exchange([4, 0, 0, 0, 16])
        return decode_measurements(resp[3:-2])

    def read_settings(self):
        resp = self.exchange([3, 0, 0, 0, 8])
        return decode_settings(resp[3:-2])

    # --- 쓰기 ---------------------------------------------------------------

    def write_settings(self, voltage, current, oc, seconds):
        data = encode_settings(voltage, current, oc, seconds)
        self.exchange([16, 0, 0, 0, 7, len(data)] + list(data))

    def command(self, value):
        self.exchange([6, 0, 7, 0, value])

    def trip_reset(self):
        """FC06 주소 8 = 1. 펌웨어에 TRIP 해제 레지스터가 추가된 뒤에만 동작(이전 펌웨어는 예외 02)."""
        self.exchange([6, 0, 8, 0, 1])


# ---------------------------------------------------------------------------
# 인코딩 / 디코딩
# ---------------------------------------------------------------------------

def decode_measurements(data):
    if len(data) != 32:
        raise ModbusError("FC04 데이터 길이 {}B (32B 기대)".format(len(data)))
    vin, vout, iout = struct.unpack(">fff", data[0:12])
    state, trip, oc, elapsed = struct.unpack(">HHHH", data[12:20])
    vbat, p12v, peak = struct.unpack(">fff", data[20:32])
    return dict(input_voltage=vin, voltage=vout, current=iout, state=state, trip=trip,
                oc_trip=oc, elapsed=elapsed, vbat=vbat, p12v=p12v, peak=peak)


def decode_settings(data):
    if len(data) != 16:
        raise ModbusError("FC03 데이터 길이 {}B (16B 기대)".format(len(data)))
    voltage, current, oc = struct.unpack(">fff", data[0:12])
    seconds, run = struct.unpack(">HH", data[12:16])
    return dict(voltage=voltage, current=current, oc=oc, seconds=seconds, run=run)


def encode_settings(voltage, current, oc, seconds):
    values = {"voltage": voltage, "current": current, "oc": oc}
    for key, value in values.items():
        low, high = LIMITS[key]
        if not math.isfinite(value) or not low <= value <= high:
            raise ValueError("{} = {} : 허용 범위 {}~{}".format(key, value, low, high))
    if seconds != int(seconds) or not 1 <= seconds <= 9999:
        raise ValueError("seconds = {} : 허용 범위 1~9999 정수".format(seconds))
    return struct.pack(">fffH", float(voltage), float(current), float(oc), int(seconds))


def print_measurements(m):
    print("  상태 {} ({})  트립 {} ({})  OC트립 {}  경과 {}s".format(
        m["state"], STATE_NAMES.get(m["state"], "?"),
        m["trip"], TRIP_NAMES.get(m["trip"], "?"), m["oc_trip"], m["elapsed"]))
    print("  입력 {:.3f}V  출력 {:.3f}V  {:.4f}A  (peak {:.4f}A)  VBAT {:.3f}V  P12V {:.3f}V".format(
        m["input_voltage"], m["voltage"], m["current"], m["peak"], m["vbat"], m["p12v"]))


def print_settings(s):
    print("  지령 {:.3f}V  {:.4f}A  OC {:.4f}A  제한 {}s  RUN={}".format(
        s["voltage"], s["current"], s["oc"], s["seconds"], s["run"]))


# ---------------------------------------------------------------------------
# 명령
# ---------------------------------------------------------------------------

def cmd_read(client, args):
    print("[FC04 측정]")
    print_measurements(client.read_measurements())
    print("[FC03 설정]")
    print_settings(client.read_settings())


def cmd_watch(client, args):
    n = 0
    try:
        while args.count == 0 or n < args.count:
            n += 1
            print("[{}] {}".format(n, time.strftime("%H:%M:%S")))
            try:
                print_measurements(client.read_measurements())
            except ModbusError as err:
                print("  [FAIL] {}".format(err))
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n[STOP] interrupted")


def cmd_set(client, args):
    print("[FC10 설정 쓰기] {}V {}A OC {}A {}s".format(args.voltage, args.current, args.oc, args.seconds))
    client.write_settings(args.voltage, args.current, args.oc, args.seconds)
    print("[FC03 확인]")
    s = client.read_settings()
    print_settings(s)
    for key in ("voltage", "current", "oc"):
        if abs(s[key] - getattr(args, key)) > 1e-4:
            print("  [WARN] {} 불일치: 쓴 값 {} / 읽은 값 {}".format(key, getattr(args, key), s[key]))


def cmd_run(client, args):
    print("[FC06 RUN]")
    client.command(1)
    print_measurements(client.read_measurements())
    print("  ※ RUN 중 10초 무통신이면 DSP 가 STOP 한다. 'watch' 로 유지할 것.")


def cmd_stop(client, args):
    print("[FC06 STOP]")
    client.command(0)
    print_measurements(client.read_measurements())


def cmd_reset(client, args):
    print("[FC04 해제 전 상태]")
    print_measurements(client.read_measurements())
    print("[FC06 TRIP 해제]")
    client.trip_reset()
    print("[FC04 해제 후 상태]")
    print_measurements(client.read_measurements())


def cmd_raw(client, args):
    pdu = list(bytes.fromhex(args.hex))
    print("[RAW] PDU {}".format(hexs(pdu)))
    client.exchange(pdu)


def cmd_selftest(args):
    """장치 없이 문서의 예시 프레임과 인코딩이 맞는지 확인."""
    cases = [
        ("FC04 read", "01 04 00 00 00 10", "F1 C6"),
        ("FC03 read", "01 03 00 00 00 08", "44 0C"),
        ("FC10 write", "01 10 00 00 00 07 0E " + hexs(encode_settings(24.0, 0.5, 0.75, 30)), "F1 28"),
        ("FC10 resp", "01 10 00 00 00 07", "81 CB"),
        ("FC06 30s", "01 06 00 06 00 1E", "E9 C3"),
        ("RUN", "01 06 00 07 00 01", "F9 CB"),
        ("STOP", "01 06 00 07 00 00", "38 0B"),
        ("exc 04", "01 90 04", "4D C3"),
        ("TRIP reset", "01 06 00 08 00 01", "C9 C8"),
    ]
    ok = True
    for name, body, want in cases:
        got = hexs(crc16(bytes.fromhex(body)))
        good = got == want
        ok &= good
        print("  {} {:11s} {} | CRC {}".format("OK  " if good else "FAIL", name, body, got))

    sample = struct.pack(">fffHHHHfff", 30.1, 24.0, 0.5, 1, 0, 0, 12, 24.2, 12.1, 0.55)
    m = decode_measurements(sample)
    good = m["state"] == 1 and m["elapsed"] == 12 and abs(m["voltage"] - 24.0) < 1e-6
    ok &= good
    print("  {} FC04 decode".format("OK  " if good else "FAIL"))
    try:
        encode_settings(301, 0.5, 0.75, 30)
        ok = False
        print("  FAIL range check")
    except ValueError:
        print("  OK   range check")
    print("[selftest] {}".format("PASS" if ok else "FAIL"))
    return 0 if ok else 1


def parse_args():
    p = argparse.ArgumentParser(description="ESR_DSP POWER board Modbus RTU test (9600 8E1)")
    p.add_argument("--port", default=DEFAULT_PORT, help="serial port (default: %(default)s)")
    p.add_argument("--addr", type=int, default=DEFAULT_ADDR, help="slave address (default: %(default)s)")
    p.add_argument("-q", "--quiet", action="store_true", help="TX/RX 프레임 출력 안 함")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("read", help="FC04 측정 + FC03 설정 읽기")
    w = sub.add_parser("watch", help="FC04 반복 읽기")
    w.add_argument("--interval", type=float, default=1.0)
    w.add_argument("--count", type=int, default=0, help="0 = Ctrl+C 까지")
    s = sub.add_parser("set", help="FC10 설정 쓰기 (STOP 상태에서만)")
    s.add_argument("voltage", type=float, help="출력 전압 V (1~300)")
    s.add_argument("current", type=float, help="출력 전류 A (0.1~1)")
    s.add_argument("oc", type=float, help="OC 임계값 A (0.3~1.5)")
    s.add_argument("seconds", type=int, help="운전 제한 시간 s (1~9999)")
    sub.add_parser("run", help="FC06 RUN")
    sub.add_parser("stop", help="FC06 STOP")
    sub.add_parser("reset", help="FC06 주소8 TRIP 해제")
    r = sub.add_parser("raw", help="PDU 직접 전송 (주소와 CRC 자동)")
    r.add_argument("hex", help='예: "04 00 00 00 10"')
    sub.add_parser("selftest", help="장치 없이 CRC/인코딩 확인")
    return p.parse_args()


def main():
    args = parse_args()
    if args.cmd == "selftest":
        return cmd_selftest(args)

    handlers = {"read": cmd_read, "watch": cmd_watch, "set": cmd_set,
                "run": cmd_run, "stop": cmd_stop, "reset": cmd_reset, "raw": cmd_raw}
    try:
        client = DspClient(args.port, args.addr, verbose=not args.quiet)
    except Exception as err:
        print("[FAIL] open {}: {}".format(args.port, err))
        return 1
    print("[OK] {} @ {} 8E1, addr {}".format(args.port, BAUD, args.addr))
    try:
        handlers[args.cmd](client, args)
    except (ModbusError, ValueError) as err:
        print("[FAIL] {}".format(err))
        return 1
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
