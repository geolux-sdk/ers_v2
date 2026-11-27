# 새 Raspberry Pi 설정 절차 — CM4S + CPi-S102WR

작성: 2026-11-24 (이 날 `192.168.0.60` 장비를 설정하면서 정리했다)

**같은 기종의 Pi를 새로 설정할 때 이 문서를 위에서부터 차례로 따라 한다.**
단계마다 **명령**, **확인**(기대하는 결과), **되돌리기**를 적었다. 확인이 기대와 다르면 다음 단계로 넘어가지 않는다.

- 왜 이렇게 설정하는지, 핀 배치와 주의 사항은 [pi-setup-2027.md](pi-setup-2027.md)에 있다.
- 이 문서의 명령은 모두 Pi에서 실행한다(SSH 또는 터미널).

## 단계 요약

| 단계 | 내용 | 보드 연결 | 재부팅 |
|---|---|---|---|
| 0 | 기종 확인 | 연결 전 | |
| 1 | 저장소 받기 (설정 도구가 여기 들어 있다) | 연결 전 | |
| **2** | **GPIO21 POWER_HOLD — 가장 먼저 설정한다** | 연결 전 | 재부팅·종료 시험 |
| 3 | 나머지 부팅 설정 확인 (GPIO, UART) | 연결 전 | |
| 4 | 패키지 | 연결 전 | |
| 5 | ModemManager 끄기 | 연결 전 | |
| 6 | VNC | 연결 전 | |
| 7 | 서비스 점검 | 연결 전 | |
| 8 | UART 루프백 | 연결 전 (점퍼) | |
| 9 | 보드 연결과 장치 통신 확인 | **연결 후** | 종료·재부팅 시험 |
| 10 | ERS 서비스와 HOST | 연결 후 | |
| 11 | 패키지 자동 갱신 막기 | **맨 마지막** | |

**0-8단계는 Pi에 전원만 넣은 상태에서 한다.** ERS 보드(POWER·릴레이·ADC)는 9단계에서 연결한다.

**GPIO21을 가장 먼저 설정하는 이유:** GPIO21(POWER_HOLD)은 보드 전원을 붙잡는 핀이다.
부팅하면 바로 HIGH가 되어야 하고, 종료하면 자동으로 LOW가 되어야 한다.
공장 설정에서는 GPIO21이 **출력 LOW**라서, 이 상태로 보드에 연결하면 전원이 유지되지 않는다.
다른 설정을 하다가 재부팅·종료할 일이 생기므로, 전원 동작부터 맞춰 놓고 나머지를 진행한다.

---

## 0. 기종 확인

이 절차는 **Compute Module 4S + 캐리어 보드 CPi-S102WR**용이다. 다른 기종이면 핀과 장치 이름이 다를 수 있으니 중단한다.

```bash
tr -d '\0' < /proc/device-tree/model; echo
head -n 1 /boot/config.txt
grep PRETTY_NAME /etc/os-release; uname -r; python3 --version
```

**확인:**
```text
Raspberry Pi Compute Module 4S Rev 1.0
# CPi-S102WR
PRETTY_NAME="Debian GNU/Linux 11 (bullseye)"
6.1.21-v8+
Python 3.9.2
```

- 첫 두 줄이 다르면 **중단**하고 하드웨어 담당에게 핀 배치를 확인한다.
- OS·커널·Python 버전이 다르면 계속하되, 2-3단계 확인을 더 꼼꼼히 본다.

공장 설정 상태인지도 본다. 아래 줄이 있어야 한다.
```bash
grep -n 'gpio=17-27' /boot/config.txt       # gpio=17-27=op,np,dl
```

---

## 1. 저장소 받기

2단계에서 쓰는 설정 스크립트와 종료 스크립트가 저장소에 들어 있다.

```bash
git clone -b 2027Version <저장소 주소> ~/ers_v2
```

**확인:**
```bash
cd ~/ers_v2 && git status -sb | head -1                                   # ## 2027Version...origin/2027Version
ls system/pi-config-cm4s.sh system/ers-power-hold-release                 # 두 파일이 있다
file system/ers-power-hold-release                                        # CRLF 라는 말이 없어야 한다
```

- 공장 이미지에 `git`은 들어 있다.
- **아직 `ERS_Main`을 import하거나 단위 테스트를 돌리지 않는다.** Pi에서는 실제 GPIO 22·23·24가 구동된다.

