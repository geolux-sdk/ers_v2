try:
    import RPi.GPIO as GPIO
except (ImportError, RuntimeError):
    import fake_gpio as GPIO

import logging

# 2027Version 핀 (docs/pi-setup-2027.md 1절). 모두 출력.
#   GPIO21 POWER_HOLD : 이 모듈은 절대 다루지 않는다. LOW/입력/cleanup 이 되면 보드 전원이 꺼진다.
#                       부팅 시 config.txt(gpio=21=op,dh)가 HIGH 로 잡고, 종료 시 system-shutdown 훅이 내린다.
RELAY_PWREN = 22        # HIGH = 릴레이 보드 전원
ADC_RESET = 23          # LOW = ADC 리셋, 평상시 HIGH
TEST_MODE_PIN = 24      # HIGH = TEST 모드
POWER_HOLD = 21         # 참고용 상수. 사용 금지.

# 핀별 평상시(안전) 레벨. 초기화와 close() 에서 이 값으로 둔다.
IDLE_LEVELS = {
    RELAY_PWREN: False,
    ADC_RESET: True,
    TEST_MODE_PIN: False,
}


class GPIOController:
    def __init__(self, logger=None):
        self.logger = logger or logging.getLogger(__name__)
        self.relay_pwren_pin = RELAY_PWREN
        self.adc_reset_pin = ADC_RESET
        self.gpio_test_mode_pin = TEST_MODE_PIN
        self.output_pins = list(IDLE_LEVELS)
        assert POWER_HOLD not in self.output_pins
        try:
            GPIO.setwarnings(False)
            GPIO.setmode(GPIO.BCM)
            for pin, level in IDLE_LEVELS.items():
                # initial 로 설정해야 출력 전환 순간 LOW 글리치(ADC 리셋)가 생기지 않는다.
                GPIO.setup(pin, GPIO.OUT, initial=GPIO.HIGH if level else GPIO.LOW)
        except Exception as e:
            self.logger.error(f"Failed to initialize GPIO: {e}")
            raise

    def _write(self, pin, level, name):
        try:
            GPIO.output(pin, GPIO.HIGH if level else GPIO.LOW)
            self.logger.info("%s %s", name, "HIGH" if level else "LOW")
        except Exception as e:
            self.logger.error(f"Failed to set {name}: {e}")
            raise

    def enable_test_mode(self):
        self._write(self.gpio_test_mode_pin, True, "TEST_MODE")

    def disable_test_mode(self):
        self._write(self.gpio_test_mode_pin, False, "TEST_MODE")

    def enable_relay_power(self):
        self._write(self.relay_pwren_pin, True, "RELAY_PWREN")

    def disable_relay_power(self):
        self._write(self.relay_pwren_pin, False, "RELAY_PWREN")

    def assert_adc_reset(self):
        self._write(self.adc_reset_pin, False, "ADC_RESET(active)")

    def release_adc_reset(self):
        self._write(self.adc_reset_pin, True, "ADC_RESET(released)")

    def disable_all_outputs(self):
        self.disable_test_mode()
        self.disable_relay_power()

    def close(self):
        """평상시 레벨로 되돌린다. GPIO.cleanup() 은 호출하지 않는다(핀이 입력으로 바뀜)."""
        try:
            for pin, level in IDLE_LEVELS.items():
                GPIO.output(pin, GPIO.HIGH if level else GPIO.LOW)
            self.logger.debug("GPIO outputs reset to idle levels")
        except Exception as e:
            self.logger.error(f"Failed to reset GPIO outputs: {e}")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


if __name__ == "__main__":
    import time

    logging.basicConfig(level=logging.DEBUG)
    with GPIOController() as pin:
        pin.enable_relay_power()
        pin.enable_test_mode()
        time.sleep(5)
        pin.disable_test_mode()
        pin.disable_relay_power()
