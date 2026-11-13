# HOST(cRho) 수정 요청 — ERS 2027Version

작성일: 2026-11-13
대상: ERS에 UDP로 명령을 보내는 HOST 프로그램(cRho) 개발자
ERS 기준 커밋: `2027Version` 브랜치

ERS 2027Version에서 POWER 보드가 바뀌면서 HOST와 주고받는 내용 일부가 달라졌다.
통신 방식(UDP, 127.0.0.1, 수신 3700 / 송신 3800)과 기존 명령·응답 형식은 그대로다.
**바뀐 부분만** 정리한다. 전체 프로토콜은 [README.md](../README.md)의 "통신 방식".

---

## 1. 필수 — `RESET` 명령 처리 (새 명령)

### 왜 필요한가

ERS가 `FAULT`가 되었을 때 지금까지 HOST는 `QUIT`를 보내 ERS를 재시작시켰다.
2027Version에서는 이 방법으로 복구되지 않는 경우가 생긴다.

- 새 POWER 보드는 과전류가 나면 **TRIP** 상태로 멈추고, 이 상태는 **POWER 보드 안에 남는다.**
- ERS를 재시작해도 POWER 보드는 여전히 TRIP이라 ERS는 다시 `FAULT`가 된다.
- TRIP은 `RESET` 명령으로만 해제된다.

ERS는 TRIP을 스스로 해제하지 않는다. 같은 조건이면 다시 과전류가 나서 고전압을 반복 인가하게 되므로,
**사람이 원인을 확인한 뒤** HOST가 `RESET`을 보내는 구조다.

### 명령과 응답

| HOST → ERS | ERS → HOST | 의미 |
|---|---|---|
| `RESET` | `RESETOK` | 해제 성공. ERS 상태 `READY` |
| `RESET` | `RESETFAIL` 다음에 원인 메시지 1줄 | 해제 실패. ERS 상태 `FAULT` 유지 |
| `RESET` (ERS가 `BUSY`일 때) | `RESETFAIL` (원인 메시지 없음) | 작업 중에는 거부 |

`RESETFAIL` 뒤에 오는 원인 메시지 예:

```text
DEVICE NOT FOUND: ADC=/dev/ttyACM0, RELAY=/dev/ttyUSB0
POWER TRIP: cause=OVER_CURRENT peak=0.812A
POWER COMM FAIL
POWER INIT FAIL
```

- `RESET`은 응답까지 수백 ms ~ 1초 정도 걸린다(POWER 보드 확인, 장치 확인).
- `READY` 상태에서 `RESET`을 보내도 된다(장치 재확인만 하고 `RESETOK`).

### HOST에서 할 일

1. `FAIL` 또는 `STATUS` 응답 `FAULT`를 받으면, 함께 받은 오류 메시지를 사용자에게 보여 준다.
2. 사용자가 원인을 확인하고 "복구"를 선택하면 `RESET`을 보낸다. **자동으로 반복해서 보내지 않는다.**
3. `RESETOK`이면 작업을 다시 시작할 수 있다.
4. `RESETFAIL`이면 이어서 오는 원인 메시지를 보여 주고 `FAULT`로 둔다.

> 프로그램 시작 직후 초기화에서 `FAULT`가 된 경우, ERS는 원인 메시지를 따로 보내지 않는다(`STATUS`에 `FAULT`만 응답).
> 이때도 `RESET`을 보내면 `RESETFAIL` 뒤에 원인을 알 수 있다.

---

## 2. 필수 — 오류 메시지 변경

`FAIL` 다음에 오는 오류 메시지가 바뀌었다. 메시지 문자열로 분기하는 코드가 있으면 수정한다.

### 새로 생긴 메시지 (모두 ERS 상태 `FAULT`)

