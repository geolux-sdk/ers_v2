import argparse
import math
import os
import struct
import time
import logging
from dataclasses import dataclass
from typing import Dict, List, Literal, Optional, Union

import pymodbus.exceptions
from pymodbus import FramerType
from pymodbus.client import ModbusSerialClient
from serial.tools import list_ports


DEFAULT_DEVICE_ID = 0xF1
DEFAULT_PORT = "/dev/ttyAMA2"
DEFAULT_BAUDRATE = 115200
DEFAULT_TIMEOUT = 0.5
DEFAULT_POLL_COUNT = 10
DEFAULT_POLL_INTERVAL = 1.0
DEFAULT_BUSY_RETRY_COUNT = 5
DEFAULT_BUSY_RETRY_DELAY = 0.2
MODBUS_SLAVE_DEVICE_BUSY = 6

logger = logging.getLogger(__name__)

RegisterValue = Union[int, float]


@dataclass(frozen=True)
class RegisterDefinition:
    """Metadata for a Modbus register."""

    address: int
    offset: int
    key: str
    title: str
    description: str
    scale: str
    default_value: Optional[RegisterValue] = None
    writable_while_running: bool = True
    data_type: Literal["uint16", "int16", "float32"] = "uint16"

    @property
    def register_count(self) -> int:
        return 2 if self.data_type == "float32" else 1

    @property
    def end_offset(self) -> int:
        return self.offset + self.register_count


HOLDING_REGISTERS = [
    RegisterDefinition(
        address=40001,
        offset=0,
        key="control_start_stop",
        title="Control Start/Stop",
        description="1: start, 0: stop",
        scale="Raw",
        default_value=0,
    ),
    RegisterDefinition(
        address=40002,
        offset=1,
        key="target_voltage",
        title="Target Voltage",
        description="Target output voltage",
        scale="1 means 1 V",
        default_value=30,
        writable_while_running=False,
    ),
    RegisterDefinition(
        address=40003,
        offset=2,
        key="target_load_current",
        title="Target Load Current",
        description="Target load current",
        scale="1 means 1 mA",
        default_value=100,
        writable_while_running=False,
    ),
    RegisterDefinition(
        address=40004,
        offset=3,
        key="input_voltage_gain",
        title="Input Voltage Gain",
        description="ADC calibration gain for input voltage",
        scale="uint",
        default_value=150,
    ),
    RegisterDefinition(
        address=40005,
        offset=4,
        key="input_voltage_offset",
        title="Input Voltage Offset",
        description="ADC calibration offset for input voltage",
        scale="1 means 1 mV",
        default_value=0,
    ),
    RegisterDefinition(
        address=40006,
        offset=5,
        key="load_voltage_gain",
        title="Load Voltage Gain",
        description="ADC calibration gain for load voltage",
        scale="uint",
        default_value=150,
    ),
    RegisterDefinition(
        address=40007,
        offset=6,
        key="load_voltage_offset",
        title="Load Voltage Offset",
        description="ADC calibration offset for load voltage",
        scale="1 means 1 mV",
        default_value=0,
    ),
    RegisterDefinition(
        address=40008,
        offset=7,
        key="load_current_gain",
        title="Load Current Gain",
        description="ADC calibration gain for load current",
        scale="1 means 1 mA/V",
        default_value=1300,
    ),
    RegisterDefinition(
        address=40009,
        offset=8,
        key="load_current_offset",
        title="Load Current Offset",
        description="ADC calibration offset for load current",
        scale="1 means 1 mV",
        default_value=20,
    ),
    RegisterDefinition(
        address=40010,
        offset=9,
        key="overvoltage_threshold",
        title="Overvoltage Threshold",
        description="Protection trip point for output voltage",
        scale="1 means 1 V",
        default_value=400,
    ),
    RegisterDefinition(
        address=40011,
        offset=10,
        key="overcurrent_threshold",
        title="Overcurrent Threshold",
        description="Protection trip point for output current",
        scale="1 means 1 mA",
        default_value=2000,
    ),
    RegisterDefinition(
        address=40012,
        offset=11,
        key="soft_start_rate",
        title="Soft Start Rate",
        description="Soft-start current ramp rate",
        scale="1 means 1 mA/s",
        default_value=5000,
        writable_while_running=False,
    ),
    RegisterDefinition(
        address=40013,
        offset=12,
        key="kp_cv",
        title="Kp_cv",
        description="Kp gain in the outer CV loop",
        scale="float32",
        default_value=0.01,
        writable_while_running=False,
        data_type="float32",
    ),
    RegisterDefinition(
        address=40015,
        offset=14,
        key="ki_cv",
        title="Ki_cv",
        description="Ki gain in the outer CV loop",
        scale="float32",
        default_value=0.002,
        writable_while_running=False,
        data_type="float32",
    ),
    RegisterDefinition(
        address=40017,
        offset=16,
        key="kd_cv",
        title="Kd_cv",
        description="Kd gain in the outer CV loop",
        scale="float32",
        default_value=0,
        writable_while_running=False,
        data_type="float32",
    ),
    RegisterDefinition(
        address=40019,
        offset=18,
        key="kp_cc",
        title="Kp_cc",
        description="Kp gain in the inner CC loop",
        scale="float32",
        default_value=0.5,
        writable_while_running=False,
        data_type="float32",
    ),
    RegisterDefinition(
        address=40021,
        offset=20,
        key="ki_cc",
        title="Ki_cc",
        description="Ki gain in the inner CC loop",
        scale="float32",
        default_value=0.01,
        writable_while_running=False,
        data_type="float32",
    ),
    RegisterDefinition(
        address=40023,
        offset=22,
        key="kd_cc",
        title="Kd_cc",
        description="Kd gain in the inner CC loop",
        scale="float32",
        default_value=0,
        writable_while_running=False,
        data_type="float32",
    ),
]

