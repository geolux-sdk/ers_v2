#!/bin/bash
# ERS 2027Version: /boot/config.txt 를 ERS 용으로 고친다.
# 대상: Raspberry Pi Compute Module 4S + 캐리어 보드 CPi-S102WR (공장 설정 상태)
#
#   sudo bash ~/ers_v2/system/pi-config-cm4s.sh            # /boot/config.txt 를 고친다
#   bash ~/ers_v2/system/pi-config-cm4s.sh /tmp/copy.txt   # 다른 파일로 시험
#
# 하는 일 (절차와 이유: docs/pi-setup-cm4s-procedure.md, docs/pi-setup-2027.md 2절)
#   1. UART3(GPIO4/5, POWER 보드), UART5(GPIO12/13, 릴레이 보드) 를 켠다
#   2. 공장 설정 gpio=17-27=op,np,dl (전부 출력 LOW) 을 입력(풀 없음)으로 바꾸고
#      21=HIGH(POWER_HOLD) 22=LOW(RELAY) 23=HIGH(ADC_RESET) 24=LOW(TEST_MODE) 만 출력으로 잡는다
#
# 기종이 다르거나 공장 설정의 기준 줄이 없으면 아무것도 바꾸지 않고 끝낸다.
# 이미 적용돼 있으면 다시 적용하지 않는다. 바꾸기 전에 <파일>.bak-<날짜시각> 으로 백업한다.
# 줄바꿈(CRLF/LF)은 원본을 따른다. 적용하려면 재부팅해야 한다.
set -euo pipefail

CFG="${1:-/boot/config.txt}"
EXPECT_MODEL="Raspberry Pi Compute Module 4S"
EXPECT_BOARD="CPi-S102WR"

MODEL="$(tr -d '\0' < /proc/device-tree/model)"
case "$MODEL" in
    "$EXPECT_MODEL"*) ;;
    *) echo "STOP: 기종이 다르다: '$MODEL' (기대: $EXPECT_MODEL)" >&2; exit 1 ;;
esac
if ! head -n 1 "$CFG" | grep -q "$EXPECT_BOARD"; then
    echo "STOP: $CFG 첫 줄에 $EXPECT_BOARD 가 없다. 캐리어 보드가 다르거나 공장 설정이 아니다." >&2
    exit 1
fi

python3 - "$CFG" <<'EOF'
import shutil
import sys
import time

path = sys.argv[1]
data = open(path, "rb").read()
nl = b"\r\n" if b"\r\n" in data else b"\n"


def lines(*items):
    return b"".join(item + nl for item in items)


EDITS = [
    (
        "UART3, UART5",
        b"dtoverlay=uart3",                                   # 이미 적용됐는지 보는 줄
        lines(b"dtoverlay=uart1,txd1_pin=32,rxd1_pin=33"),    # 공장 설정의 기준 줄
        lines(
            b"dtoverlay=uart1,txd1_pin=32,rxd1_pin=33",
            b"# 2027Version: UART3(GPIO4/5)=POWER board RS485, UART5(GPIO12/13)=RELAY board",
            b"dtoverlay=uart3",
            b"dtoverlay=uart5",
        ),
    ),
    (
        "GPIO 17-27",
        b"gpio=21=op,dh",
        lines(b"gpio=17-27=op,np,dl"),
        lines(
            b"# 2027Version: factory default was gpio=17-27=op,np,dl (all outputs LOW).",
            b"# Unused pins must not drive the board: inputs, no pull. 21-24 are set as outputs below.",
            b"gpio=17-27=ip,np",
            b"# 2027Version: 21=POWER_HOLD(HIGH keep, LOW cuts power) 22=RELAY 23=ADC_RESET 24=TEST_MODE",
            b"gpio=21=op,dh",
            b"gpio=22=op,dl",
            b"gpio=23=op,dh",
            b"gpio=24=op,dl",
        ),
    ),
]

new = data
todo = []
for name, marker, old, replacement in EDITS:
    if (nl + marker + nl) in (nl + new):
        print("skip  {}: 이미 적용됨".format(name))
        continue
    if new.count(old) != 1:
        sys.exit("STOP: {} 의 기준 줄을 찾지 못했다: {!r}. 아무것도 바꾸지 않았다.".format(
            name, old.strip().decode()))
    new = new.replace(old, replacement)
    todo.append(name)

if not todo:
    print("변경 없음")
    sys.exit(0)

backup = "{}.bak-{}".format(path, time.strftime("%Y%m%d-%H%M%S"))
shutil.copyfile(path, backup)
open(path, "wb").write(new)
for name in todo:
    print("apply {}".format(name))
print("백업: {}".format(backup))
print("재부팅해야 적용된다: sudo reboot")
EOF