| 메시지 | 의미 | 사용자 조치 |
|---|---|---|
| `POWER TRIP: cause=OVER_CURRENT peak=<A>A` | POWER 보드 과전류 보호 동작. `peak`는 작업 중 최대 전류 | 전극·배선 단락 확인 후 `RESET` |
| `POWER SETUP FAIL` | 설정 확인 불일치, 또는 출력 전압이 2초 안에 목표에 도달하지 못함 | POWER 보드 확인 후 `RESET` |
| `POWER STOPPED UNEXPECTEDLY` | 작업 중 POWER 보드가 스스로 멈춤(운전 제한 시간 초과, 통신 10초 두절) | 로그 확인 후 `RESET` |
| `POWER COMM FAIL` | POWER 보드 통신 두절 | RS485 배선·POWER 보드 전원 확인 후 `RESET` |
| `POWER REJECTED` | POWER 보드가 현재 상태 때문에 명령 거부(보드 로컬 메뉴 사용 중 등) | POWER 보드 로컬 메뉴 종료 후 `RESET` |
| `POWER PARAM INVALID` | 잡 파일 `MaxVval`이 1~300V 밖 | 잡 파일 수정 |
| `POWER SETUP EXCEPTION` | 전원 설정 중 예상하지 못한 오류 | 로그 확인 |
| `POWER INIT FAIL` | (`RESETFAIL` 원인으로만) POWER 보드 초기 확인 실패 | 배선·전원 확인 |

### 더 이상 보내지 않는 메시지

| 메시지 | 이유 |
|---|---|
| `BOOSTER ENABLE FAIL` | 새 하드웨어에 부스터가 없어 제거 |
| `POWER MONITORING FAIL` | `POWER COMM FAIL`로 대체 |
| `POWER ERROR FAIL: error_status=...` | 새 POWER 보드에는 `error_status`가 없음. `POWER TRIP: ...`으로 대체 |

### 바뀌지 않은 메시지

`CANCEL BY USER`, `ADC CAPTURE FAIL`, `RELAY SETUP FAIL`(이 셋은 `READY`로 복귀),
`RELAY POWER ENABLE FAIL`, `RELAY INIT CLEAR FAIL`, `TEST MODE ENABLE FAIL`, 잡 파일 관련 메시지 등은 그대로다.

---

## 3. 확인 — 잡 파일 값의 해석

잡 파일 형식은 그대로다. 다만 POWER 보드 한계 때문에 값이 다르게 적용될 수 있다.

| 필드 | 2027Version 동작 |
|---|---|
| `MaxVval` (V) | 1~300V만 허용. 범위 밖이면 작업 거부(`POWER PARAM INVALID`) |
| `MaxIval` (mA) | A로 바꿔 적용. **1000mA를 넘으면 1000mA(1A)로 잘라서 진행**하고 ERS 로그에만 경고를 남긴다. 100mA 미만은 100mA |
| (자동) 과전류 임계값 | `MaxIval` × 1.5 (0.3~1.5A 범위로 제한) |
| `DoElectrodeTest`, `DoADCTest` | 기존처럼 전압 30V 고정 |

- HOST 화면에서 `MaxIval` 입력 범위를 **100~1000mA**로 제한하면, 요청과 실제 값이 달라지는 일을 막을 수 있다(권장).
- 1A, 1.5A 상한은 POWER 보드 하드웨어 확인 뒤 바뀔 수 있다. 바뀌면 이 문서를 갱신한다.

---

## 4. 참고 — 동작 시간 변화

- 작업 시작 시 POWER 확인(설정 → 확인 → RUN → 전압 도달)에 최대 약 3초가 걸린다. `STARTOK`은 이보다 먼저 온다.
- POWER 보드는 작업 길이로 계산한 제한 시간이 지나면 스스로 멈춘다. 정상 작업에서는 일어나지 않는다.

---

## 5. 참고 — 현재 Pi 상태 (개발 기간)

- Pi에서 cRho 자동 실행을 꺼 두었다(`/etc/xdg/lxsession/LXDE-pi/autostart`의 cRho 줄 주석 처리).
- ERS 서비스(`ers-v2`)도 자동 시작을 꺼 두었다.
- 운영으로 돌아갈 때 두 가지를 다시 켠다. 방법은 [pi-setup-2027.md](pi-setup-2027.md) 5절.

---

## 체크리스트

- [ ] `RESET` 송신 기능 (사용자 확인 후, 자동 반복 금지)
- [ ] `RESETOK` / `RESETFAIL` + 원인 메시지 처리
- [ ] 새 POWER 오류 메시지 표시
- [ ] 삭제된 메시지(`BOOSTER ENABLE FAIL`, `POWER MONITORING FAIL`, `POWER ERROR FAIL...`)에 의존하는 코드 정리
- [ ] (권장) `MaxIval` 입력 범위 100~1000mA, `MaxVval` 1~300V