INPUT_REGISTERS = [
    RegisterDefinition(
        address=30001,
        offset=0,
        key="load_voltage",
        title="Load Voltage",
        description="Measured load voltage",
        scale="1 means 1 V",
    ),
    RegisterDefinition(
        address=30002,
        offset=1,
        key="load_current",
        title="Load Current",
        description="Measured load current",
        scale="1 means 1 mA",
        data_type="int16",
    ),
    RegisterDefinition(
        address=30003,
        offset=2,
        key="input_voltage",
        title="Input Voltage",
        description="Measured input voltage",
        scale="1 means 1 V",
    ),
    RegisterDefinition(
        address=30004,
        offset=3,
        key="error_status",
        title="Error Status",
        description="Bit 0: Overvoltage, Bit 1: Overcurrent, Bit 2: Overtemperature",
        scale="Bit field",
    ),
]


HOLDING_REG_NAMES = [register.key for register in HOLDING_REGISTERS]
INPUT_REG_NAMES = [register.key for register in INPUT_REGISTERS]

HOLDING_REGISTER_MAP = {
    register.key: register for register in HOLDING_REGISTERS
}

INPUT_REGISTER_MAP = {
    register.key: register for register in INPUT_REGISTERS
}

HOLDING_REGISTER_WORD_COUNT = max(
    register.end_offset for register in HOLDING_REGISTERS
)

INPUT_REGISTER_WORD_COUNT = max(
    register.end_offset for register in INPUT_REGISTERS
)

RUNTIME_LOCKED_HOLDING_KEYS = {
    register.key
    for register in HOLDING_REGISTERS
    if not register.writable_while_running
}

ERROR_STATUS_BITS = {
    0: "Overvoltage",
    1: "Overcurrent",
    2: "Overtemperature",
}

REGISTER_NAME_ALIASES = {
    "start_stop": "control_start_stop",
    "current_limit": "target_load_current",
    "soft_start": "soft_start_rate",
    "ramp_rate": "soft_start_rate",
    "voltage_gain": "load_voltage_gain",
    "voltage_offset": "load_voltage_offset",
    "current_gain": "load_current_gain",
    "current_offset": "load_current_offset",
    "over_voltage": "overvoltage_threshold",
    "over_current": "overcurrent_threshold",
    "controller_kp": "kp_cv",
    "controller_ki": "ki_cv",
    "controller_kd": "kd_cv",
    "kp_inner": "kp_cc",
    "ki_inner": "ki_cc",
    "kd_inner": "kd_cc",
}