**되돌리기:** `rm -rf ~/ers_v2`

---

## 2. GPIO21 POWER_HOLD — 가장 먼저 설정한다

| 요구 | 방법 |
|---|---|
| 부팅하면 바로 HIGH | `/boot/config.txt`의 `gpio=21=op,dh` (2.1) |
| 종료하면 자동으로 LOW | 종료 스크립트 `ers-power-hold-release` (2.2) |
| 재부팅할 때는 HIGH 유지 | 같은 스크립트가 재부팅일 때는 핀을 건드리지 않는다 |

### 2.1 부팅 시 HIGH — 부팅 설정 스크립트

```bash
sudo bash ~/ers_v2/system/pi-config-cm4s.sh
```

이 스크립트는 `/boot/config.txt`를 고친다. 기종과 캐리어 보드가 맞는지 스스로 확인하고, 맞지 않으면 아무것도 바꾸지 않는다.
바꾸기 전에 백업을 만든다. 여러 번 실행해도 한 번만 적용된다.
GPIO21뿐 아니라 나머지 GPIO와 UART 설정도 함께 넣는다(그 확인은 3단계).

**확인 (실행 직후):**
```text
apply UART3, UART5
apply GPIO 17-27
백업: /boot/config.txt.bak-<날짜시각>
재부팅해야 적용된다: sudo reboot
```

`STOP:`으로 시작하는 줄이 나오면 바뀐 것이 없다. 메시지를 보고 0단계로 돌아간다.

아직 재부팅하지 않는다. 2.2를 먼저 한다.

### 2.2 종료 시 LOW — 종료 스크립트 설치

리눅스가 종료돼도 칩에는 전원이 남아 있어서 GPIO 출력은 마지막 값(HIGH)을 유지한다.
종료 마지막 단계에 GPIO21을 LOW로 내리는 스크립트를 설치한다.

```bash
sudo install -m 0755 -o root -g root ~/ers_v2/system/ers-power-hold-release /usr/lib/systemd/system-shutdown/
```

**확인:**
```bash
ls -l /usr/lib/systemd/system-shutdown/ers-power-hold-release    # -rwxr-xr-x root root
```

### 2.3 동작 확인

**① 재부팅 — HIGH가 되고 유지되는지**
```bash
sudo reboot
```
다시 접속해서:
```bash
raspi-gpio get 21        # GPIO 21: level=1 fsel=1 func=OUTPUT
```

**② 종료 — LOW가 되는지**

Pi에 전원을 직접 넣은 상태에서는 종료해도 전원이 꺼지지 않는다. 그래서 핀을 테스터로 잴 수 있다.
외부 커넥터의 40번 핀(GPIO21)에 테스터를 대고 종료한다.

```bash
sudo shutdown -h now
```

| 시점 | 40번 핀 |
|---|---|
| 종료 전 | HIGH |
| 종료가 끝난 뒤 (화면 꺼짐, 네트워크 끊김) | **LOW** |

LOW가 되지 않으면 2.2의 설치 위치, 실행 권한, 줄바꿈(LF)을 본다.
확인이 끝나면 전원을 껐다 켜서 다시 부팅한다.

- 40번은 표준 라즈베리파이 40핀 배열 기준이다. CPi-S102WR 외부 커넥터의 배열과 먼저 대조한다.
- 전원이 들어온 순간부터 펌웨어가 `config.txt`를 적용할 때까지의 짧은 구간에는 GPIO21이 칩 기본 상태(입력)다.
  그 구간에 전원을 유지하는 것은 보드의 전원 유지 회로가 맡는다.
- 보드를 연결한 상태의 전원 동작(종료하면 전원 차단, 재부팅하면 전원 유지)은 9.1에서 다시 확인한다.
- **2026-11-24 현재 `192.168.0.60`에서 2.2와 2.3 ①까지 했다. 2.3 ②(종료 시 LOW 실측)는 아직 하지 않았다.**

**되돌리기:**
```bash
sudo rm /usr/lib/systemd/system-shutdown/ers-power-hold-release
sudo cp /boot/config.txt.bak-<날짜시각> /boot/config.txt && sudo reboot
```

