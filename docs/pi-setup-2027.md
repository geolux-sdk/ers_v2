# ERS 2027Version Raspberry Pi 설정

최종 확인: 2026-11-24
대상 장비: Raspberry Pi Compute Module 4S Rev 1.0 (`raspberrypi`), 캐리어 보드 CPi-S102WR,
Raspberry Pi OS (Debian 11 bullseye), 커널 6.1.21-v8+, Python 3.9.2

이 문서는 2027Version 하드웨어의 Pi 설정 내용과 그 이유, 주의 사항을 설명한다.
각 항목 끝에 **확인 방법**과 **되돌리는 방법**을 적었다.

> **Pi를 새로 설정할 때는 [pi-setup-cm4s-procedure.md](pi-setup-cm4s-procedure.md)의 순서를 따른다.**
> 그 문서가 따라 하는 절차이고, 이 문서는 설명과 기록이다.

> 2026-11-24에 Pi를 CM4에서 **CM4S + CPi-S102WR**로 교체했다. 이 문서는 새 Pi 기준이다.
> 교체 전 Pi의 설정과 시험 기록은 10절에 남겼다.
>
> **2026-11-24 현재 새 Pi는 전원만 직접 연결한 상태다. ERS 보드(POWER·릴레이·ADC)는 아직 연결하지 않았다.**
> Pi 쪽 설정은 확인했지만 보드와의 통신은 확인하지 못했다. 남은 확인 항목은 9절에 있다.
>
> 기존(v2) 하드웨어 기준 설정은 [README.md](../README.md)의 "Raspberry Pi 부팅 설정"에 있다.
> 2027Version에서는 이 문서가 우선한다.

---

## 1. 하드웨어 연결 요약

### 1.1 ERS가 쓰는 핀

ERS가 쓰는 것은 **GPIO 21·22·23·24, UART3, UART5**뿐이다.
40핀 번호는 표준 라즈베리파이 40핀 배열 기준이다(CPi-S102WR 외부 커넥터와 대조 필요, 9절).

| 기능 | Pi 자원 | 40핀 | 방향 | 평상시 레벨 | 비고 |
|---|---|---|---|---|---|
| **POWER_HOLD** | GPIO21 | 40 | 출력 | **HIGH** | **LOW가 되면 보드 전원이 꺼진다** |
| 릴레이 전원 | GPIO22 | 15 | 출력 | LOW | HIGH = 동작 |
| ADC_RESET | GPIO23 | 16 | 출력 | HIGH | **LOW = ADC 리셋** |
| TEST 모드 | GPIO24 | 18 | 출력 | LOW | HIGH = TEST 모드 |
| POWER 보드 (ESR_DSP) | **`/dev/ttyAMA1`** (UART3, TX GPIO4 / RX GPIO5) | 7 / 29 | RS485 | — | Modbus RTU 9600 **8E1**, 주소 1. [power-board-dsp-modbus.md](power-board-dsp-modbus.md) |
| 릴레이 보드 통신 | **`/dev/ttyAMA2`** (UART5, TX GPIO12 / RX GPIO13) | 32 / 33 | UART 직결 | — | 115200 8N1. 교체 전에는 USB-시리얼(`/dev/ttyUSB0`)이었다 |
| ADC | `/dev/ttyACM0` | — | USB | — | 기존과 같음 |

- RS485 트랜시버는 **자동 방향 전환** 방식이다. Pi 쪽에서 방향 제어 핀을 다루지 않는다.
- **장치 이름 주의:** 교체 전 Pi에서 `/dev/ttyAMA2`는 POWER 보드였다. 새 Pi에서는 **릴레이**다(2.3절).

### 1.2 캐리어 보드가 기본으로 쓰는 핀 — 건드리지 않는다

