# ERS 2027Version Raspberry Pi 설정

최종 확인: 2026-11-13
대상 장비: Raspberry Pi Compute Module 4 Rev 1.1 (`raspberrypi`), Raspberry Pi OS, 커널 6.1.21-v8+, Python 3.9.2

이 문서는 2027Version 하드웨어에서 Pi를 처음 설정하거나 다시 설치할 때 따라 하는 절차다.
각 항목 끝에 **확인 방법**과 **되돌리는 방법**을 적었다.

> 기존(v2) 하드웨어 기준 설정은 [README.md](../README.md)의 "Raspberry Pi 부팅 설정"에 있다.
> 2027Version에서는 이 문서가 우선한다.

---

## 1. 하드웨어 연결 요약

| 기능 | Pi 자원 | 방향 | 평상시 레벨 | 비고 |
|---|---|---|---|---|
| **POWER_HOLD** | GPIO21 | 출력 | **HIGH** | **LOW가 되면 보드 전원이 꺼진다** |
| 릴레이 | GPIO22 | 출력 | LOW | HIGH = 동작 |
| ADC_RESET | GPIO23 | 출력 | HIGH | **LOW = ADC 리셋** |
| TEST 모드 | GPIO24 | 출력 | LOW | HIGH = TEST 모드 |
| POWER 보드 (ESR_DSP) | `/dev/ttyAMA2` (UART3, TX GPIO4 / RX GPIO5) | RS485 | — | Modbus RTU 9600 **8E1**, 주소 1. [power-board-dsp-modbus.md](power-board-dsp-modbus.md) |
| ADC | `/dev/ttyACM0` | USB | — | 기존과 같음 |
| 릴레이 보드 통신 | `/dev/ttyUSB0` | USB-시리얼 | — | 기존과 같음 |

- RS485 트랜시버는 **자동 방향 전환** 방식이다. Pi 쪽에서 방향 제어 핀을 다루지 않는다.
- GPIO11은 기존 보드(CPi)용 `gpio-poweroff` 설정이 걸려 있다. **2027Version에서 쓰지 않지만 설정은 건드리지 않는다.**
- 2026-11-13 하드웨어 확인 결과:

  | 항목 | 결과 |
  |---|---|
  | GPIO22, 23, 24 | HIGH/LOW 토글 동작 정상 |
  | GPIO21 | 부팅하면 HIGH, 종료하면 전원 차단, 재부팅하면 전원이 유지된 채 다시 올라옴 |
  | UART3 RS485 | 9600bps 양방향 송수신 정상 (PC의 USB-RS485 변환기와 시험) |
  | POWER 보드 Modbus | FC04 측정 읽기, FC03 설정 읽기 정상 (`dsp_modbus_test.py read`) |

---

## 2. 부팅 설정 — `/boot/config.txt`

### 2.1 GPIO 초기 상태

`# Configure GPIO for CP-IO board` 묶음의 `gpio=20=op,dl` 아래에 4줄을 넣는다.
`gpio=24=op,dl`은 원래 있던 줄이다.

```ini
gpio=11=op,dh
gpio=20=op,dl
# 2027Version: 21=POWER_HOLD(HIGH keep, LOW cuts power) 22=RELAY 23=ADC_RESET 24=TEST_MODE
gpio=21=op,dh
gpio=22=op,dl
gpio=23=op,dh
gpio=24=op,dl
gpio=45=op,dl
```

- `op` = 출력, `dh` = HIGH, `dl` = LOW.
- 이 설정은 **부팅 초기에 펌웨어가 한 번 적용하는 시작 상태**다. 리눅스가 뜨기 전부터 POWER_HOLD를 HIGH로 잡아 준다.
- 종료할 때 핀을 어떻게 할지는 정하지 않는다. 그건 3절의 스크립트가 담당한다.

**주의:** 이 장비의 `/boot/config.txt`는 **Windows 줄바꿈(CRLF)**으로 저장돼 있다.
편집기에서 줄바꿈 형식을 바꾸지 말고 줄만 추가한다. 펌웨어는 LF도 읽지만, 원본과의 차이를 최소로 하기 위해서다.

**확인:** 재부팅 후
```bash
raspi-gpio get 21-24
# GPIO 21: level=1 fsel=1 func=OUTPUT
# GPIO 22: level=0 fsel=1 func=OUTPUT
# GPIO 23: level=1 fsel=1 func=OUTPUT
# GPIO 24: level=0 fsel=1 func=OUTPUT
```

**되돌리기:** 2026-11-13 변경 전 원본은 `/boot/config.txt.bak-20261113-gpio`에 있다.

### 2.2 UART (기존과 같음, 변경 없음)

```ini
dtoverlay=disable-bt
dtparam=uart0=on
dtoverlay=uart2,txd2_pin=0,rxd2_pin=1
dtoverlay=uart3,txd3_pin=4,rxd3_pin=5
dtoverlay=uart4,txd4_pin=8,rxd4_pin=9
```

| 장치 파일 | UART | 핀 (TX/RX) |
|---|---|---|
| `/dev/ttyAMA0` | UART0 | GPIO14/15 |
| `/dev/ttyAMA1` | UART2 | GPIO0/1 |
| `/dev/ttyAMA2` | **UART3** | **GPIO4/5** ← POWER 보드 |
| `/dev/ttyAMA3` | UART4 | GPIO8/9 |

---

## 3. 종료 시 전원 차단 — POWER_HOLD 해제 스크립트

### 왜 필요한가

