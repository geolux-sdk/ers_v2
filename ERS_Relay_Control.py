import argparse
import logging
import struct
import sys
import time

import serial

# -----------------------------
# Module-level logger
# -----------------------------
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())

class relay_board_controller:
    def __init__(
        self,
        inport_num=13,
        outport_num=48,
        comport="/dev/ttyAMA3",
        baudrate=115200,
        port_order=None,
        timeout=1.0,
        logger=None,
    ):
        self.comport = comport
        self.baudrate = baudrate
        self.timeout = timeout
        self.outport_num = outport_num
        self.inport_num = inport_num
        self.port_order = port_order

        self.logger = logger or logging.getLogger(__name__)

    def set(self, data: list) -> bool:
        try:
            with serial.Serial(
                self.comport, self.baudrate, timeout=self.timeout
            ) as ser:

                self.logger.info(f"relay write data:{data=}")

                send_msg = self._make_sendmsg(data)

                for i in range(5):
                    ser.reset_input_buffer()
                    ser.write(send_msg)
                    time.sleep(0.5)

                    if self.__recv(ser):
                        return True
                    
                self.logger.error(f"relay receive error {i=}")

        except (OSError, serial.SerialException) as err:
            self.logger.error(f"{repr(err)}")
            return False
        return False

    def _make_sendmsg(self, data: list):
        if len(data) % 2 != 0:
            raise ValueError("data length must be even")
        for ch in data:
            if not (0 <= ch <= 0xFFFF):
                raise ValueError(f"Invalid data value: {ch}")
        if len(data)//2 > 255:
            raise ValueError("Too much data")        

        data = list(reversed(data))
        header = 0xFF
        packnum = len(data) // 2

        data_bytes = b"".join(struct.pack("<H", ch) for ch in data)
        checksum = (0 - (sum(data_bytes) + header + packnum)) & 0xFF

        send_msg = struct.pack("<3B", header, packnum, checksum) + data_bytes

        self.logger.debug("header:" + " ".join(f"{byte:02x}" for byte in send_msg[:3]))
        for i in range(0, len(send_msg) - 3, 24):
            self.logger.debug(
                f"data[{i+3},{i+24+3}]:"
                + " ".join(f"{byte:02x}" for byte in send_msg[3 + i : 3 + i + 24])
            )

        return send_msg

    @staticmethod
    def _format_packet(packet: bytes) -> str:
        return packet.hex(" ") if packet else "<empty>"

    def __recv(self, ser) -> bool:
        head_data = ser.read(3)  # 시리얼 포트로부터 데이터 수신

        if len(head_data) != 3:
            self.logger.error(
                "recv header size error: expected=3 received=%d packet=%s",
                len(head_data),
                self._format_packet(head_data),
            )
            return False
        if head_data[0] != 0xFF:
            self.logger.error(
                "invalid header: packet=%s",
                self._format_packet(head_data),
            )
            return False
        

        packnum = head_data[1]
        checksum = head_data[2]

        received_data = ser.read(packnum * 4)
        packet = head_data + received_data
        
        if len(received_data) != packnum * 4:
            self.logger.error(
                "recv data size mismatch: expected=%d received=%d packet=%s",
                packnum * 4,
                len(received_data),
                self._format_packet(packet),
            )
            return False

        sum_data = sum(received_data)
        calc_checksum = (0 - (sum_data + 0xFF + packnum)) & 0xFF

        if checksum != calc_checksum:
            self.logger.error(
                "checksum mismatch: received=0x%02X calculated=0x%02X packet=%s",
                checksum,
                calc_checksum,
                self._format_packet(packet),
            )
            return False

        if packnum != 1:
            self.logger.warning(
                "unexpected packnum=%d packet=%s",
                packnum,
                self._format_packet(packet),
            )
            return False

        return True

    def clear(self) -> bool:
        data = [0] * self.outport_num
        return self.set(data)

    def test_clear(self) -> bool:
        for _ in range(3):
            if self.clear():
                self.logger.debug("RELAY TEST Clear Success")
                return True
            time.sleep(1)

        self.logger.error("RELAY TEST Clear Fail")
        return False

    def test_all(self, delay_time=0.2) -> bool:
        ret = True
        try:
            for relay_ch in range(1, self.outport_num + 1):
                for inport in range(1, self.inport_num + 1):
                    relay_data = [0] * self.outport_num
                    relay_data[relay_ch - 1] = 0x1 << (inport - 1)

                    if self.set(relay_data):
                        self.logger.debug(
                            "success relay=%d inport=%d", relay_ch, inport
                        )
                    else:
                        self.logger.warning(
                            "fail relay=%d inport=%d", relay_ch, inport
                        )
                        ret = False

                    time.sleep(delay_time)

            self.clear()

        except Exception as err:
            self.logger.critical("Exception: %s", repr(err))
            ret = False

        return ret

    def test_circular(self, delay_time=0.2) -> bool:
        num_list = list(range(1, self.inport_num + 1))
        ret = True
        try:
            for i in range(self.outport_num):
                temp_list = (
                    [0] * i + num_list + [0] * (self.outport_num - i - len(num_list))
                )
                relay_data = temp_list[: self.outport_num]
                if len(temp_list) > self.outport_num:
                    tmep_num = len(temp_list[self.outport_num :])
                    relay_data = (
                        temp_list[self.outport_num :]
                        + temp_list[tmep_num : self.outport_num]
                    )

                for x, val in enumerate(relay_data):
                    if val != 0:
                        relay_data[x] = 0x1 << (val - 1)

                if self.set(relay_data):
                    self.logger.debug(f"{i}th Success")
                else:
                    self.logger.error(f"{i}th Fail!!!")
                    ret = False
                time.sleep(delay_time)
                self.test_clear()
                time.sleep(delay_time)
        except KeyboardInterrupt:
            raise
        except Exception as e:
            print(f"Exception: {repr(e)}")
            self.logger.critical(f"Exception: {repr(e)}")
            ret = False
        return ret