| 기능 | GPIO | 공장 설정 |
|---|---|---|
| UART0 (`/dev/ttyAMA0`) — 보드의 RS485 | 14, 15 | `enable_uart=1` |
| I2C — RTC(DS3231) 등 | 2, 3 | `dtparam=i2c_arm=on`, `dtoverlay=i2c-rtc,ds3231` |
| UART1 (`/dev/ttyS0`) | 32, 33 | `dtoverlay=uart1,txd1_pin=32,rxd1_pin=33` |
| LCD 백라이트 PWM, 토글 | 31, 34 | `gpio=31=op,np,dh`, `gpio=34=op,np,dh` |
| ESD 레벨 시프터 enable | 37, 38, 39 | `gpio=37-39=op,np,dh` |
| 오디오 | 40, 41 | `dtoverlay=audremap,pins_40_41` |

이 줄들은 공장 설정 그대로 둔다. ERS 코드도 이 핀과 포트를 열지 않는다.

### 1.3 쓰지 않는 핀 — 입력으로 둔다

나머지 핀은 쓰지 않는다. **출력이면 안 되고 입력(OPEN) 상태여야 한다.**

| GPIO | 40핀 | 상태 | 비고 |
|---|---|---|---|
| 17, 18, 19, 20 | 11, 12, 35, 38 | 입력, 풀 없음 | 공장 설정은 출력 LOW였다 → 바꿈(2.2절) |
| 25, 26, 27 | 22, 37, 13 | 입력, 풀 없음 | 공장 설정은 출력 LOW였다 → 바꿈(2.2절) |
| 10 (MOSI), 9 (MISO), 11 (SCLK) | 19, 21, 23 | 입력, 풀다운 | SPI0. SPI는 꺼져 있다. 공장 설정 그대로 |
| 8 (CE0), 7 (CE1) | 24, 26 | 입력, 풀업 | SPI0. 공장 설정 그대로 |
| 6 | 31 | 입력, 풀업 | 공장 설정 그대로 |
| 16 | 36 | 입력, 풀다운 | 공장 설정 그대로 |
| 0, 1 | 27, 28 | 입력, 풀업 | 공장 설정 그대로 |

- GPIO 핀의 상태는 출력, 대체 기능(UART 등), 입력 세 가지뿐이다. **입력에 풀 없음이 OPEN에 해당한다.**
- 풀업·풀다운은 칩 내부 저항(약 50kΩ)이다. 완전한 OPEN이 필요하면 `gpio=<번호>=ip,np`로 바꾼다.
- 핀을 새로 출력으로 잡는 변경은 하드웨어 담당 확인 후에 한다.

---

## 2. 부팅 설정 — `/boot/config.txt`

### 2.1 공장 설정

CPi-S102WR의 공장 설정(파일 첫 줄 `# CPi-S102WR`) 가운데 GPIO와 시리얼 부분은 다음과 같았다.

```ini
# Serial ports
enable_uart=1
core_freq=250
dtoverlay=uart1,txd1_pin=32,rxd1_pin=33

# Configure GPIO for CP-IO boards
gpio=4-13=ip
gpio=16=ip
gpio=17-27=op,np,dl
```

원본 전체는 Pi의 `/boot/config.txt.bak-20261124-gpio`에 있다.

### 2.2 GPIO 초기 상태

`gpio=17-27=op,np,dl` 한 줄을 아래처럼 바꾼다.

```ini
# Configure GPIO for CP-IO boards
gpio=4-13=ip
gpio=16=ip
# 2027Version: factory default was gpio=17-27=op,np,dl (all outputs LOW).
# Unused pins must not drive the board: inputs, no pull. 21-24 are set as outputs below.
gpio=17-27=ip,np
# 2027Version: 21=POWER_HOLD(HIGH keep, LOW cuts power) 22=RELAY 23=ADC_RESET 24=TEST_MODE
gpio=21=op,dh
gpio=22=op,dl
gpio=23=op,dh
gpio=24=op,dl
```

- `op` = 출력, `ip` = 입력, `dh` = HIGH, `dl` = LOW, `np` = 풀 없음.
- 뒤에 나온 줄이 앞의 줄을 덮어쓴다. 17-27을 먼저 입력으로 잡고, 21-24만 출력으로 다시 잡는다.
- 이 설정은 **부팅 초기에 펌웨어가 한 번 적용하는 시작 상태**다. 리눅스가 뜨기 전부터 POWER_HOLD를 HIGH로 잡아 준다.
- 종료할 때 핀을 어떻게 할지는 정하지 않는다. 그건 3절의 스크립트가 담당한다.