**GPIO21에 대한 규칙:** ERS 코드와 손으로 치는 명령 모두 GPIO21을 LOW로 쓰거나, 입력으로 바꾸거나,
`GPIO.cleanup()` 대상에 넣지 않는다. 장비를 끌 때는 `sudo shutdown -h now`만 쓴다
([pi-setup-2027.md](pi-setup-2027.md) 4절).

---

## 3. 나머지 부팅 설정 확인 (GPIO, UART)

2.1의 스크립트가 함께 넣은 설정을 확인한다. 2.3 ①의 재부팅 뒤에 본다.

```bash
raspi-gpio get 4,5,12,13
raspi-gpio get 17-27
ls /dev/ttyAMA*
for t in /sys/class/tty/ttyAMA*; do echo "$(basename $t) $(basename $(readlink -f $t/device))"; done
```

| 핀 | 기대하는 결과 |
|---|---|
| GPIO4, 5 | `func=TXD3`, `func=RXD3` |
| GPIO12, 13 | `func=TXD5`, `func=RXD5` |
| GPIO17, 18, 19, 20 | `func=INPUT pull=NONE` |
| GPIO21 | `level=1 func=OUTPUT` |
| GPIO22 | `level=0 func=OUTPUT` |
| GPIO23 | `level=1 func=OUTPUT` |
| GPIO24 | `level=0 func=OUTPUT` |
| GPIO25, 26, 27 | `func=INPUT pull=NONE` |

```text
/dev/ttyAMA0  /dev/ttyAMA1  /dev/ttyAMA2
ttyAMA0 fe201000.serial      ← UART0 (캐리어 보드의 RS485, ERS는 쓰지 않음)
ttyAMA1 fe201600.serial      ← UART3 = POWER 보드
ttyAMA2 fe201a00.serial      ← UART5 = 릴레이 보드
```

- 입력 핀이 `level=1`로 읽히는 것은 정상이다(캐리어 보드의 레벨 시프터).
- **`ttyAMA1`과 `ttyAMA2`의 주소가 위와 다르면 중단한다.** 장치 이름이 밀린 것이므로 `settings.json`의
  `power.comport`(`/dev/ttyAMA1`)와 `relay.comport`(`/dev/ttyAMA2`)가 맞지 않게 된다.

캐리어 보드 기본 기능이 그대로인지도 본다.
```bash
sudo hwclock -r                              # RTC 시각이 나온다 (I2C 정상)
raspi-gpio get 2,3,14,15                     # SDA1, SCL1, TXD0, RXD0
```

스크립트가 바꾸는 내용(직접 고칠 때 참고):

| 공장 설정 | 바꾼 뒤 |
|---|---|
| `dtoverlay=uart1,txd1_pin=32,rxd1_pin=33` | 그 아래에 `dtoverlay=uart3`, `dtoverlay=uart5` 추가 |
| `gpio=17-27=op,np,dl` | `gpio=17-27=ip,np` 로 바꾸고, 그 아래에 `gpio=21=op,dh`, `gpio=22=op,dl`, `gpio=23=op,dh`, `gpio=24=op,dl` 추가 |

파일은 CRLF 줄바꿈이다. 직접 고칠 때는 줄바꿈 형식을 바꾸지 않는다.
그 밖의 줄(I2C, RTC, UART0, UART1, 백라이트, 레벨 시프터, 오디오)은 캐리어 보드 기본 기능이므로 건드리지 않는다.

---

## 4. 패키지

```bash
pip3 install --user jsonschema==4.25.1
```

**확인:**
```bash
python3 -c "import serial, jsonschema, RPi.GPIO; print('ok')"     # ok
```

공장 이미지에 `pyserial`, `RPi.GPIO`, `raspi-gpio`, `mono`는 들어 있다. 빠진 것은 `jsonschema`뿐이었다.

**되돌리기:** `pip3 uninstall jsonschema`

---

## 5. ModemManager 끄기

USB 시리얼 장치(ADC, `/dev/ttyACM0`)에 AT 명령을 보내는 서비스다. ERS는 모뎀을 쓰지 않는다.

```bash
sudo systemctl disable --now ModemManager
```

**확인:**
```bash
systemctl is-enabled ModemManager; systemctl is-active ModemManager     # disabled / inactive
systemctl is-active NetworkManager; hostname -I                         # active / IP 주소
```

**되돌리기:** `sudo systemctl enable --now ModemManager`

---

## 6. VNC

```bash
sudo apt-get install -y realvnc-vnc-server
sudo raspi-config nonint do_vnc 0
```

