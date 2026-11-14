"""Minimal IPP server: makes the Niimbot a normal Windows printer queue.

Windows has no way to install an unsigned kernel-mode printer driver, but it ships
the signed **Microsoft IPP Class Driver**. Registering our endpoint as an IPP
printer therefore needs no driver of our own:

    Add-PrinterPort -Name "http://127.0.0.1:8765/ipp/print"
    Add-Printer -Name "Niimbot B1" -DriverName "Microsoft IPP Class Driver" -PortName "..."

Windows then sends every job (PWG Raster / PDF / JPEG) to us; we decode it, scale
it onto the label format detected from the RFID tag, and print over Bluetooth.

Implemented operations (IPP/2.0 subset): Get-Printer-Attributes, Validate-Job,
Print-Job, Create-Job, Send-Document, Cancel-Job, Get-Job-Attributes, Get-Jobs.
"""

from __future__ import annotations

import io
import logging
import struct
from dataclasses import dataclass, field

from PIL import Image

log = logging.getLogger(__name__)

# ------------------------------------------------------------------ constants
OP_GET_PRINTER_ATTRIBUTES = 0x000B
OP_PRINT_JOB = 0x0002
OP_VALIDATE_JOB = 0x0004
OP_CREATE_JOB = 0x0005
OP_SEND_DOCUMENT = 0x0006
OP_CANCEL_JOB = 0x0008
OP_GET_JOB_ATTRIBUTES = 0x0009
OP_GET_JOBS = 0x000A

STATUS_OK = 0x0000
STATUS_BAD_REQUEST = 0x0400
STATUS_NOT_POSSIBLE = 0x0403
STATUS_NOT_FOUND = 0x0406

TAG_OPERATION = 0x01
TAG_JOB = 0x02
TAG_END = 0x03
TAG_PRINTER = 0x04
TAG_UNSUPPORTED = 0x05

T_CHARSET = 0x47
T_NATURAL_LANGUAGE = 0x48
T_TEXT = 0x41
T_NAME = 0x42
T_KEYWORD = 0x44
T_URI = 0x45
T_MIME = 0x49
T_INTEGER = 0x21
T_BOOLEAN = 0x22
T_ENUM = 0x23
T_RESOLUTION = 0x32
T_RANGE = 0x33
T_COLLECTION = 0x34
T_BEGIN_COLLECTION = 0x34
T_END_COLLECTION = 0x37
T_MEMBER_NAME = 0x4A


@dataclass
class IppAttribute:
    tag: int
    name: str
    value: bytes


@dataclass
class IppRequest:
    version: tuple[int, int]
    operation: int
    request_id: int
    groups: dict[int, list[IppAttribute]] = field(default_factory=dict)
    document: bytes = b""

    def get(self, name: str, default: str | None = None) -> str | None:
        for attributes in self.groups.values():
            for attribute in attributes:
                if attribute.name == name:
                    return _decode_value(attribute)
        return default

    def get_int(self, name: str, default: int | None = None) -> int | None:
        for attributes in self.groups.values():
            for attribute in attributes:
                if attribute.name == name and attribute.tag in (T_INTEGER, T_ENUM):
                    return int.from_bytes(attribute.value, "big")
        return default


def _decode_value(attribute: IppAttribute) -> str:
    if attribute.tag in (T_INTEGER, T_ENUM):
        return str(int.from_bytes(attribute.value, "big"))
    if attribute.tag == T_BOOLEAN:
        return "true" if attribute.value and attribute.value[0] else "false"
    if attribute.tag == T_RESOLUTION and len(attribute.value) >= 9:
        x = int.from_bytes(attribute.value[0:4], "big")
        y = int.from_bytes(attribute.value[4:8], "big")
        units = attribute.value[8]
        return f"{x}x{y}{'dpi' if units == 3 else 'dpcm'}"
    try:
        if attribute.tag in (T_MIME, T_KEYWORD, T_URI, T_TEXT, T_NAME, T_CHARSET, T_NATURAL_LANGUAGE):
            if len(attribute.value) >= 4:
                # textWithLanguage/nameWithLanguage carry two length-prefixed strings.
                return attribute.value.decode("utf-8", "replace")
            return attribute.value.decode("utf-8", "replace")
    except Exception:  # pragma: no cover - defensive
        pass
    return attribute.value.hex()