**주의:** 이 장비의 `/boot/config.txt`는 **Windows 줄바꿈(CRLF)**으로 저장돼 있다.
편집기에서 줄바꿈 형식을 바꾸지 말고 줄만 추가한다. 펌웨어는 LF도 읽지만, 원본과의 차이를 최소로 하기 위해서다.

**확인:** 재부팅 후 (2026-11-24 확인)
```bash
raspi-gpio get 17-27
# GPIO 17~20: level=1 fsel=0 func=INPUT pull=NONE
# GPIO 21: level=1 fsel=1 func=OUTPUT
# GPIO 22: level=0 fsel=1 func=OUTPUT
# GPIO 23: level=1 fsel=1 func=OUTPUT
# GPIO 24: level=0 fsel=1 func=OUTPUT
# GPIO 25~27: level=1 fsel=0 func=INPUT pull=NONE
```

입력 핀이 `level=1`로 읽히는 것은 정상이다(9절 "레벨 시프터").

**되돌리기:** `/boot/config.txt.bak-20261124-gpio`(공장 설정 원본),
`/boot/config.txt.bak-20261124-inputs`(17-27을 입력으로 바꾸기 직전).

### 2.3 UART

`# Serial ports` 묶음의 `dtoverlay=uart1,...` 아래에 넣는다.

```ini
# 2027Version: UART3(GPIO4/5)=POWER board RS485, UART5(GPIO12/13)=RELAY board
dtoverlay=uart3
dtoverlay=uart5
```

| 장치 파일 | UART | 핀 (TX/RX) | 용도 |
|---|---|---|---|
| `/dev/ttyAMA0` | UART0 | GPIO14/15 | 캐리어 보드의 RS485 (ERS는 쓰지 않음) |
| `/dev/ttyS0` | UART1 | GPIO32/33 | 캐리어 보드 (ERS는 쓰지 않음) |
| **`/dev/ttyAMA1`** | **UART3** | **GPIO4/5** | **POWER 보드** |
| **`/dev/ttyAMA2`** | **UART5** | **GPIO12/13** | **릴레이 보드** |

- UART3과 UART5의 핀은 칩에 고정돼 있다. 흐름 제어(`ctsrts`)는 켜지 않는다(켜면 UART3은 GPIO6/7, UART5는 GPIO14/15를 더 쓴다).
- `ttyAMA` 번호는 **켜진 UART를 주소 순으로 센 것**이다. 다른 UART 오버레이를 더하거나 빼면 번호가 바뀐다.
  교체 전 Pi는 UART2·3·4를 켜서 UART3이 `/dev/ttyAMA2`였다.
- `settings.json`은 새 이름에 맞췄다(`relay.comport` = `/dev/ttyAMA2`, `power.comport` = `/dev/ttyAMA1`).
- 코드와 테스트 스크립트의 기본 포트도 2026-11-27에 새 이름으로 바꿨다(POWER `/dev/ttyAMA1`, 릴레이 `/dev/ttyAMA2`).

**확인:** 재부팅 후 (2026-11-24 확인)
```bash
raspi-gpio get 4,5,12,13
# GPIO 4: func=TXD3   GPIO 5: func=RXD3   GPIO 12: func=TXD5   GPIO 13: func=RXD5
for t in /sys/class/tty/ttyAMA*; do echo "$(basename $t) $(basename $(readlink -f $t/device))"; done
# ttyAMA0 fe201000.serial   (UART0)
# ttyAMA1 fe201600.serial   (UART3)
# ttyAMA2 fe201a00.serial   (UART5)
```

---

## 3. 종료 시 전원 차단 — POWER_HOLD 해제 스크립트

