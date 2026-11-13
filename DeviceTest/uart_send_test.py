"""UART 송신 테스트.

지정한 시리얼 포트를 열고 테스트 데이터를 주기적으로 보낸다.
수신된 바이트가 있으면 함께 출력한다(TX-RX 점퍼 시 루프백 확인 가능).

사용 예:
    python3 uart_send_test.py                       # /dev/ttyAMA2, 9600bps, 1초마다 "Hello", Ctrl+C 까지
    python3 uart_send_test.py --count 10            # 10회만
    python3 uart_send_test.py --listen              # 수신만 (송신 안 함)
    python3 uart_send_test.py --data "HELLO"
    python3 uart_send_test.py --hex "55 AA 01 02"
"""

import argparse
import sys
import time

import serial


def parse_args():
    p = argparse.ArgumentParser(description="UART send test")
    p.add_argument("--port", default="/dev/ttyAMA2", help="serial port (default: %(default)s)")
    p.add_argument("--baud", type=int, default=9600, help="baudrate (default: %(default)s)")
    p.add_argument("--count", type=int, default=0, help="send count, 0 = forever (default: %(default)s)")
    p.add_argument("--interval", type=float, default=1.0, help="seconds between sends (default: %(default)s)")
    p.add_argument("--listen", action="store_true", help="receive only, do not transmit")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--data", help="ASCII payload; '{n}' is replaced with sequence number")
    g.add_argument("--hex", help="hex payload, e.g. '55 AA 01'")
    return p.parse_args()


def build_payload(args, n):
    if args.hex:
        return bytes.fromhex(args.hex)
    text = args.data if args.data is not None else "Hello\r\n"
    return text.replace("{n}", str(n)).encode("ascii")


def hexdump(data):
    return " ".join(f"{b:02X}" for b in data)


def main():
    args = parse_args()
    try:
        ser = serial.Serial(args.port, args.baud, timeout=0)
    except serial.SerialException as err:
        print(f"[FAIL] open {args.port}: {err}")
        return 1

    print(f"[OK] opened {args.port} @ {args.baud} 8N1")
    ser.reset_input_buffer()
    tx_total = rx_total = 0
    n = 0
    try:
        while args.count == 0 or n < args.count:
            n += 1
            if not args.listen:
                payload = build_payload(args, n)
                ser.write(payload)
                ser.flush()
                tx_total += len(payload)
                print(f"TX[{n}] {len(payload):3d}B : {hexdump(payload)}  {payload!r}")

            deadline = time.monotonic() + args.interval
            while time.monotonic() < deadline:
                rx = ser.read(ser.in_waiting or 1)
                if rx:
                    rx_total += len(rx)
                    print(f"RX     {len(rx):3d}B : {hexdump(rx)}  {rx!r}")
                else:
                    time.sleep(0.01)
    except KeyboardInterrupt:
        print("\n[STOP] interrupted")
    finally:
        ser.close()

    print(f"[DONE] sent {n} frames, TX {tx_total}B, RX {rx_total}B")
    return 0


if __name__ == "__main__":
    sys.exit(main())