# ------------------------------------------------------------------- parsing
def parse_request(body: bytes) -> IppRequest:
    if len(body) < 8:
        raise ValueError("IPP request is too short")
    major, minor, operation, request_id = struct.unpack(">BBHI", body[:8])
    request = IppRequest(version=(major, minor), operation=operation, request_id=request_id)
    index = 8
    current_group = TAG_OPERATION
    pending_name: str | None = None
    pending_tag = 0
    pending_value = b""

    while index < len(body):
        tag = body[index]
        index += 1
        if tag == TAG_END:
            request.document = body[index:]
            break
        if tag in (TAG_OPERATION, TAG_JOB, TAG_PRINTER, TAG_UNSUPPORTED):
            current_group = tag
            request.groups.setdefault(tag, [])
            continue
        if tag in (T_END_COLLECTION,):
            continue
        if tag == T_BEGIN_COLLECTION:
            if pending_name:
                request.groups.setdefault(current_group, []).append(
                    IppAttribute(pending_tag, pending_name, pending_value)
                )
                pending_name = None
            index += 2
            name_len = int.from_bytes(body[index : index + 2], "big")
            index += 2 + name_len
            index += 2
            continue
        if index + 2 > len(body):
            break
        name_len = int.from_bytes(body[index : index + 2], "big")
        index += 2
        name = body[index : index + name_len].decode("utf-8", "replace")
        index += name_len
        if index + 2 > len(body):
            break
        value_len = int.from_bytes(body[index : index + 2], "big")
        index += 2
        value = body[index : index + value_len]
        index += value_len

        if name:
            request.groups.setdefault(current_group, []).append(IppAttribute(tag, name, value))
            pending_name = name
        elif pending_name:
            # Additional value for the previous attribute (multi-value).
            request.groups.setdefault(current_group, []).append(IppAttribute(tag, pending_name, value))
        else:
            pending_name = name
    return request


# ------------------------------------------------------------------ encoding
class _Writer:
    def __init__(self) -> None:
        self.buffer = bytearray()

    def group(self, tag: int) -> None:
        self.buffer.append(tag)

    def attr(self, tag: int, name: str, value: bytes) -> None:
        encoded = name.encode("utf-8")
        self.buffer.append(tag)
        self.buffer += len(encoded).to_bytes(2, "big")
        self.buffer += encoded
        self.buffer += len(value).to_bytes(2, "big")
        self.buffer += value

    def text(self, name: str, value: str) -> None:
        self.attr(T_TEXT, name, value.encode("utf-8"))

    def name_value(self, name: str, value: str) -> None:
        self.attr(T_NAME, name, value.encode("utf-8"))

    def keyword(self, name: str, value: str) -> None:
        self.attr(T_KEYWORD, name, value.encode("utf-8"))

    def uri(self, name: str, value: str) -> None:
        self.attr(T_URI, name, value.encode("utf-8"))

    def mime(self, name: str, value: str) -> None:
        self.attr(T_MIME, name, value.encode("utf-8"))

    def integer(self, name: str, value: int, tag: int = T_INTEGER) -> None:
        self.attr(tag, name, int(value).to_bytes(4, "big"))

    def boolean(self, name: str, value: bool) -> None:
        self.attr(T_BOOLEAN, name, b"\x01" if value else b"\x00")

    def resolution(self, name: str, dpi: int) -> None:
        self.attr(T_RESOLUTION, name, struct.pack(">IIB", dpi, dpi, 3))

    def range_of_integer(self, name: str, low: int, high: int) -> None:
        self.attr(T_RANGE, name, struct.pack(">II", low, high))

    def collection(self, name: str, members: list[tuple[int, str, bytes]]) -> None:
        encoded = name.encode("utf-8")
        self.buffer.append(T_BEGIN_COLLECTION)
        self.buffer += len(encoded).to_bytes(2, "big")
        self.buffer += encoded
        self.buffer += b"\x00\x00"
        for tag, member, value in members:
            encoded_member = member.encode("utf-8")
            self.buffer.append(T_MEMBER_NAME)
            self.buffer += len(encoded_member).to_bytes(2, "big")
            self.buffer += encoded_member
            self.buffer += len(value).to_bytes(2, "big")
            self.buffer += value
        self.buffer.append(T_END_COLLECTION)
        self.buffer += b"\x00\x00\x00\x00"

    def finish(self, version: tuple[int, int], status: int, request_id: int) -> bytes:
        head = struct.pack(">BBHI", version[0], version[1], status, request_id)
        return head + bytes(self.buffer) + bytes((TAG_END,))