리눅스가 종료돼도 CM4 칩에는 전원이 남아 있어서 **GPIO 출력은 마지막 값을 유지한다.**
그래서 아무 조치가 없으면 `shutdown` 후에도 GPIO21이 HIGH로 남아 보드 전원이 꺼지지 않는다.

`gpio-poweroff` 오버레이를 GPIO21로 옮기는 방법은 쓰지 않는다. 이유는 두 가지다.
- `active_low`로 쓰면 **재부팅할 때도 핀이 LOW**가 되어 전원이 꺼진다(오버레이 설명서에 명시).
- 이 오버레이는 커널의 정상 종료 절차를 막는다. GPIO11에 이미 쓰이고 있고, 하나만 지정할 수 있다.

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
- GPIO11의 `gpio-poweroff`는 그대로 동작한다. 이 스크립트가 그보다 먼저 실행된다.

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

재부팅하면 칩이 리셋되면서, `config.txt`가 적용될 때까지 GPIO21이 기본 상태(입력, 풀다운 = LOW)로 잠깐 떨어진다.
2026-11-13 확인 결과, 현재 하드웨어는 이 순간에도 전원을 유지한다.
전원 유지 회로를 바꾸면 이 동작을 다시 확인해야 한다.

---

## 4. 소프트웨어 규칙 — GPIO21

ERS 코드는 GPIO21을 다음과 같이 다뤄야 한다.

- **절대 LOW로 쓰지 않는다.** LOW를 쓰는 순간 보드 전원이 꺼진다(종료 절차를 거치지 않음 → 파일 시스템 손상 위험).
- **입력으로 바꾸지 않는다.** 내부 풀다운 때문에 LOW가 된다.
- **`GPIO.cleanup()`의 대상에 넣지 않는다.** RPi.GPIO의 `cleanup()`은 사용한 핀을 입력으로 되돌린다.
  가장 안전한 방법은 ERS 코드에서 GPIO21을 아예 `setup()`하지 않는 것이다(부팅 설정이 이미 HIGH 출력으로 잡아 둠).
- 장비를 끄려면 GPIO를 직접 조작하지 말고 `sudo shutdown -h now`를 쓴다.

[pi_gpio.py](../pi_gpio.py)는 2027Version 핀(22/23/24)만 다룬다. GPIO21은 목록에 없다.
초기화할 때 `initial=`로 레벨을 지정해 출력 전환 순간의 LOW 글리치(ADC 리셋)를 막고,
종료할 때는 22·24 LOW, 23 HIGH로 되돌린다. `GPIO.cleanup()`은 호출하지 않는다.

---

## 5. 개발 기간 동안 꺼 둔 자동 실행

2026-11-13부터 장치 테스트를 위해 아래 두 가지를 꺼 두었다. **운영으로 돌아갈 때 다시 켠다.**

### 5.1 `ers-v2` 서비스 (기존 ERS)

시리얼 포트(`/dev/ttyAMA2` 등)를 테스트 스크립트가 쓰기 위해 껐다.

```bash
systemctl is-enabled ers-v2      # disabled
# 다시 켜기:
sudo systemctl enable --now ers-v2
```

### 5.2 cRho (HOST 프로그램)

데스크톱 세션이 뜰 때 자동 실행되던 것을 막았다.
파일: `/etc/xdg/lxsession/LXDE-pi/autostart`

```
#@mono /home/pi/cRho/bin/cRho.exe        ← 맨 앞에 # 를 붙여 주석 처리
```

- 원본 백업: `/etc/xdg/lxsession/LXDE-pi/autostart.bak-20261113`
- 다시 켜기: 그 줄 맨 앞의 `#`을 지우고 재부팅한다.
- `~/cRho`는 `~/cRho4M_Dev/`를 가리키는 심볼릭 링크다.
- Pi는 그래픽 모드로 부팅하고, LightDM이 `pi` 계정으로 자동 로그인한다.

---

## 6. 장치 테스트 도구

저장소의 [DeviceTest/](../DeviceTest/) 폴더. Pi에서 `~/ers_v2/DeviceTest/`에서 실행한다.

| 스크립트 | 용도 | 예 |
|---|---|---|
| `uart_send_test.py` | 시리얼 포트 송신 / 수신 확인 (기본 9600 8N1) | `python3 uart_send_test.py`, `python3 uart_send_test.py --listen` |
| `dsp_modbus_test.py` | POWER 보드 Modbus 통신 (9600 8E1) | `python3 dsp_modbus_test.py read`, `... selftest` |
| `test_power_control.py`, `test_main_flow.py` | 장치 없이 도는 단위·흐름 테스트 | 저장소 루트에서 `python3 -m unittest discover -s DeviceTest` |

GPIO는 별도 스크립트 없이 `raspi-gpio`로 확인한다.
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
| 재부팅하다가 전원이 꺼짐 | 전원 유지 회로가 바뀌었는지 (3절 "재부팅할 때의 동작") |
| RS485로 아무것도 안 들어옴 | **485 IC 전원** (2026-11-13 실제 원인), 그다음 A/B 배선, 8E1 설정 |
| 잡 실행 중 Pi가 조용히 재부팅됨 | 소프트웨어보다 **전원(저전압) 먼저**. `journalctl --list-boots`, `journalctl -k \| grep -i voltage` |
| 시리얼 포트를 열 수 없음 | `fuser /dev/ttyAMA2`로 다른 프로그램(ers-v2 서비스 등)이 잡고 있는지 |