**2026-11-24 새 Pi에 설치했다.** 설치 후 재부팅하면 GPIO21이 HIGH로 올라오는 것까지 확인했고,
종료 시 LOW가 되는지는 아직 실측하지 않았다.
GPIO21은 Pi 설정에서 가장 먼저 처리한다. 순서와 확인 방법은 [pi-setup-cm4s-procedure.md](pi-setup-cm4s-procedure.md) 2단계.

### 왜 필요한가

리눅스가 종료돼도 CM4S 칩에는 전원이 남아 있어서 **GPIO 출력은 마지막 값을 유지한다.**
그래서 아무 조치가 없으면 `shutdown` 후에도 GPIO21이 HIGH로 남아 보드 전원이 꺼지지 않는다.

### 스크립트

저장소: [system/ers-power-hold-release](../system/ers-power-hold-release)

```sh
case "$1" in
    poweroff|halt)
        /usr/bin/raspi-gpio set 21 op dl
        ;;
esac
exit 0
```

- systemd는 종료 마지막 단계(파일 시스템 정리가 끝난 뒤)에 `/usr/lib/systemd/system-shutdown/` 안의 실행 파일을 인자 `poweroff`, `halt`, `reboot`, `kexec` 중 하나로 실행한다.
- **종료(`poweroff`, `halt`)일 때만** GPIO21을 LOW로 내린다. 재부팅일 때는 건드리지 않는다.

### 설치

저장소를 Pi의 `~/ers_v2`에 받은 상태에서 실행한다.
```bash
sudo install -m 0755 -o root -g root ~/ers_v2/system/ers-power-hold-release /usr/lib/systemd/system-shutdown/
```

이 파일은 **반드시 LF 줄바꿈**이어야 한다. CRLF면 실행되지 않는다.
저장소의 `.gitattributes`가 `system/*`를 LF로 고정하므로 git으로 받으면 문제없다.
Windows에서 파일을 직접 복사할 때만 주의한다.

**확인:**
```bash
ls -l /usr/lib/systemd/system-shutdown/ers-power-hold-release   # -rwxr-xr-x root root
sudo shutdown -h now      # 종료가 끝나면 보드 전원이 꺼져야 한다
sudo reboot               # 전원이 유지된 채 다시 올라와야 한다
```

**되돌리기:**
```bash
sudo rm /usr/lib/systemd/system-shutdown/ers-power-hold-release
```

### 재부팅할 때의 동작

재부팅하면 칩이 리셋되면서, `config.txt`가 적용될 때까지 GPIO21이 기본 상태(입력, 풀다운)로 잠깐 바뀐다.
교체 전 하드웨어는 이 순간에도 전원을 유지했다(2026-11-13 확인).
**새 보드에서는 아직 확인하지 못했다**(9절).

---

## 4. 소프트웨어 규칙 — GPIO21

ERS 코드는 GPIO21을 다음과 같이 다뤄야 한다.

- **절대 LOW로 쓰지 않는다.** LOW를 쓰는 순간 보드 전원이 꺼진다(종료 절차를 거치지 않음 → 파일 시스템 손상 위험).
- **입력으로 바꾸지 않는다.**
- **`GPIO.cleanup()`의 대상에 넣지 않는다.** RPi.GPIO의 `cleanup()`은 사용한 핀을 입력으로 되돌린다.
  가장 안전한 방법은 ERS 코드에서 GPIO21을 아예 `setup()`하지 않는 것이다(부팅 설정이 이미 HIGH 출력으로 잡아 둠).
- 장비를 끄려면 GPIO를 직접 조작하지 말고 `sudo shutdown -h now`를 쓴다.

[pi_gpio.py](../pi_gpio.py)는 2027Version 핀(22/23/24)만 다룬다. GPIO21은 목록에 없다.
초기화할 때 `initial=`로 레벨을 지정해 출력 전환 순간의 LOW 글리치(ADC 리셋)를 막고,
종료할 때는 22·24 LOW, 23 HIGH로 되돌린다. `GPIO.cleanup()`은 호출하지 않는다.

---

## 5. 자동 실행