# --------------------------------------------------------------- job handling
@dataclass
class IppPrinterState:
    """What the IPP endpoint advertises and how it turns jobs into label prints."""

    name: str
    make_and_model: str
    dpi: int = 203
    width_mm: float = 40.0
    height_mm: float = 30.0
    formats: tuple[str, ...] = ("image/pwg-raster", "application/pdf", "image/jpeg", "image/png")
    accepting: bool = True
    jobs: int = 0


def _media_name(state: IppPrinterState) -> str:
    return f"custom_{state.width_mm:g}x{state.height_mm:g}mm"


def build_printer_attributes(state: IppPrinterState, base_uri: str) -> bytes:
    writer = _Writer()
    writer.group(TAG_OPERATION)
    writer.attr(T_CHARSET, "attributes-charset", b"utf-8")
    writer.attr(T_NATURAL_LANGUAGE, "attributes-natural-language", b"en")

    writer.group(TAG_PRINTER)
    writer.uri("printer-uri-supported", base_uri)
    writer.keyword("uri-authentication-supported", "none")
    writer.keyword("uri-security-supported", "none")
    writer.name_value("printer-name", state.name)
    writer.text("printer-info", state.make_and_model)
    writer.text("printer-make-and-model", state.make_and_model)
    writer.text("printer-location", "Niibot Studio")
    writer.integer("printer-state", 3, T_ENUM)  # idle
    writer.keyword("printer-state-reasons", "none")
    writer.boolean("printer-is-accepting-jobs", state.accepting)
    writer.integer("queued-job-count", 0)
    writer.keyword("ipp-versions-supported", "1.1")
    writer.keyword("ipp-versions-supported", "2.0")
    for operation in (
        OP_PRINT_JOB,
        OP_VALIDATE_JOB,
        OP_CREATE_JOB,
        OP_SEND_DOCUMENT,
        OP_CANCEL_JOB,
        OP_GET_JOB_ATTRIBUTES,
        OP_GET_JOBS,
        OP_GET_PRINTER_ATTRIBUTES,
    ):
        writer.integer("operations-supported", operation, T_ENUM)
    writer.attr(T_CHARSET, "charset-configured", b"utf-8")
    writer.attr(T_CHARSET, "charset-supported", b"utf-8")
    writer.attr(T_NATURAL_LANGUAGE, "natural-language-configured", b"en")
    for mime in state.formats:
        writer.mime("document-format-supported", mime)
    writer.mime("document-format-default", "image/pwg-raster")
    writer.resolution("printer-resolution-default", state.dpi)
    writer.resolution("printer-resolution-supported", state.dpi)
    writer.resolution("pwg-raster-document-resolution-supported", state.dpi)
    writer.keyword("pwg-raster-document-type-supported", "black_1")
    writer.keyword("pwg-raster-document-type-supported", "sgray_8")
    writer.keyword("pwg-raster-document-sheet-back", "normal")
    writer.boolean("color-supported", False)
    writer.keyword("print-color-mode-default", "monochrome")
    writer.keyword("print-color-mode-supported", "monochrome")
    writer.keyword("sides-default", "one-sided")
    writer.keyword("sides-supported", "one-sided")
    writer.range_of_integer("copies-supported", 1, 99)
    writer.integer("copies-default", 1)
    writer.integer("orientation-requested-default", 3, T_ENUM)
    for value in (3, 4, 5, 6):
        writer.integer("orientation-requested-supported", value, T_ENUM)

    media = _media_name(state)
    writer.keyword("media-default", media)
    writer.keyword("media-ready", media)
    writer.keyword("media-supported", media)
    writer.collection(
        "media-col-database",
        [
            (0x21, "x-dimension", int(round(state.width_mm * 100)).to_bytes(4, "big")),
            (0x21, "y-dimension", int(round(state.height_mm * 100)).to_bytes(4, "big")),
        ],
    )
    writer.collection(
        "media-col-default",
        [
            (0x21, "x-dimension", int(round(state.width_mm * 100)).to_bytes(4, "big")),
            (0x21, "y-dimension", int(round(state.height_mm * 100)).to_bytes(4, "big")),
        ],
    )
    return writer.finish((2, 0), STATUS_OK, 1)