@dataclass
class ModbusConnectionSettings:
    """Connection parameters for the Modbus RTU client."""

    port: str = DEFAULT_PORT
    baudrate: int = DEFAULT_BAUDRATE
    timeout: float = DEFAULT_TIMEOUT
    device_id: int = DEFAULT_DEVICE_ID
    parity: str = "N"
    stopbits: int = 1
    bytesize: int = 8


class ModbusBusyError(RuntimeError):
    """Raised when the slave reports it is temporarily busy."""


def parse_int(value: str) -> int:
    """Parse a decimal or hex integer."""
    return int(value, 0)


def get_available_serial_ports() -> List[str]:
    """Return the currently available serial port names."""
    return [port.device for port in list_ports.comports()]


def is_slave_device_busy_response(result: object) -> bool:
    """Return True when a Modbus exception response reports slave-device-busy."""
    return (
        getattr(result, "isError", lambda: False)()
        and getattr(result, "exception_code", None) == MODBUS_SLAVE_DEVICE_BUSY
    )


def normalize_register_values(values: List[int]) -> List[int]:
    """Normalize signed values to the 16-bit register range expected by Modbus."""
    normalized_values = []

    for value in values:
        if not -32768 <= value <= 65535:
            raise ValueError(
                f"Register value {value} is out of range. Use -32768..65535."
            )

        normalized_values.append(value & 0xFFFF)

    return normalized_values


def encode_register_value(
    definition: RegisterDefinition,
    value: RegisterValue,
) -> List[int]:
    """Encode a logical register value into one or more Modbus words."""
    if definition.data_type == "float32":
        float_value = float(value)

        if not math.isfinite(float_value):
            raise ValueError(f"{definition.title} must be a finite float.")

        packed = struct.pack(">f", float_value)

        return [
            int.from_bytes(packed[:2], byteorder="big"),
            int.from_bytes(packed[2:], byteorder="big"),
        ]

    if isinstance(value, float) and not value.is_integer():
        raise ValueError(f"{definition.title} requires an integer value.")

    int_value = int(value)

    if definition.data_type == "int16":
        if not -32768 <= int_value <= 32767:
            raise ValueError(f"{definition.title} must be in -32768..32767.")
    else:
        if not 0 <= int_value <= 65535:
            raise ValueError(f"{definition.title} must be in 0..65535.")

    return [int_value & 0xFFFF]


def get_default_holding_values() -> List[int]:
    """Return the configured default values for all holding registers."""
    missing_defaults = [
        register.title
        for register in HOLDING_REGISTERS
        if register.default_value is None
    ]

    if missing_defaults:
        missing_text = ", ".join(missing_defaults)
        raise ValueError(f"Missing default values for holding registers: {missing_text}")

    default_values = [0] * HOLDING_REGISTER_WORD_COUNT

    for register in HOLDING_REGISTERS:
        encoded_value = encode_register_value(register, register.default_value)
        default_values[register.offset:register.end_offset] = encoded_value

    return default_values


def decode_register_value(
    registers: List[int],
    definition: RegisterDefinition,
) -> RegisterValue:
    """Decode one logical register value from one or more Modbus words."""
    if len(registers) != definition.register_count:
        raise ValueError(
            f"{definition.title} expects {definition.register_count} register(s), "
            f"received {len(registers)}."
        )

    if definition.data_type == "float32":
        packed = registers[0].to_bytes(2, byteorder="big") + registers[1].to_bytes(
            2, byteorder="big"
        )
        return struct.unpack(">f", packed)[0]

    register = registers[0]

    if definition.data_type == "int16" and register & 0x8000:
        return register - 0x10000

    return register