**확인:**
```bash
systemctl is-active vncserver-x11-serviced       # active
ss -ltn | grep ':5900'                           # LISTEN
```
PC의 RealVNC Viewer로 접속해 화면이 뜨는지 본다.

**되돌리기:** `sudo raspi-config nonint do_vnc 1`

---

## 7. 서비스 점검

공장 이미지의 서비스가 ERS와 겹치지 않는지 본다.

```bash
systemctl --failed --no-legend                                   # 아무것도 없어야 한다
sudo fuser -v /dev/ttyAMA1 /dev/ttyAMA2 2>&1                     # 아무것도 없어야 한다
tr ' ' '\n' < /proc/cmdline | grep console                       # console=tty1 (시리얼 콘솔 없음)
sudo ss -lunp | grep -E ':(3700|3800)\b'                         # 아무것도 없어야 한다 (HOST 통신 포트)
dpkg -l brltty 2>/dev/null | grep '^ii'                          # 아무것도 없어야 한다
```

**건드리지 않는 것** (캐리어 보드 제조사 ComfileTech가 넣은 것):
`touch-beep`, `pigpiod`, `config_mono`, `rpi-display-backlight`, `lightdm`.
`pigpiod`는 `touch-beep`이 쓴다. 떠 있는 상태에서 3단계의 핀 상태가 유지되면 문제없다.

바탕화면의 제조사 시험 프로그램(`ComfilePiTest`)은 GPIO와 시리얼 포트를 직접 구동한다.
**ERS 보드가 연결된 상태에서는 실행하지 않는다.**

서비스 목록과 설명은 [pi-setup-2027.md](pi-setup-2027.md) 8.5절.

---

## 8. UART 루프백 (보드 연결 전)

Pi의 UART 송신이 외부 커넥터까지 나가고 수신이 들어오는지 본다.
**Pi 전원을 끈 상태에서** 외부 40핀 커넥터에 점퍼 두 개를 꽂는다.

| UART | 점퍼 |
|---|---|
| UART3 (POWER 보드) | 7번(GPIO4, TX) - 29번(GPIO5, RX) |
| UART5 (릴레이 보드) | 32번(GPIO12, TX) - 33번(GPIO13, RX) |

핀 번호는 표준 라즈베리파이 40핀 배열 기준이다. CPi-S102WR 외부 커넥터의 배열과 먼저 대조한다.

```bash
python3 - <<'EOF'
import serial

for port in ("/dev/ttyAMA1", "/dev/ttyAMA2"):
    with serial.Serial(port, 9600, timeout=1) as s:
        s.reset_input_buffer()
        s.write(b"ERS-LOOP")
        print(port, "OK" if s.read(8) == b"ERS-LOOP" else "FAIL")
EOF
```

**확인:**
```text
/dev/ttyAMA1 OK
/dev/ttyAMA2 OK
```

- 점퍼가 없으면 둘 다 `FAIL`이 나온다(2026-11-24 확인). 점퍼를 꽂고 `FAIL`이면 핀 번호가 다르거나
  레벨 시프터가 그 방향으로 신호를 넘기지 않는 것이다. 이때는 보드를 연결하지 말고 하드웨어 담당에게 확인한다.
- **2026-11-24 현재 점퍼를 꽂은 시험은 아직 하지 않았다.**
- 이때 커넥터 쪽 신호 전압(3.3V인지 5V인지)도 테스터로 잰다.

시험이 끝나면 전원을 끄고 점퍼를 뺀다.

---

## 9. 보드 연결과 장치 통신 확인

2-8단계가 모두 끝난 뒤에 ERS 보드를 연결한다.
`ers-v2` 서비스가 돌고 있으면 먼저 멈춘다(`sudo systemctl stop ers-v2`). 명령은 저장소 폴더에서 실행한다.

### 9.1 POWER_HOLD — 보드 전원 동작

```bash
raspi-gpio get 21         # level=1 func=OUTPUT
vcgencmd get_throttled    # throttled=0x0
sudo reboot               # 전원이 유지된 채 다시 올라와야 한다
sudo shutdown -h now      # 종료가 끝나면 보드 전원이 꺼져야 한다
```

부팅할 때 릴레이 전원(GPIO22)과 TEST 모드(GPIO24)가 잠깐 켜지지 않는지도 본다
([pi-setup-2027.md](pi-setup-2027.md) 9절).