새 Pi에는 2026-11-24 현재 **아무것도 자동 실행되지 않는다.** 운영으로 갈 때 설치한다.

### 5.1 `ers-v2` 서비스

설치돼 있지 않다. 설치 방법:

```bash
sudo bash ~/ers_v2/system/install_systemd_service.sh
systemctl is-enabled ers-v2      # enabled
```

개발 중에는 서비스가 시리얼 포트와 GPIO를 잡으므로, 테스트 스크립트를 쓸 때는 멈춘다(`sudo systemctl stop ers-v2`).

### 5.2 cRho (HOST 프로그램)

새 Pi에는 cRho가 없다(`mono`는 설치돼 있음). 교체 전 Pi에서는 `~/cRho`가 `~/cRho4M_Dev/`를 가리키는 심볼릭 링크였고,
`/etc/xdg/lxsession/LXDE-pi/autostart`의 `@mono /home/pi/cRho/bin/cRho.exe` 줄로 자동 실행했다.

- Pi는 그래픽 모드로 부팅하고, LightDM이 `pi` 계정으로 자동 로그인한다.

---

## 6. 장치 테스트 도구

저장소의 [DeviceTest/](../DeviceTest/) 폴더.

| 스크립트 | 용도 | 새 Pi에서 실행 예 |
|---|---|---|
| `uart_send_test.py` | 시리얼 포트 송신 / 수신 확인 (기본 `/dev/ttyAMA1`, 9600 8N1) | `python3 DeviceTest/uart_send_test.py --port /dev/ttyAMA2 --baud 115200` |
| `dsp_modbus_test.py` | POWER 보드 Modbus 통신 (기본 `/dev/ttyAMA1`, 9600 8E1) | `python3 DeviceTest/dsp_modbus_test.py` |
| `GPIOPortTest.py` | GPIO 21-24 상태, 토글, ADC 리셋 | `python3 DeviceTest/GPIOPortTest.py status` |
| `ERS_ADC_Comm_Test.py` | ADC 측정 1회 | `python3 DeviceTest/ERS_ADC_Comm_Test.py` |
| `test_power_control.py`, `test_main_flow.py` | 단위·흐름 테스트 | 저장소 루트에서 `python3 -m unittest discover -s DeviceTest` |

릴레이 보드는 [ERS_Relay_Control.py](../ERS_Relay_Control.py)를 직접 실행한다.
```bash
raspi-gpio set 22 op dh                           # 릴레이 전원 ON
python3 ERS_Relay_Control.py      # 순환 테스트 (48스텝, 약 2분). 기본 포트 /dev/ttyAMA2
python3 ERS_Relay_Control.py -t   # 전체 조합 테스트 (48×13, 약 13분)
raspi-gpio set 22 op dl                           # 릴레이 전원 OFF
```

**주의 두 가지:**

- **기본 포트는 새 Pi(CM4S) 기준이다**(2026-11-27 변경). POWER 쪽 스크립트(`dsp_modbus_test.py`,
  `uart_send_test.py`, `GPIOPortTest.py`, `ERS_Power_Control.py`)는 `/dev/ttyAMA1`,
  `ERS_Relay_Control.py`는 `/dev/ttyAMA2`. 교체 전 Pi에서 돌리려면 포트를 지정해야 한다.
- **Pi에서 단위 테스트를 돌리면 실제 GPIO 22·23·24가 구동된다.** `ERS_Main`을 import만 해도 GPIO23이 HIGH가 된다.
  보드가 연결된 상태에서는 이 점을 알고 실행한다. PC에서는 가짜 GPIO(`fake_gpio.py`)로 돈다.

GPIO는 `raspi-gpio`로 직접 확인할 수 있다.
```bash
raspi-gpio get 21-24            # 상태 읽기
raspi-gpio set 22 op dh         # 릴레이 HIGH
raspi-gpio set 22 op dl         # 릴레이 LOW
raspi-gpio set 23 op dl         # ADC 리셋
raspi-gpio set 23 op dh         # ADC 리셋 해제
```
**`raspi-gpio set 21 ... dl`은 실행하지 않는다** — 즉시 전원이 꺼진다.