def get_definitions_in_range(
    definitions: List[RegisterDefinition],
    addr: int,
    quantity: int,
) -> List[RegisterDefinition]:
    """Return logical definitions fully covered by a raw Modbus word range."""
    if quantity <= 0:
        raise ValueError("Quantity must be at least 1.")

    word_count = max(definition.end_offset for definition in definitions)
    end = addr + quantity

    if end > word_count:
        raise ValueError("Requested register range exceeds available register words")

    covered_definitions = []

    for definition in definitions:
        overlaps = definition.offset < end and definition.end_offset > addr

        if not overlaps:
            continue

        if definition.offset < addr or definition.end_offset > end:
            raise ValueError(
                f"Requested range splits {definition.title}. "
                "Read the full logical register range instead."
            )

        covered_definitions.append(definition)

    return covered_definitions


def resolve_holding_key(register_name: str) -> str:
    """Resolve legacy register aliases to the current holding-register keys."""
    return REGISTER_NAME_ALIASES.get(register_name, register_name)


def calculate_zero_current_offset(
    measured_load_current_ma: float,
    load_current_gain: RegisterValue,
    current_offset_mv: RegisterValue,
) -> int:
    """Return the offset register value needed to make the measured current read zero."""
    gain = float(load_current_gain)

    if not math.isfinite(gain) or gain <= 0.0:
        raise ValueError("Load Current Gain must be a finite value greater than 0.")

    measured_current = float(measured_load_current_ma)
    offset_mv = float(current_offset_mv)

    if not math.isfinite(measured_current) or not math.isfinite(offset_mv):
        raise ValueError("Load current calibration inputs must be finite values.")

    corrected_offset_mv = offset_mv + ((measured_current * 1000.0) / gain)
    corrected_offset_int = int(round(corrected_offset_mv))

    if not 0 <= corrected_offset_int <= 65535:
        raise ValueError(
            "Calculated Load Current Offset is out of range (0..65535 mV)."
        )

    return corrected_offset_int


def build_client(settings: ModbusConnectionSettings) -> ModbusSerialClient:
    """Create a Modbus serial client for the requested port."""
    return ModbusSerialClient(
        port=settings.port,
        framer=FramerType.RTU,
        baudrate=settings.baudrate,
        parity=settings.parity,
        stopbits=settings.stopbits,
        bytesize=settings.bytesize,
        timeout=settings.timeout,
    )


def ensure_connection(client: ModbusSerialClient, port: str) -> None:
    """Open the serial connection or raise a useful error."""
    if client.connect():
        return

    available_ports = get_available_serial_ports()
    ports_text = ", ".join(available_ports) if available_ports else "none"

    raise ConnectionError(
        f"Failed to open serial port '{port}'. Available ports: {ports_text}. "
        "Use --port or MODBUS_PORT to select the correct device."
    )


def read_input_registers(
    client: ModbusSerialClient,
    addr: int,
    quantity: int,
    device_id: int,
) -> Dict[str, RegisterValue]:
    """Read input registers from the Modbus server."""
    try:
        definitions = get_definitions_in_range(INPUT_REGISTERS, addr, quantity)

        for attempt in range(1, DEFAULT_BUSY_RETRY_COUNT + 2):
            result = client.read_input_registers(
                addr,
                count=quantity,
                slave=device_id,
            )

            if not result.isError():
                break

            if is_slave_device_busy_response(result):
                if attempt <= DEFAULT_BUSY_RETRY_COUNT:
                    time.sleep(DEFAULT_BUSY_RETRY_DELAY)
                    continue

                raise ModbusBusyError(
                    "Modbus slave device busy after "
                    f"{attempt} attempts - {result}"
                )

            raise RuntimeError(f"Modbus read_input_registers error - {result}")

        values = {}

        for definition in definitions:
            start = definition.offset - addr
            registers = result.registers[start:start + definition.register_count]
            values[definition.key] = decode_register_value(registers, definition)

        return values

    except pymodbus.exceptions.ConnectionException as e:
        raise ConnectionError(f"Failed to connect to Modbus server: {e}") from e

    except ModbusBusyError:
        raise

    except Exception as e:
        raise RuntimeError(f"Failed to read input registers: {e}") from e


