# ERS V2 systemd 실행 가이드

이 프로젝트는 Raspberry Pi에서 Python 3.9.2로 실행되는 ERS V2 Controller입니다.

권장 실행 방식은 `systemd`가 직접 `ERS_Main.py`를 실행하고 재시작을 관리하는 방식입니다.

## 실행 구조

```text
systemd -> ERS_Main.py
```

`systemd` 서비스에 `Restart=always`를 설정하면 `ERS_Main.py`가 종료되거나 예외로 중단되어도 자동으로 다시 실행됩니다.

## Raspberry Pi 부팅 설정

ERS에서 사용하는 UART와 GPIO 초기 상태를 사용하려면 Raspberry Pi의 부팅 설정 파일에 아래 내용을 미리 추가해야 합니다.

일반 Raspberry Pi OS에서는 아래 파일을 수정합니다.

```bash
sudo nano /boot/config.txt
```

일부 최신 Raspberry Pi OS에서는 설정 파일 위치가 아래일 수 있습니다.

```bash
sudo nano /boot/firmware/config.txt
```

아래 내용을 파일 끝에 추가합니다.

```ini
dtoverlay=uart3,txd3_pin=4,rxd3_pin=5
dtoverlay=uart4,txd4_pin=8,rxd4_pin=9

gpio=20=op,dl
gpio=24=op,dl
gpio=45=op,dl
```

설정 의미:

```text
UART3 TX/RX -> GPIO4/GPIO5
UART4 TX/RX -> GPIO8/GPIO9
GPIO20      -> output, default low
GPIO24      -> output, default low
GPIO45      -> output, default low
```

`config.txt` 수정 후에는 반드시 재부팅해야 설정이 적용됩니다.

```bash
sudo reboot
```

## 서비스 파일 위치

Raspberry Pi에서 아래 위치에 서비스 파일을 생성합니다.

```bash
/etc/systemd/system/ers-v2.service
```

파일 생성:

```bash
sudo nano /etc/systemd/system/ers-v2.service
```

## 서비스 파일 내용

```ini
[Unit]
Description=ERS V2 Controller
After=multi-user.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/ers_v2
ExecStart=/usr/bin/python3 /home/pi/ers_v2/ERS_Main.py
Restart=always
RestartSec=1
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

## 설정 의미

`WorkingDirectory=/home/pi/ers_v2`

`settings.json` 안의 `./log`, `./JSON`, `./DataDC` 같은 상대경로가 `/home/pi/ers_v2` 기준으로 동작하게 합니다.

`ExecStart=/usr/bin/python3 /home/pi/ers_v2/ERS_Main.py`

Raspberry Pi의 Python 3.9.2로 `ERS_Main.py`를 실행합니다.

`Restart=always`

`ERS_Main.py`가 종료되면 자동으로 다시 실행합니다.

`RestartSec=1`

종료 후 1초 뒤에 재실행합니다.

`Environment=PYTHONUNBUFFERED=1`

`print()` 출력이 지연되지 않고 로그에 바로 기록되게 합니다.

## 서비스 적용

예제 서비스 파일을 복사하고 systemd에 등록하려면 설치 스크립트를 사용할 수 있습니다.

```bash
cd /home/pi/ers_v2
sudo bash install_systemd_service.sh
```

수동으로 적용하려면 아래 명령을 실행합니다.

```bash
sudo cp /home/pi/ers_v2/ers-v2.service.example /etc/systemd/system/ers-v2.service
sudo systemctl daemon-reload
sudo systemctl enable ers-v2.service
sudo systemctl restart ers-v2.service
```

## 상태 확인

```bash
systemctl status ers-v2.service
```

## 실시간 로그 확인

```bash
journalctl -u ers-v2.service -f
```

## 서비스 중지

```bash
sudo systemctl stop ers-v2.service
```

## 자동 시작 해제

```bash
sudo systemctl disable ers-v2.service
```

## 중복 실행 주의

ERS는 UDP 포트, serial 포트, GPIO를 직접 사용합니다. 같은 프로그램이 두 개 이상 동시에 실행되면 포트 bind 실패, serial 포트 충돌, 장비 제어 충돌이 발생할 수 있습니다.

운영 중에는 `systemd` 서비스 하나만 `ERS_Main.py`를 실행하도록 관리합니다.

## 통신 방식

ERS V2 Controller는 외부 프로그램과는 UDP로 통신하고, Raspberry Pi에 연결된 장비와는 serial 통신으로 제어합니다.

```text
외부 프로그램
  <-> UDP 127.0.0.1:3700/3800
