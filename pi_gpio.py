try:
    import RPi.GPIO as GPIO
except (ImportError, RuntimeError):
    import fake_gpio as GPIO

import logging


TEST_MODE_PIN = 24
BOOSTER_ENABLE = 20
RELAY_PWREN = 45


class GPIOController:
    def __init__(self, logger=None):
        self.logger = logger or logging.getLogger(__name__)
        self.gpio_test_mode_pin = TEST_MODE_PIN
        self.booster_enable_pin = BOOSTER_ENABLE
        self.relay_pwren_pin = RELAY_PWREN
        self.output_pins = [
            self.gpio_test_mode_pin,
            self.booster_enable_pin,
            self.relay_pwren_pin,
        ]
        try:
            GPIO.setwarnings(False)
            GPIO.setmode(GPIO.BCM)
            for pin in self.output_pins:
                GPIO.setup(pin, GPIO.OUT)
                GPIO.output(pin, GPIO.LOW)
        except Exception as e:
            self.logger.error(f"Failed to initialize GPIO: {e}")
            raise

    def enable_test_mode(self):
        try:
            GPIO.output(self.gpio_test_mode_pin, GPIO.HIGH)
            self.logger.debug("gpio_test_mode_pin SET")
            self.logger.info("TEST MODE enabled")
        except Exception as e:
            self.logger.error(f"Failed to enable test mode: {e}")
            raise

    def disable_test_mode(self):
        try:
            GPIO.output(self.gpio_test_mode_pin, GPIO.LOW)
            self.logger.debug("gpio_test_mode_pin CLEAR")
            self.logger.info("TEST MODE disabled")
        except Exception as e:
            self.logger.error(f"Failed to disable test mode: {e}")
            raise

    def enable_booster(self):
        try:
            GPIO.output(self.booster_enable_pin, GPIO.HIGH)
            self.logger.info("BOOSTER_ENABLE enabled")
        except Exception as e:
            self.logger.error(f"Failed to enable BOOSTER_ENABLE: {e}")
            raise

    def disable_booster(self):
        try:
            GPIO.output(self.booster_enable_pin, GPIO.LOW)
            self.logger.info("BOOSTER_ENABLE disabled")
        except Exception as e:
            self.logger.error(f"Failed to disable BOOSTER_ENABLE: {e}")
            raise

    def enable_relay_power(self):
        try:
            GPIO.output(self.relay_pwren_pin, GPIO.HIGH)
            self.logger.info("RELAY_PWREN enabled")
        except Exception as e:
            self.logger.error(f"Failed to enable RELAY_PWREN: {e}")
            raise

    def disable_relay_power(self):
        try:
            GPIO.output(self.relay_pwren_pin, GPIO.LOW)
            self.logger.info("RELAY_PWREN disabled")
        except Exception as e:
            self.logger.error(f"Failed to disable RELAY_PWREN: {e}")
            raise

    def disable_all_outputs(self):
        self.disable_test_mode()
        self.disable_booster()
        self.disable_relay_power()

    def close(self):
        try:
            for pin in self.output_pins:
                GPIO.output(pin, GPIO.LOW)
            GPIO.cleanup()
            self.logger.debug("GPIO cleanup done")
        except Exception as e:
            self.logger.error(f"Failed to cleanup GPIO: {e}")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


if __name__ == "__main__":
    import time

    logging.basicConfig(level=logging.DEBUG)
    with GPIOController() as pin:
        pin.enable_booster()
        pin.enable_relay_power()
        pin.enable_test_mode()
        time.sleep(100)
        pin.disable_test_mode()
        pin.disable_relay_power()
        pin.disable_booster()