def read_holding_registers(
    client: ModbusSerialClient,
    addr: int,
    quantity: int,
    device_id: int,
) -> Dict[str, RegisterValue]:
    """Read holding registers from the Modbus server."""
    try:
        definitions = get_definitions_in_range(HOLDING_REGISTERS, addr, quantity)

        for attempt in range(1, DEFAULT_BUSY_RETRY_COUNT + 2):
            result = client.read_holding_registers(
                addr,
                count=quantity,
                slave=device_id,
            )

            if not result.isError():
                break

            if is_slave_device_busy_response(result):
                if attempt <= DEFAULT_BUSY_RETRY_COUNT:
                    time.sleep(DEFAULT_BUSY_RETRY_DELAY)
                    continue

                raise ModbusBusyError(
                    "Modbus slave device busy after "
                    f"{attempt} attempts - {result}"
                )

            raise RuntimeError(f"Modbus read_holding_registers error - {result}")

        values = {}

        for definition in definitions:
            start = definition.offset - addr
            registers = result.registers[start:start + definition.register_count]
            values[definition.key] = decode_register_value(registers, definition)

        return values

    except pymodbus.exceptions.ConnectionException as e:
        raise ConnectionError(f"Failed to connect to Modbus server: {e}") from e

    except ModbusBusyError:
        raise

    except Exception as e:
        raise RuntimeError(f"Failed to read holding registers: {e}") from e


def write_holding_registers(
    client: ModbusSerialClient,
    addr: int,
    values: List[int],
    device_id: int,
) -> bool:
    """Write values to holding registers on the Modbus server."""
    try:
        normalized_values = normalize_register_values(values)

        for attempt in range(1, DEFAULT_BUSY_RETRY_COUNT + 2):
            result = client.write_registers(
                addr,
                values=normalized_values,
                slave=device_id,
            )

            if not result.isError():
                return True

            if is_slave_device_busy_response(result):
                if attempt <= DEFAULT_BUSY_RETRY_COUNT:
                    time.sleep(DEFAULT_BUSY_RETRY_DELAY)
                    continue

                raise ModbusBusyError(
                    "Modbus slave device busy after "
                    f"{attempt} attempts - {result}"
                )

            raise RuntimeError(f"Modbus write_holding_registers error - {result}")

        return True

    except pymodbus.exceptions.ConnectionException as e:
        raise ConnectionError(f"Failed to connect to Modbus server: {e}") from e

    except ModbusBusyError:
        raise

    except Exception as e:
        raise RuntimeError(f"Failed to write holding registers: {e}") from e


def write_holding_registers_by_names(
    client: ModbusSerialClient,
    device_id: int,
    **kwargs,
) -> List[bool]:
    """Write values to holding registers specified by their names."""
    ret_values = []

    for key, value in kwargs.items():
        resolved_key = resolve_holding_key(key)

        if resolved_key in HOLDING_REGISTER_MAP:
            try:
                definition = HOLDING_REGISTER_MAP[resolved_key]
                encoded_value = encode_register_value(definition, value)

                result = write_holding_registers(
                    client,
                    definition.offset,
                    encoded_value,
                    device_id,
                )

                ret_values.append(result)

            except ModbusBusyError:
                raise

            except Exception as e:
                logger.error("%s register write failed: %s", resolved_key, e)
                ret_values.append(False)
        else:
            logger.error("%s is not defined in HOLDING_REGISTERS.", key)
            ret_values.append(False)

    return ret_values