---

## 7. 문제 해결

| 증상 | 먼저 볼 것 |
|---|---|
| `shutdown` 후 전원이 안 꺼짐 | 3절 스크립트가 설치돼 있는지, 실행 권한과 LF 줄바꿈인지 |
| 재부팅하다가 전원이 꺼짐 | 전원 유지 회로 (3절 "재부팅할 때의 동작") |
| RS485로 아무것도 안 들어옴 | **485 IC 전원** (2026-11-13 실제 원인), 그다음 A/B 배선, 8E1 설정, **포트가 `/dev/ttyAMA1`인지** |
| 릴레이 보드가 응답하지 않음 | GPIO22가 HIGH인지, **포트가 `/dev/ttyAMA2`인지**, TX/RX가 엇갈려 연결됐는지 |
| UART 송신이 커넥터에서 안 보임 | 레벨 시프터 방향 (9절) |
| ADC 포트가 열리지 않거나 첫 응답이 이상함 | ModemManager가 다시 켜졌는지 (8.4절), ADC 리셋 후 2초 대기 |
| 잡 실행 중 Pi가 조용히 재부팅됨 | 소프트웨어보다 **전원(저전압) 먼저**. `journalctl --list-boots`, `journalctl -k \| grep -i voltage`, `vcgencmd get_throttled` |
| 시리얼 포트를 열 수 없음 | `fuser /dev/ttyAMA1`로 다른 프로그램(ers-v2 서비스 등)이 잡고 있는지 |

`config.txt`에 `avoid_warnings=1`(공장 설정)이 있어 화면에 저전압 경고가 뜨지 않는다. `vcgencmd get_throttled`로 확인한다.

---

## 8. 새 Pi 초기 설치 기록 (2026-11-24)

### 8.1 접속

- SSH: 계정 `pi`, 비밀번호 인증. 2026-11-24 기준 주소는 `192.168.0.60`(이더넷).
- VNC: 8.3절.

### 8.2 저장소와 패키지

```bash
git clone -b 2027Version <저장소 주소> ~/ers_v2
pip3 install --user jsonschema==4.25.1
```

- 공장 이미지에 `git`, `pyserial 3.5b0`, `RPi.GPIO 0.7.0`, `raspi-gpio`, `mono`는 들어 있었다.
- `jsonschema`가 없어서 `ERS_Main`을 import하지 못했다. 설치 후 import 정상.
- `RPi.GPIO 0.7.0`은 CM4S를 `TYPE: Unknown`으로 표시하지만 핀 구동은 정상이다.

### 8.3 VNC

공장 이미지에 VNC 서버가 없어서 설치했다.

```bash
sudo apt-get install -y realvnc-vnc-server      # 7.5.1
sudo raspi-config nonint do_vnc 0               # 켜기 (1 = 끄기)
systemctl is-active vncserver-x11-serviced      # active
```

RealVNC Viewer로 5900 포트에 접속한다. 2026-11-24 접속 확인.

### 8.4 ModemManager — 껐다

공장 이미지에서 ModemManager 서비스가 실행 중이었다. 이 서비스는 USB 시리얼 장치가 꽂히면 모뎀인지 확인하려고
포트를 열고 AT 명령을 보낸다. ADC(`/dev/ttyACM0`)가 그 대상이 될 수 있고, ERS는 모뎀을 쓰지 않으므로 껐다(2026-11-24).

```bash
sudo systemctl disable --now ModemManager
# 되돌리기: sudo systemctl enable --now ModemManager
```

끈 뒤에도 NetworkManager와 이더넷은 정상이다.

### 8.5 공장 이미지의 서비스 (2026-11-24 점검)

**캐리어 보드 제조사(ComfileTech)가 넣은 것 — 건드리지 않는다**

