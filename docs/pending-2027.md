# ERS 2027Version 추후 확인 사항

작성일: 2026-11-13
기준 커밋: `2027Version` 브랜치 `63f9e4d`

2026-11-13까지 개발·검증하면서 **장비가 없거나 결정을 미뤄서** 확인하지 못한 항목을 모았다.
항목을 처리하면 결과와 날짜를 적고 체크한다.

---

## 1. 실제 잡 시험 (장비 연결 후)

2026-11-13 당시 Pi에는 ADC·릴레이가 연결되지 않았고, POWER 보드는 전압을 출력하지 못했다.
ERS_Main 흐름은 가짜 장치로 만든 테스트(`DeviceTest/test_main_flow.py`)로만 확인했다.

준비: ADC(`/dev/ttyACM0`), 릴레이(`/dev/ttyUSB0`) 연결, POWER 보드 출력 가능 상태, 부하(전극 또는 더미 저항).

- [ ] **잡 전체 흐름** — `DCSTART` → `DCSTARTOK` → 측정 → `DCDONE`. 저장된 `.dat` 파일 확인
- [ ] **TESTEL / TESTADC** — 30V 고정으로 동작하는지, TESTADC에서 TEST 모드(GPIO24) 켜고 끄는지
- [ ] **출력 전압 도달 판정** — 기준: 2초 안에 ±max(1V, 2%).
  저전압(예: 20V)과 고전압(예: 200V)에서 실제 도달 시간·정착 오차를 재고 `settings.json`의
  `voltage_settle_timeout_sec`, `voltage_tolerance_v`, `voltage_tolerance_pct`를 조정
- [ ] **운전 제한 시간 계산** — 로그의 `POWER PLAN ... limit=...s`가 실제 잡 시간보다 충분히 긴지.
  명령 수가 많은 잡(예: 21개 이상)에서 확인
- [ ] **통신 유지** — ADC 측정이 10초를 넘는 잡(`OnTime`·`NoStack`이 큰 잡)에서 POWER 보드가 멈추지 않는지
- [ ] **정지 순서** — 정상 종료·오류·`QUIT` 각각에서 POWER STOP이 릴레이 해제보다 먼저인지 로그로 확인
- [ ] **ADC_RESET(GPIO23)** — 평상시 HIGH에서 ADC가 정상 동작하는지. 지금 ERS는 이 핀을 잡 흐름에서 쓰지 않는다
  (`pi_gpio.assert_adc_reset()/release_adc_reset()`만 있음). ADC 오류 복구에 리셋을 쓸지 검토

## 2. POWER 보드 TRIP 실제 시험

과전류를 실제로 일으켜야 해서 2026-11-13에는 하지 못했다. STOP 상태의 해제 명령과 RUN 중 거부(예외 04)는 확인했다
([power-board-dsp-modbus.md](power-board-dsp-modbus.md) 11절).

- [ ] 실제 과전류 → ERS `FAIL` + `POWER TRIP: cause=OVER_CURRENT peak=...` + `FAULT`
- [ ] TRIP 후 ERS가 **자동으로 해제하지 않는지**
- [ ] `RESET` → POWER 보드 TRIP 해제 → `RESETOK` → `READY`
- [ ] 트립 직후 전류가 아직 OC 임계값보다 클 때 해제가 거부되는지(펌웨어 사양)
- [ ] OC 임계값 = 전류 지령 × 1.5가 정상 측정 중 순간 전류로 불필요하게 TRIP되지 않는지

## 3. POWER 보드 전류 상한 (하드웨어 확인 후)

지금은 펌웨어 범위 기준으로 **전류 지령 최대 1A, OC 최대 1.5A**로 정리했다. 2.0A 지령은 DSP가 거부한다(예외 03).

- [ ] POWER 보드 하드웨어 정격(소자, 전류 센서) 확인
- [ ] 상한을 올린다면 ESR_DSP 펌웨어의 범위 검사(`main.c` `app_mb_write_multiple()`)를 먼저 바꿈
- [ ] 그다음 `settings.json` `power`의 `max_current_a`, `oc_max_a` 조정
- [ ] [host-changes-2027.md](host-changes-2027.md) 3절(`MaxIval` 범위) 갱신

## 4. HOST(cRho) 수정

할 일과 체크리스트는 [host-changes-2027.md](host-changes-2027.md).

- [ ] `RESET` 명령, `RESETOK`/`RESETFAIL` 처리
- [ ] 새 POWER 오류 메시지 처리, 삭제된 메시지 정리
- [ ] cRho 수정 후 ERS와 연동 시험 (`FAULT` → 사용자 확인 → `RESET` → `READY`)

## 5. 보류한 설계 — 같은 오류 반복

[error-recovery-design.md](error-recovery-design.md)의 "연속 실패 카운터"는 사용자 결정으로 보류했다(2026-11-13).

- [ ] `ADC CAPTURE FAIL`, `RELAY SETUP FAIL`, `CANCEL BY USER`는 `READY`로 돌아가므로, HOST가 계속 재요청하면 반복된다.
  연속 실패 횟수(N), 카운터를 두는 위치(잡과 잡 사이), FAULT 승격 여부를 정한다
- 참고: POWER 관련 오류는 모두 `FAULT`로 처리해서 고전압 반복 인가는 이미 막았다

## 6. 운영 전환 전

개발 기간 동안 꺼 둔 것들. 방법은 [pi-setup-2027.md](pi-setup-2027.md) 5절.

- [ ] ERS 서비스 자동 시작 다시 켜기 (`sudo systemctl enable --now ers-v2`)
  - 서비스 파일이 `/home/pi/ers_v2/ERS_Main.py`를 실행하는지, 2027Version 코드로 정상 기동하는지 확인
- [ ] cRho 자동 실행 다시 켜기 (`/etc/xdg/lxsession/LXDE-pi/autostart`의 `#` 제거)
- [ ] 재부팅 후 `STATUS` → `READY` 확인
- [ ] 전원 유지 회로를 바꿨다면 종료(전원 차단)·재부팅(전원 유지) 동작 다시 확인 ([pi-setup-2027.md](pi-setup-2027.md) 3절)

## 7. ERS 밖의 일 (참고)

- [ ] ESR_DSP `MODBUS_HOST_PROTOCOL.md`의 한글이 `?`로 깨져 저장돼 있음 — 펌웨어 담당에게 원본 요청
