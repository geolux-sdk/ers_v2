"""[레거시] 기존(v2) 하드웨어용 GPIO / UART 점검 스크립트.

주의 — 2027Version 하드웨어에서는 핀 정의가 맞지 않는다 (docs/pi-setup-2027.md 1절):
  - GPIO23 은 이제 ADC_RESET 이다. 이 스크립트는 23 을 2초마다 토글하고 끝날 때 LOW 로 두므로
    ADC 가 반복 리셋된다.
  - GPIO22 는 이제 릴레이 전원 출력인데, 여기서는 입력으로 읽는다.
  - GPIO20(부스터), 45(기존 릴레이 전원)는 2027Version 에서 쓰지 않는다.
  - /dev/ttyAMA2 는 이제 POWER 보드 RS485(9600 8E1)인데, 여기서는 115200 8N1 로 데이터를 보낸다.
  - GPIO21(POWER_HOLD)은 건드리지 않는다.
2027Version GPIO 확인은 raspi-gpio 를 쓴다 (docs/pi-setup-2027.md 6절).
"""
import time
import RPi.GPIO as GPIO
import serial

# =========================
# GPIO 설정
# =========================
GPIO_OUT_PINS = [20, 23, 24, 45]
GPIO_IN_PIN = 22

GPIO.setmode(GPIO.BCM)

# 출력 핀 설정
for pin in GPIO_OUT_PINS:
    GPIO.setup(pin, GPIO.OUT)
    GPIO.output(pin, False)

# 입력 핀 설정
GPIO.setup(GPIO_IN_PIN, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)

print("GPIO INIT DONE")

# =========================
# UART 설정
# =========================
UART_PORTS = {
    "UART3": "/dev/ttyAMA2",
    "UART4": "/dev/ttyAMA3",
}

serial_ports = {}

for name, port in UART_PORTS.items():
    try:
        ser = serial.Serial(port=port, baudrate=115200, timeout=0.5)
        serial_ports[name] = ser
        print(f"[OK] {name} OPEN: {port}")
    except Exception as e:
        print(f"[FAIL] {name} ({port}): {e}")

print("\n===== TEST START =====\n")

state = False
counter = 0

try:
    while True:
        counter += 1

        # =========================
        # GPIO 출력 토글
        # =========================
        state = not state

        for pin in GPIO_OUT_PINS:
            GPIO.output(pin, state)

        print("\n==============================")
        print(f"GPIO OUTPUT STATE = {'ON' if state else 'OFF'}")

        for pin in GPIO_OUT_PINS:
            print(f"GPIO{pin} -> {'HIGH' if state else 'LOW'}")

        # =========================
        # GPIO22 입력 읽기
        # =========================
        input_val = GPIO.input(GPIO_IN_PIN)
        print(f"GPIO22 INPUT -> {'HIGH' if input_val else 'LOW'}")

        # =========================
        # UART 송신
        # =========================
        for name, ser in serial_ports.items():
            msg = f"[{name}] TEST {counter}\r\n"
            try:
                ser.write(msg.encode())
                print(f"TX {name} -> {msg.strip()}")
            except serial.SerialException as e:
                print(f"[FAIL] TX {name}: {e}")

        # 수신 대기
        time.sleep(0.2)

        # =========================
        # UART 수신
        # =========================
        for name, ser in serial_ports.items():
            try:
                while ser.in_waiting:
                    data = ser.readline().decode(errors="ignore").strip()
                    print(f"RX {name} -> {data}")
            except serial.SerialException as e:
                print(f"[FAIL] RX {name}: {e}")

        print("==============================")

        time.sleep(2)

except KeyboardInterrupt:
    print("\n종료")

finally:
    for ser in serial_ports.values():
        try:
            ser.close()
        except serial.SerialException as e:
            print(f"[FAIL] serial close: {e}")

    for pin in GPIO_OUT_PINS:
        try:
            GPIO.output(pin, False)
        except RuntimeError as e:
            print(f"[FAIL] GPIO{pin} LOW: {e}")

    try:
        GPIO.cleanup()
    except RuntimeError as e:
        print(f"[FAIL] GPIO cleanup: {e}")
    print("정리 완료")