### 9.2 릴레이 보드 (`/dev/ttyAMA2`)

```bash
cd ~/ers_v2
raspi-gpio set 22 op dh                        # 릴레이 전원 ON — 보드의 전원 LED 확인
python3 -c "from ERS_Relay_Control import relay_board_controller as R; print('CLEAR', R(comport='/dev/ttyAMA2').clear())"
python3 ERS_Relay_Control.py                   # 순환 테스트, 약 2분 (기본 포트 /dev/ttyAMA2)
raspi-gpio set 22 op dl                        # 릴레이 전원 OFF — LED 꺼짐 확인
```

**확인:** `CLEAR True`, 순환 테스트 로그에 `Fail`이 없다. 릴레이 동작음이 차례로 난다.

### 9.3 POWER 보드 (`/dev/ttyAMA1`)

`DeviceTest/dsp_modbus_test.py`로 측정값을 읽는다. 기본 포트가 `/dev/ttyAMA1`이다.

**확인:** FC04 측정 읽기와 FC03 설정 읽기가 응답한다. 프로토콜은 [power-board-dsp-modbus.md](power-board-dsp-modbus.md).

### 9.4 ADC (`/dev/ttyACM0`)

```bash
lsusb | grep 303a                              # 303a:4001 Espressif Device
python3 DeviceTest/ERS_ADC_Comm_Test.py
```

**확인:** 측정 1회가 재시도 없이 끝난다. ADC를 리셋한 뒤에는 2초 이상 기다렸다가 포트를 연다.

### 9.5 단위 테스트

```bash
python3 -m unittest discover -s DeviceTest      # OK
raspi-gpio get 21-24                            # 21=1, 22=0, 23=1, 24=0
```

Pi에서는 이 테스트가 실제 GPIO 22·23·24를 구동한다. 끝난 뒤 평상시 레벨인지 본다.

---

## 10. ERS 서비스와 HOST

```bash
sudo bash ~/ers_v2/system/install_systemd_service.sh
```

**확인:**
```bash
systemctl is-enabled ers-v2; systemctl is-active ers-v2      # enabled / active
journalctl -u ers-v2 -n 30 --no-pager
```
HOST에서 `STATUS`를 보내 `READY`가 오는지 본다.

cRho(HOST 프로그램)를 설치하고 자동 실행을 설정한다([pi-setup-2027.md](pi-setup-2027.md) 5.2절).

**되돌리기:** `sudo systemctl disable --now ers-v2`

---

## 11. 패키지 자동 갱신 막기 (맨 마지막)

설치와 설정이 모두 끝난 뒤에 한다. 먼저 막으면 `apt`로 설치할 때 불편하다.

```bash
sudo systemctl disable --now apt-daily.timer apt-daily-upgrade.timer
sudo systemctl mask packagekit
```

**확인:**
```bash
systemctl is-enabled apt-daily.timer apt-daily-upgrade.timer packagekit     # disabled / disabled / masked
```

**되돌리기:**
```bash
sudo systemctl unmask packagekit
sudo systemctl enable --now apt-daily.timer apt-daily-upgrade.timer
```

---

## 설정 기록

Pi를 설정할 때마다 한 줄씩 적는다.

| 날짜 | 장비 | 한 단계 | 남은 단계 | 비고 |
|---|---|---|---|---|
| 2026-11-24 | `192.168.0.60` (CM4S Rev 1.0, CPi-S102WR) | 0, 1, 2.1, 2.2, 2.3 ①, 3, 4, 5, 6, 7 | **2.3 ②**, 8, 9, 10, 11 | 이 문서를 만들기 전이라 순서가 달랐다. 2.1은 손으로 고쳤고 그 결과가 스크립트 결과와 같은 것을 확인했다. 전원만 연결한 상태 |
| 2026-11-27 | `192.168.0.60` | 9.2(순환 테스트 제외), 9.3, 9.4, 9.1 중 종료 시 전원 차단 | **2.3 ②**, 8, 9.1 재부팅 시 전원 유지, 9.2 순환 테스트, 9.5, 10, 11 | 보드 연결 상태. 8(루프백)을 건너뛰고 장치 통신으로 직접 확인했다. 종료 시 전원 차단은 눈으로 확인했고 40번 핀 전압은 재지 않았다 |