| 서비스 | 하는 일 |
|---|---|
| `touch-beep` | 터치 비프음 (`/opt/ComfileTech/touch/beep/`). `pigpiod`에 연결해서 동작한다 |
| `pigpiod` | GPIO 데몬. `touch-beep`과 제조사 시험 프로그램(`ComfilePiTest`)이 쓴다. 127.0.0.1:8888 |
| `config_mono` | 스피커가 하나인 모델의 오디오를 모노로 설정 (부팅 때 한 번) |
| `rpi-display-backlight` | 종료·재부팅 때 LCD 백라이트 끄기 |
| `lightdm` | 데스크톱 화면 (LCD, VNC) |

`pigpiod`는 명령을 받기 전에는 핀 설정을 바꾸지 않는다. ERS가 쓰는 핀을 점유하지 않는 것을 확인했다.
CPU를 상시 약 7% 쓴다.

**ERS에 필요 없는 것 — 실행 중이지만 그대로 두었다**

| 서비스 | 하는 일 | 비고 |
|---|---|---|
| `cups`, `cups-browsed` | 인쇄 | 127.0.0.1:631 |
| `avahi-daemon` | mDNS(`raspberrypi.local` 이름 찾기) | UDP 5353 |
| `wpa_supplicant`, `bluetooth` | 무선랜, 블루투스 | 이 장비에는 해당 하드웨어가 없다 |
| `packagekit`, `apt-daily.timer`, `apt-daily-upgrade.timer` | 패키지 목록 자동 갱신 | 인터넷에 연결돼 있으면 주기적으로 동작 |
| `udisks2`, `triggerhappy`, `colord` | USB 저장장치 자동 인식, 단축키, 색 관리 | |

시리얼 포트를 여는 서비스는 없다(시리얼 콘솔 꺼짐, `console=tty1`). 실패한 서비스 없음. 부팅 시간 약 8초.

---

## 9. 확인하지 못한 것 (보드 연결 후)

2026-11-24에는 Pi에 전원만 연결했다. 2026-11-27에 보드를 연결했다. 아래는 Pi 설정만으로는 확인할 수 없는 항목이다.

- [ ] **40핀 번호** — 이 문서의 40핀 번호는 표준 배열 기준이다. CPi-S102WR 외부 커넥터 배열과 대조
- [ ] **UART 송신이 커넥터까지 나가는지** — 공장 설정은 GPIO4-13을 입력용, 17-27을 출력용으로 나눠 두었다.
  레벨 시프터가 방향 고정식이면 UART3 TX(GPIO4)와 UART5 TX(GPIO12)가 나가지 않는다.
  커넥터에서 7번-29번, 32번-33번을 각각 이어 루프백으로 확인
- [ ] **커넥터 쪽 신호 전압** — 3.3V인지 5V인지 실측
- [ ] **레벨 시프터의 풀업** — 아무것도 연결하지 않았는데 입력 핀이 전부 HIGH로 읽힌다(내부 풀다운이 걸린 핀 포함).
  레벨 시프터가 선을 HIGH로 당기는 것으로 추정. Pi 핀이 입력이어도 커넥터에는 전압이 나올 수 있다
- [ ] **부팅 직후 GPIO22·24** — 전원 투입부터 `config.txt`가 적용될 때까지 위 풀업 때문에 릴레이 전원(GPIO22)과
  TEST 모드(GPIO24)가 잠깐 HIGH가 될 수 있다. ERS 보드 쪽에 풀다운이 있는지 확인, 필요하면 오실로스코프로 측정
- [ ] **POWER_HOLD** — 종료하면 전원이 꺼지는지, 재부팅하면 전원이 유지되는지 (3절)
  - 2026-11-27 보드 연결 상태에서 `shutdown -h now` 후 전원이 내려가는 것을 눈으로 확인. 재부팅 시 전원 유지는 아직 확인하지 않음