class ModbusService:
    """Reusable Modbus RTU service for CLI and GUI callers."""

    def __init__(self, settings: ModbusConnectionSettings):
        self.settings = settings
        self.client = build_client(settings)

    def connect(self) -> None:
        ensure_connection(self.client, self.settings.port)

    def close(self) -> None:
        self.client.close()

    def read_input_registers(
        self,
        addr: int = 0,
        quantity: Optional[int] = None,
    ) -> Dict[str, RegisterValue]:
        if quantity is None:
            quantity = INPUT_REGISTER_WORD_COUNT - addr

        return read_input_registers(
            self.client,
            addr=addr,
            quantity=quantity,
            device_id=self.settings.device_id,
        )

    def read_holding_registers(
        self,
        addr: int = 0,
        quantity: Optional[int] = None,
    ) -> Dict[str, RegisterValue]:
        if quantity is None:
            quantity = HOLDING_REGISTER_WORD_COUNT - addr

        return read_holding_registers(
            self.client,
            addr=addr,
            quantity=quantity,
            device_id=self.settings.device_id,
        )

    def write_holding_registers(self, addr: int, values: List[int]) -> bool:
        return write_holding_registers(
            self.client,
            addr=addr,
            values=values,
            device_id=self.settings.device_id,
        )

    def write_default_holding_registers(self) -> bool:
        return self.write_holding_registers(0, get_default_holding_values())

    def write_holding_registers_by_names(self, **kwargs) -> List[bool]:
        return write_holding_registers_by_names(
            self.client,
            self.settings.device_id,
            **kwargs,
        )


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="RTU Modbus master example")

    parser.add_argument(
        "--port",
        default=os.environ.get("MODBUS_PORT", DEFAULT_PORT),
        help=f"Serial port to use. Default: {DEFAULT_PORT}",
    )

    parser.add_argument(
        "--baudrate",
        type=int,
        default=DEFAULT_BAUDRATE,
        help=f"Serial baudrate. Default: {DEFAULT_BAUDRATE}",
    )

    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help=f"Read timeout in seconds. Default: {DEFAULT_TIMEOUT}",
    )

    parser.add_argument(
        "--device-id",
        type=parse_int,
        default=DEFAULT_DEVICE_ID,
        help=f"Modbus device id in decimal or hex. Default: {DEFAULT_DEVICE_ID:#x}",
    )

    parser.add_argument(
        "--poll-count",
        type=int,
        default=DEFAULT_POLL_COUNT,
        help=f"Number of input-register polling iterations. Default: {DEFAULT_POLL_COUNT}",
    )

    parser.add_argument(
        "--poll-interval",
        type=float,
        default=DEFAULT_POLL_INTERVAL,
        help=f"Delay between polls in seconds. Default: {DEFAULT_POLL_INTERVAL}",
    )

    parser.add_argument(
        "--read-only",
        action="store_true",
        help="Skip register writes and perform read operations only",
    )

    parser.add_argument(
        "--gui",
        action="store_true",
        help="Launch the PySide6 desktop UI instead of the CLI example",
    )

    return parser.parse_args()


def args_to_settings(args: argparse.Namespace) -> ModbusConnectionSettings:
    """Build connection settings from parsed CLI arguments."""
    return ModbusConnectionSettings(
        port=args.port,
        baudrate=args.baudrate,
        timeout=args.timeout,
        device_id=args.device_id,
    )


def run_cli(
    settings: ModbusConnectionSettings,
    poll_count: int = DEFAULT_POLL_COUNT,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    read_only: bool = False,
) -> None:
    """Run the CLI polling flow."""
    service = ModbusService(settings)

    try:
        service.connect()

        regs = service.read_holding_registers()
        print("Holding registers:")
        print(regs)

        if not read_only:
            result = service.write_holding_registers_by_names(control_start_stop=1)
            print("Start command result:")
            print(result)

        for _ in range(poll_count):
            time.sleep(poll_interval)

            regs = service.read_input_registers()
            print("Input registers:")
            print(regs)

        if not read_only:
            result = service.write_holding_registers_by_names(control_start_stop=0)
            print("Stop command result:")
            print(result)

    except Exception as e:
        print(f"Error: {e}")

    finally:
        service.close()


def main() -> None:
    """Program entry point."""
    args = parse_args()
    settings = args_to_settings(args)

    if args.gui:
        from modbus_gui import run_modbus_gui

        run_modbus_gui(settings)
        return

    run_cli(
        settings=settings,
        poll_count=args.poll_count,
        poll_interval=args.poll_interval,
        read_only=args.read_only,
    )


if __name__ == "__main__":
    main()