def process_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Receive data from serial port and store received data in a file"
    )
    parser.add_argument(
        "-p",
        "--port",
        default="/dev/ttyAMA3",
        type=str,
        help="COM port (default: /dev/ttyACM3)",
    )
    parser.add_argument(
        "-b",
        "--baudrate",
        default=115200,
        type=int,
        help="COM port Baudrate (default: 115200)",
    )
    parser.add_argument(
        "-o",
        "--outport_num",
        default=48,
        type=int,
        help="Relay Board Output port num (default: 48)",
    )
    parser.add_argument(
        "-i",
        "--inport_num",
        default=13,
        type=int,
        help="Relay Board Inport num (default: 13)",
    )
    parser.add_argument(
        "-s",
        "--sleep_time",
        default=1.0,
        type=float,
        help="Sleep time between Commands (default: 1.0)",
    )
    parser.add_argument(
        "-t",
        "--test",
        action="store_true",
        default=False,
        help="Relay Test (default: false)",
    )
    # parser.add_argument(
    #     "-d",
    #     "--data",
    #     nargs="+",
    #     type=int,
    #     default=[0],
    #     help="Send Data list (default: all zero)",
    # )
    args = parser.parse_args()
    return args


def main(args):
    logging.basicConfig(
        stream=sys.stdout,
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    logger = logging.getLogger("relay")

    relay = relay_board_controller(
        inport_num=args.inport_num,
        outport_num=args.outport_num,
        comport=args.port,
        baudrate=args.baudrate,
        logger=logger,
    )
    logger.debug(f"Initialized relay_board_controller with {args=}")
    try:
        if args.test:
            time.sleep(args.sleep_time)
            relay.test_clear()
            time.sleep(args.sleep_time)
            relay.test_all(delay_time=0.3)
            time.sleep(args.sleep_time)
        else:
            relay.test_circular(delay_time=0.3)

    except Exception as err:
        logger.error(f"Unexpected error: {repr(err)}")
        raise

    finally:
        relay.clear()


if __name__ == "__main__":
    args = process_args()
    main(args)
