"""NIIMBOT B1 serial protocol adapter for fixed-size label images."""
import struct
import time

import serial
from serial.tools import list_ports

from lib.application_errors import ValidationError

_PORT_ID = "VID:PID=3513:0002"
_NO_PRINTER = "找不到標籤機，請確認電源與 USB 連接線。"
_NO_RESPONSE = "標籤機沒有回應，請重新連接後再試一次。"


def build_packet(command_type, data):
    """Encode one NIIMBOT frame with the documented XOR checksum."""
    if not 0 <= command_type <= 0xFF or len(data) > 0xFF:
        raise ValueError("invalid packet")
    checksum = command_type ^ len(data)
    for byte in data:
        checksum ^= byte
    return b"\x55\x55" + bytes((command_type, len(data))) + data + bytes((checksum,)) + b"\xAA\xAA"


def parse_packet(packet):
    """Return response type and body; reject incomplete or corrupt frames."""
    if len(packet) < 7 or packet[:2] != b"\x55\x55" or packet[-2:] != b"\xAA\xAA":
        raise ValueError("invalid frame")
    size = packet[3]
    if len(packet) != size + 7:
        raise ValueError("invalid frame length")
    expected = packet[2] ^ size
    for byte in packet[4:4 + size]:
        expected ^= byte
    if packet[4 + size] != expected:
        raise ValueError("invalid checksum")
    return packet[2], packet[4:4 + size]


class PrinterReportedError(Exception):
    """機器主動回報的錯誤封包（type 219），code 為其回報的錯誤碼。"""
    def __init__(self, code):
        super().__init__(f"printer error {code}")
        self.code = code


# 實測對照：1 出現在上蓋開啟時，8 出現在卡紙／送紙異常時。
_ERROR_MESSAGES = {
    1: "標籤機上蓋未關閉，請關好後重新列印。",
    8: "標籤機卡紙或送紙異常，請開蓋將標籤紙重新裝好後再列印。",
}


def translate_printer_error(exc):
    """Translate low-level protocol exceptions at the hardware boundary."""
    if isinstance(exc, ValidationError):
        raise exc
    if isinstance(exc, PrinterReportedError):
        raise ValidationError(_ERROR_MESSAGES.get(exc.code, _NO_RESPONSE)) from exc
    raise ValidationError(_NO_RESPONSE) from exc


