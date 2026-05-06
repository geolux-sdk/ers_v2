# fake_gpio.py

BCM = "BCM"
OUT = "OUT"
HIGH = True
LOW = False


def setwarnings(flag):
    print(f"Setting warnings to {flag}")


def setmode(mode):
    print(f"Setting mode to {mode}")


def setup(pin, mode):
    print(f"Setting up pin {pin} to mode {mode}")


def output(pin, state):
    print(f"Setting pin {pin} to state {state}")


def cleanup():
    print("Cleaning up GPIO")