ERS_Main.py
  <-> /dev/ttyAMA3 relay board
  <-> /dev/ttyAMA2 power controller
  <-> /dev/ttyACM0 ADC device
  <-> GPIO24 test mode pin
```

### UDP 통신

UDP 설정은 `settings.json`의 `udp` 항목에서 관리합니다.

```json
"udp": {
    "send_addr": "127.0.0.1",
    "send_port": 3800,
    "recv_port": 3700
}
```

현재 구성은 외부 프로그램도 같은 Raspberry Pi에서 실행되는 것을 전제로 합니다. 그래서 `send_addr`는 `127.0.0.1`입니다.

ERS는 `recv_port`인 `3700`번 포트에서 명령을 수신하고, 응답은 `send_addr:send_port`, 즉 `127.0.0.1:3800`으로 전송합니다.

### UDP 명령

외부 프로그램이 ERS로 보내는 명령은 문자열입니다.

| 명령 | 동작 |
| --- | --- |
| `STATUS` | 현재 상태를 응답합니다. |
| `DCSTART` | `JSON/DCworkControl.json`을 읽고 DC 작업을 시작합니다. |
| `IPSTART` | `JSON/IPworkControl.json`을 읽고 IP 작업을 시작합니다. |
| `SPSTART` | `JSON/SPworkControl.json`을 읽고 SP 작업을 시작합니다. |
| `TESTELSTART` | `JSON/DCworkControl.json`을 읽고 전극 테스트를 시작합니다. |
| `TEST` | `TESTELSTART`와 동일하게 동작합니다. |
| `TESTADCSTART` | `JSON/DCworkControl.json`을 읽고 ADC 테스트를 시작합니다. |
| `QUIT` | 상태에 따라 현재 작업 중지 또는 프로세스 종료를 수행합니다. |

### UDP 응답

작업 시작 명령이 정상 접수되면 아래 형식으로 응답합니다.

```text
DCSTARTOK
IPSTARTOK
SPSTARTOK
TESTELSTARTOK
TESTADCSTARTOK
```

작업 시작이 거부되면 아래 형식으로 응답합니다.

```text
DCSTARTFAIL
IPSTARTFAIL
SPSTARTFAIL
TESTELSTARTFAIL
TESTADCSTARTFAIL
```

작업이 정상 완료되면 아래 형식으로 응답하고 상태는 `READY`가 됩니다.

```text
DCDONE
IPDONE
SPDONE
TESTELDONE
TESTADCDONE
```

작업 중 오류가 발생하면 `FAIL`을 전송하고 내부 상태를 `FAULT`로 변경합니다. 오류 설명이 있으면 `FAIL` 전송 뒤에 오류 메시지를 추가로 전송합니다.

`STATUS` 명령에 대한 응답은 현재 상태 문자열입니다.

```text
INIT
WAIT
READY
BUSY
FAULT
ERROR
```

### 상태 전이

정상 초기화 흐름:

```text
INIT -> WAIT -> READY
```

작업 시작 흐름:

```text
READY -> BUSY -> READY
```

작업 실패 흐름:

```text
BUSY -> FAULT
```

초기화 중 설정 파일 오류나 UDP bind 실패가 발생하면 `ERROR` 상태가 됩니다. 릴레이 또는 전원 장치 초기화가 실패하면 `FAULT` 상태가 됩니다.

### QUIT 처리

`QUIT` 명령은 현재 상태에 따라 다르게 동작합니다.

| 현재 상태 | 동작 |
| --- | --- |
| `BUSY` | 현재 작업 중지 요청 플래그를 설정합니다. worker가 다음 안전 지점에서 릴레이와 전원을 정지하고 `FAIL`을 전송합니다. |
| `READY` | 별도 종료 없이 로그만 남깁니다. |
| `FAULT`, `ERROR`, `WAIT`, 기타 상태 | 메인 루프 종료 플래그를 설정합니다. systemd가 실행 중이면 프로세스 종료 후 자동 재시작됩니다. |

현재 복구 정책은 `FAIL` 또는 `STATUS=FAULT`를 받은 외부 프로그램이 `QUIT`를 보내고, systemd가 `ERS_Main.py`를 재시작해 다시 초기화하는 방식입니다.

### Relay serial 통신

릴레이 보드는 `settings.json`의 `relay.comport`에 설정된 `/dev/ttyAMA3` 포트를 사용합니다.

```json
"relay": {
    "inport_num": 13,
    "outport_num": 48,
    "comport": "/dev/ttyAMA3"
}
```

job 파일의 `Cmds`, `TestCmds`, `TestADCCmds`는 `settings.json`의 `main.work_order` 순서에 맞춰 릴레이 번호를 지정합니다. 값 `0`은 해당 채널을 사용하지 않는다는 의미입니다.

ERS는 작업 파일을 읽을 때 각 command row의 길이가 `work_order` 길이와 같은지, 릴레이 번호가 `0..outport_num` 범위인지 먼저 검증합니다.

### Power serial 통신

전원 컨트롤러는 Modbus RTU 방식으로 `/dev/ttyAMA2` 포트를 사용합니다.

```json
"power": {
    "comport": "/dev/ttyAMA2",
    "baudrate": 115200,
    "timeout": 0.5,
    "device_id": 241
}
```

프로그램 시작 시 holding register 초기값을 설정하고 전류 offset 보정을 수행합니다. 작업 시작 시 job 파일의 `MaxVval`, `MaxIval` 값을 사용해 목표 전압과 전류를 설정합니다.

작업 중 전원 모니터링 값의 `error_status`가 0이 아니면 작업 실패로 처리하고 `FAIL`을 전송합니다.

### ADC serial 통신

ADC 장치는 `/dev/ttyACM0` 포트를 사용합니다.

```json
"adc": {
    "port": "/dev/ttyACM0",
    "baudrate": 115200,
    "sample_rate": 2400
}
```

job 파일의 `OnTime`, `OffTime`, `NoStack`과 `sample_rate`를 이용해 ADC sample 수를 계산합니다.

작업 유형에 따라 ADC pattern 값은 아래처럼 설정됩니다.

| 작업 유형 | pattern |
| --- | --- |
| `DC` | `1` |
| `IP` | `2` |
| 기타 | `0` |

ADC 데이터는 job의 `DataDir` 아래에 `.dat` 파일로 저장됩니다. 예상 byte 수보다 적게 수신되면 작업 실패로 처리합니다.

ADC busy timeout은 예상 측정 시간에 5초 여유를 더해 계산하되, 최소 10초를 사용합니다.

### GPIO 통신

ComfilePi IO26-2 GPIO 핀맵은 아래 이미지를 참고합니다.

![ComfilePi IO26-2 GPIO Pin Map](https://www.comfilewiki.co.kr/ko/lib/exe/fetch.php?media=comfilepi:comfilepiports:io262.png)

GPIO 출력은 BCM 번호 기준으로 사용합니다.

```text
GPIO20 -> BOOSTER_ENABLE
GPIO24 -> TEST_MODE
GPIO45 -> RELAY_PWREN
```

초기화 시 세 핀은 모두 출력으로 설정하고 LOW 상태로 둡니다.

```text
BOOSTER_ENABLE HIGH -> booster enabled
BOOSTER_ENABLE LOW  -> booster disabled
TEST_MODE HIGH      -> test mode enabled
TEST_MODE LOW       -> test mode disabled
RELAY_PWREN HIGH    -> relay power enabled
RELAY_PWREN LOW     -> relay power disabled
```

`TESTADCSTART` 작업에서는 테스트 모드를 켠 뒤 1초 안정화 대기를 하고, 작업 종료 또는 실패 시 테스트 모드를 끄고 다시 1초 안정화 대기를 수행합니다.

프로그램 시작 시에는 GPIO를 먼저 초기화한 뒤 `RELAY_PWREN`을 HIGH로 설정하고 1초 안정화 대기 후 릴레이 보드를 초기화합니다. 프로그램 종료 시에는 GPIO cleanup 전에 GPIO20/24/45를 모두 LOW로 내립니다.

작업 시작 시 전압/전류 목표값을 설정하기 직전에 `BOOSTER_ENABLE`을 HIGH로 설정합니다. 작업이 정상 완료되거나 오류로 중지되면 전원 stop 후 `BOOSTER_ENABLE`을 LOW로 내립니다.