- [x] **POWER 보드 Modbus** (`/dev/ttyAMA1`), **릴레이 보드** (`/dev/ttyAMA2`), **ADC** (`/dev/ttyACM0`) 통신 — 2026-11-27 확인
  - POWER: FC04 측정 읽기 5회, FC03 설정 읽기 1회 모두 응답 (STOP, TRIP 없음)
  - 릴레이: GPIO22 HIGH로 전원을 켠 뒤 전체 해제 → 1번 릴레이 설정 → 전체 해제, 모두 첫 시도에 응답. 순환 테스트는 하지 않음
  - ADC: `303a:4001` 인식, DC 패턴(On 500ms / Off 50ms / 1스택) 5280샘플을 재시도 없이 수신
- [x] **코드 기본 포트 정리** — 2026-11-27 POWER `/dev/ttyAMA2` → `/dev/ttyAMA1`, 릴레이 `/dev/ttyAMA3` → `/dev/ttyAMA2`

2026-11-24 Pi 쪽에서 확인한 것: ERS가 쓰는 핀을 다른 드라이버가 점유하지 않음, 부팅 로그에 핀·UART·I2C 오류 없음,
RTC 시각 읽기 정상, `/dev/ttyAMA1`·`/dev/ttyAMA2`를 연 프로세스 없음, 9600 8E1과 115200 8N1 설정 가능,
`pi` 계정이 `dialout`·`gpio` 그룹에 있음.

---

## 10. 교체 전 Pi 기록 (CM4, 2026-11-13 ~ 2026-11-24)

교체 전 장비: Raspberry Pi Compute Module 4 Rev 1.1, 같은 OS·커널·Python.

| 항목 | 교체 전 (CM4) | 새 Pi (CM4S + CPi-S102WR) |
|---|---|---|
| GPIO 21·22·23·24 | 같음 | 같음 |
| POWER 보드 | UART3 (GPIO4/5), `/dev/ttyAMA2` | UART3 (GPIO4/5), **`/dev/ttyAMA1`** |
| 릴레이 보드 | USB-시리얼, `/dev/ttyUSB0` | **UART5 (GPIO12/13), `/dev/ttyAMA2`** |
| ADC | USB, `/dev/ttyACM0` | 같음 |
| 켠 UART | UART0, 2, 3, 4 | UART0, 1, 3, 5 |
| 쓰지 않는 핀 | 공장 설정 그대로 | 17-20, 25-27을 입력으로 바꿈 |

교체 전 `config.txt`의 관련 줄:
```ini
dtoverlay=disable-bt
dtparam=uart0=on
dtoverlay=uart2,txd2_pin=0,rxd2_pin=1
dtoverlay=uart3,txd3_pin=4,rxd3_pin=5
dtoverlay=uart4,txd4_pin=8,rxd4_pin=9
gpio=11=op,dh
gpio=20=op,dl
gpio=21=op,dh
gpio=22=op,dl
gpio=23=op,dh
gpio=24=op,dl
gpio=45=op,dl
```

교체 전 하드웨어에서 확인한 것:

| 날짜 | 항목 | 결과 |
|---|---|---|
| 2026-11-13 | GPIO22, 23, 24 | HIGH/LOW 토글 동작 정상 |
| 2026-11-13 | GPIO21 | 부팅하면 HIGH, 종료하면 전원 차단, 재부팅하면 전원이 유지된 채 다시 올라옴 |
| 2026-11-13 | UART3 RS485 | 9600bps 양방향 송수신 정상 (PC의 USB-RS485 변환기와 시험) |
| 2026-11-13 | POWER 보드 Modbus | FC04 측정 읽기, FC03 설정 읽기 정상 |
| 2026-11-17 | ADC | `/dev/ttyACM0` 인식, 측정 5회 연속 통과 |
| 2026-11-24 | 릴레이 보드 (USB-시리얼) | GPIO22로 전원 ON/OFF, 전체 해제 응답, 단일 채널, 순환 테스트 48스텝 통과. Pi 리셋·저전압 없음 |

교체 전 Pi에서는 개발 기간 동안 `ers-v2` 서비스를 `disable`했고, cRho 자동 실행 줄을 주석 처리했다
(백업 `/etc/xdg/lxsession/LXDE-pi/autostart.bak-20261113`). 교체 전 Pi는 2026-11-24에 종료했다.