def build_simple_response(
    request: IppRequest,
    status: int = STATUS_OK,
    job_id: int | None = None,
    job_state: int = 9,
    message: str | None = None,
) -> bytes:
    writer = _Writer()
    writer.group(TAG_OPERATION)
    writer.attr(T_CHARSET, "attributes-charset", b"utf-8")
    writer.attr(T_NATURAL_LANGUAGE, "attributes-natural-language", b"en")
    if message:
        writer.text("status-message", message)
    if job_id is not None:
        writer.group(TAG_JOB)
        writer.integer("job-id", job_id)
        writer.uri("job-uri", f"ipp://localhost/jobs/{job_id}")
        writer.integer("job-state", job_state, T_ENUM)
        writer.keyword("job-state-reasons", "none")
    return writer.finish(request.version, status, request.request_id)


# ---------------------------------------------------------------- decoding
def decode_document(payload: bytes, mime: str, dpi: int = 203) -> Image.Image | None:
    """Turn an IPP document into a PIL image we can fit onto the label.

    ``dpi`` is the target printer's resolution — vector documents are rasterised at it,
    so a 300 dpi device gets a 300 dpi render instead of an upscaled 203 dpi one.
    """
    if not payload:
        return None

    if payload[:4] in (b"RaS2", b"RaS3"):
        return decode_pwg_raster(payload)
    if payload[:4] == b"%PDF" or mime == "application/pdf":
        return decode_pdf(payload, dpi=dpi)
    try:
        image = Image.open(io.BytesIO(payload))
        image.load()
        return image
    except Exception as exc:  # pragma: no cover - depends on input
        log.warning("Could not decode the document (%s): %s", mime, exc)
        return None


def decode_pdf(payload: bytes, dpi: int = 203) -> Image.Image | None:
    try:  # optional dependency, only needed for PDF jobs
        import pypdfium2 as pdfium
    except ImportError:
        log.warning("pypdfium2 is missing — cannot print a PDF from Windows")
        return None
    try:
        document = pdfium.PdfDocument(payload)
        page = document[0]
        # Rasterise at the printer's own resolution so a full-page design lands at print size.
        bitmap = page.render(scale=max(72, int(dpi)) / 72)
        return bitmap.to_pil()
    except Exception as exc:  # pragma: no cover
        log.warning("PDF rendering failed: %s", exc)
        return None


def decode_pwg_raster(payload: bytes) -> Image.Image | None:
    """Decode a PWG Raster (the IPP Everywhere baseline Windows sends)."""
    if len(payload) < 1796:
        return None
    magic = payload[:4]
    width, height, bits_per_color, bits_per_pixel, bytes_per_line = struct.unpack(">IIIII", payload[8:28])
    color_space, = struct.unpack(">I", payload[32:36])
    compression, = struct.unpack(">I", payload[36:40])
    if magic == b"RaS3":
        # 16-bit per component: take the most significant byte.
        bits_per_pixel = max(1, bits_per_pixel // 2)
    if width <= 0 or height <= 0 or bytes_per_line <= 0:
        return None

    data = payload[1796:]
    index = 0
    rows: list[bytes] = []
    while len(rows) < height and index < len(data):
        repeat = data[index]
        index += 1
        if repeat == 0:
            break
        if compression == 1:
            line = bytearray()
            needed = bytes_per_line
            while needed > 0 and index < len(data):
                control = data[index]
                index += 1
                if control <= 127:
                    count = control + 1
                    line += data[index : index + count]
                    index += count
                    needed -= count
                elif control >= 193:
                    count = 257 - control
                    value = data[index : index + 1]
                    index += 1
                    line += value * count
                    needed -= count
                else:
                    break
        else:
            line = bytearray(data[index : index + bytes_per_line])
            index += bytes_per_line
        for _ in range(repeat):
            rows.append(bytes(line))
            if len(rows) >= height:
                break
    if not rows:
        return None

    if bits_per_pixel == 1:
        image = Image.new("1", (width, height), 1)
        pixels = image.load()
        for y, row in enumerate(rows):
            for x in range(width):
                byte = row[x >> 3] if (x >> 3) < len(row) else 0xFF
                bit = (byte >> (7 - (x & 7))) & 1
                # PWG black_1: 1 = black, 0 = white (PIL "1": 0 = black, 1 = white).
                pixels[x, y] = 0 if bit else 1
        return image

    image = Image.new("L", (width, height), 255)
    pixels = image.load()
    step = max(1, bits_per_pixel // 8)
    for y, row in enumerate(rows):
        for x in range(width):
            offset = x * step
            if offset >= len(row):
                break
            # sgray_8: 0 = black, 255 = white - already luminance.
            pixels[x, y] = row[offset]
    return image