class LabelPrinter:
    """A synchronous, one-job NIIMBOT B1 printer. Failures never retry a job."""
    def __init__(self, serial_factory=serial.Serial, ports=list_ports.comports, sleep=time.sleep,
                 monotonic=time.monotonic, request_timeout=3, row_delay=0.002):
        self._serial_factory = serial_factory
        self._ports = ports
        self._sleep = sleep
        self._monotonic = monotonic
        self._request_timeout = request_timeout
        self._row_delay = row_delay
        self._serial = None
        self._receive_buffer = bytearray()

    def _port_name(self):
        for port in self._ports():
            if _PORT_ID in (getattr(port, "hwid", "").upper()):
                return port.device
        raise ValidationError(_NO_PRINTER)

    def _open(self):
        try:
            self._serial = self._serial_factory(self._port_name(), baudrate=115200, timeout=0.5)
            self._serial.dtr = False
            # 清掉上一個工作殘留的回應：舊的進度封包會被誤認成這次指令的回應，
            # 造成解析錯亂而時好時壞。
            self._receive_buffer.clear()
            try:
                self._serial.reset_input_buffer()
            except Exception:
                pass
        except ValidationError:
            raise
        except Exception as exc:
            raise ValidationError("無法連線標籤機，請確認未被其他程式占用。") from exc

    def _read_packet(self):
        for _ in range(6):
            if len(self._receive_buffer) >= 4:
                total = self._receive_buffer[3] + 7
                if len(self._receive_buffer) >= total:
                    packet = bytes(self._receive_buffer[:total])
                    del self._receive_buffer[:total]
                    return parse_packet(packet)
            # 先等一個位元組再把緩衝區收乾；直接 read(1024) 會等到收滿或逾時，
            # 使得每道指令都固定耗掉一次逾時（實測每次白等 500 毫秒）。
            chunk = self._serial.read(1)
            if chunk:
                pending = getattr(self._serial, "in_waiting", 0)
                if pending:
                    chunk += self._serial.read(pending)
                self._receive_buffer.extend(chunk)
                continue
            self._sleep(0.1)
        raise ValueError("printer timeout")

    def _request(self, command_type, data=b"", response_offset=1):
        self._serial.write(build_packet(command_type, data))
        expected_type = (command_type + response_offset) & 0xFF
        deadline = self._monotonic() + self._request_timeout
        while self._monotonic() < deadline:
            response_type, response = self._read_packet()
            if response_type == 219:
                raise PrinterReportedError(response[0] if response else None)
            if response_type == 0:
                raise ValueError("printer response error")
            if response_type == expected_type:
                return response
            if response_type != 0xD3:
                raise ValueError("printer response error")
        raise ValueError("printer timeout")

    def _heartbeat(self):
        data = self._request(0xDC, b"\x01")
        if len(data) < 10:
            raise ValueError("invalid heartbeat response")
        if data[9] == 1:
            raise ValidationError("標籤機上蓋未關閉，請關好後重新列印。")

    def get_print_status(self):
        """Return (printed_lines, status, error) from the status response header."""
        data = self._request(0xA3, b"\x01", 16)
        return struct.unpack(">HBB", data[:4])

    def _send_image(self, image):
        if image.width % 8:
            raise ValueError("label image width must be a multiple of 8")
        bits = image.convert("L")
        width_bytes = image.width // 8
        for y in range(image.height):
            line = 0
            for pixel in range(image.width):
                if bits.getpixel((pixel, y)) < 128:
                    line |= 1 << (image.width - pixel - 1)
            data = struct.pack(">H3BB", y, 0, 0, 0, 1) + line.to_bytes(width_bytes, "big")
            self._serial.write(build_packet(0x85, data))
            self._sleep(self._row_delay)

    def _wait_for_completion(self, copies):
        """等機器實際印完所有張數再收工。

        end_page_print 只代表「資料收到了」，此時實體列印還在進行；太早送
        end_print 會把列印中止在半途（實測截在約六成處）。機器每印完一張會把
        狀態裡的頁數加一，這是明確的完成訊號，不需要盲等固定秒數。
        """
        deadline = self._monotonic() + 15 * copies
        while self._monotonic() < deadline:
            page, _progress, _error = self.get_print_status()
            if page >= copies:
                return
            self._sleep(0.1)
        raise ValueError("print completion timeout")

    def _start_print(self):
        """等機器願意接受新工作。

        前一個工作走紙收尾期間，機器會以回應內容 0 拒絕新工作（不是回錯誤封包）。
        只看回應類型就往下送圖，資料會被整批丟棄，最後卡在等待完成。
        """
        deadline = self._monotonic() + 10
        while self._monotonic() < deadline:
            response = self._request(0x01, b"\x01")
            if response and response[0]:
                return
            self._sleep(0.2)
        raise ValueError("start print rejected")

    def _end_page_print(self):
        for _ in range(30):
            response = self._request(0xE3, b"\x01")
            if response and response[0]:
                return
            self._sleep(0.1)
        raise ValueError("page print timeout")

    def print(self, image, copies):
        try:
            self._open()
            self._heartbeat()
            self._request(0x21, b"\x05", 16)
            self._request(0x23, b"\x01", 16)
            self._start_print()
            # 每一張都要自己送一次頁面：設定張數不會讓機器自行重複列印，
            # 只送一頁卻等頁數累加到張數，會等到逾時並把工作留在未收工狀態。
            for page in range(copies):
                self._request(0x03, b"\x01")
                self._request(0x13, struct.pack(">HH", image.height, image.width))
                self._request(0x15, struct.pack(">H", 1))
                self._send_image(image)
                self._end_page_print()
                self._wait_for_completion(page + 1)
            self._request(0xF3, b"\x01")
        except Exception as exc:
            self._abort()
            translate_printer_error(exc)
        finally:
            serial_instance, self._serial = self._serial, None
            if serial_instance is not None:
                try:
                    serial_instance.close()
                except Exception:
                    pass

    def _abort(self):
        """失敗時務必送出收工指令：工作留在未收工狀態會讓機器卡住，
        使得下一次列印被回絕，症狀看起來像卡紙。"""
        try:
            self._serial.write(build_packet(0xF3, b"\x01"))
            self._sleep(0.2)
        except Exception:
            pass
