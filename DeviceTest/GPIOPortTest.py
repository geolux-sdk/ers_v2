"""2027Version GPIO / POWER 보드 통신 점검 스크립트.

핀 정의: docs/pi-setup-2027.md 1절 (pi_gpio.py 와 같음)
  GPIO21 POWER_HOLD  — 읽기만 한다. 이 스크립트는 절대 출력하지 않는다(LOW 가 되면 보드 전원 차단).
  GPIO22 RELAY_PWREN — 평상시 LOW
  GPIO23 ADC_RESET   — LOW = 리셋, 평상시 HIGH
  GPIO24 TEST_MODE   — 평상시 LOW

사용 예 (Pi, ~/ers_v2 에서):
  python3 DeviceTest/GPIOPortTest.py status                 # 21~24 상태
  python3 DeviceTest/GPIOPortTest.py toggle 24               # 2초 간격 5회 토글 후 평상시 레벨로
  python3 DeviceTest/GPIOPortTest.py toggle 22 --count 0     # Ctrl+C 까지
  python3 DeviceTest/GPIOPortTest.py adc-reset --pulse 0.1   # ADC 리셋 펄스 1회
  python3 DeviceTest/GPIOPortTest.py cycle                   # 22·24 토글 + POWER 보드(FC04) 읽기 반복

끝나면(Ctrl+C 포함) 22·24 LOW, 23 HIGH 로 되돌린다. GPIO.cleanup() 은 호출하지 않는다.
ERS(ers-v2 서비스)가 실행 중이면 같은 핀·포트를 쓰므로 먼저 멈춘다.
"""

import argparse
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pi_gpio  # noqa: E402

PIN_NAMES = {
    pi_gpio.POWER_HOLD: "POWER_HOLD",
    pi_gpio.RELAY_PWREN: "RELAY_PWREN",
    pi_gpio.ADC_RESET: "ADC_RESET",
    pi_gpio.TEST_MODE_PIN: "TEST_MODE",
}
TOGGLE_PINS = (pi_gpio.RELAY_PWREN, pi_gpio.TEST_MODE_PIN)


def read_pin(pin):
    """raspi-gpio 로 읽는다. RPi.GPIO 로 setup 하지 않으므로 핀 설정을 바꾸지 않는다."""
    try:
        out = subprocess.run(["raspi-gpio", "get", str(pin)], capture_output=True,
                             text=True, timeout=2).stdout.strip()
    except (OSError, subprocess.SubprocessError) as err:
        return "읽기 실패: {}".format(err)
    return out or "(응답 없음)"


def show_status():
    for pin, name in sorted(PIN_NAMES.items()):
        print("  GPIO{} {:11s} {}".format(pin, name, read_pin(pin)))


def set_pin(gpio, pin, level):
    if pin == pi_gpio.POWER_HOLD:
        raise SystemExit("GPIO21(POWER_HOLD)은 출력하지 않는다")
    pi_gpio.GPIO.output(pin, pi_gpio.GPIO.HIGH if level else pi_gpio.GPIO.LOW)
    print("  GPIO{} {} -> {}".format(pin, PIN_NAMES[pin], "HIGH" if level else "LOW"))


def read_power(client):
    if client is None:
        return
    try:
        m = client.read_measurements()
        print("  POWER " + m.summary())
    except Exception as err:  # noqa: BLE001
        print("  POWER 읽기 실패: {!r}".format(err))


def open_power(port):
    try:
        from dsp_modbus import DspModbusClient
        return DspModbusClient(port)
    except Exception as err:  # noqa: BLE001
        print("[WARN] POWER 보드 포트 열기 실패 ({}): {!r}".format(port, err))
        return None


def loop(count, interval, body):
    n = 0
    while count == 0 or n < count:
        n += 1
        print("[{}] {}".format(n, time.strftime("%H:%M:%S")))
        body(n)
        time.sleep(interval)


def main():
    p = argparse.ArgumentParser(description="2027Version GPIO / POWER check")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="GPIO 21~24 상태")
    t = sub.add_parser("toggle", help="22 또는 24 토글")
    t.add_argument("pin", type=int, choices=TOGGLE_PINS)
    t.add_argument("--count", type=int, default=5, help="0 = Ctrl+C 까지")
    t.add_argument("--interval", type=float, default=2.0)
    r = sub.add_parser("adc-reset", help="GPIO23 LOW 펄스")
    r.add_argument("--pulse", type=float, default=0.1, help="LOW 유지 시간(s)")
    c = sub.add_parser("cycle", help="22·24 토글 + POWER 보드 읽기 반복")
    c.add_argument("--count", type=int, default=0, help="0 = Ctrl+C 까지")
    c.add_argument("--interval", type=float, default=2.0)
    c.add_argument("--power-port", default="/dev/ttyAMA1")
    args = p.parse_args()

    if args.cmd == "status":
        show_status()
        return 0

    gpio = pi_gpio.GPIOController()     # 22 LOW, 23 HIGH, 24 LOW 로 시작 (21 은 건드리지 않음)
    client = None
    try:
        if args.cmd == "toggle":
            state = {"level": False}

            def body(_n):
                state["level"] = not state["level"]
                set_pin(gpio, args.pin, state["level"])
            loop(args.count, args.interval, body)

        elif args.cmd == "adc-reset":
            set_pin(gpio, pi_gpio.ADC_RESET, False)
            time.sleep(args.pulse)
            set_pin(gpio, pi_gpio.ADC_RESET, True)

        elif args.cmd == "cycle":
            client = open_power(args.power_port)

            def body(n):
                level = n % 2 == 1
                for pin in TOGGLE_PINS:
                    set_pin(gpio, pin, level)
                read_power(client)
            loop(args.count, args.interval, body)

    except KeyboardInterrupt:
        print("\n[STOP] interrupted")
    finally:
        gpio.close()                    # 평상시 레벨로 복귀
        if client is not None:
            client.close()
        print("[DONE] 평상시 레벨로 복귀")
        show_status()
    return 0


if __name__ == "__main__":
    sys.exit(main())
