"""Minimal synchronous WebSocket framing.

Shared by the server handler (server/room_server.py) and its integration-test
client (server/test_room_server.py), which both speak WebSocket over plain
sockets. The badge's client lives in app/aiohttp_ws.py and can't share this — it
is built on asyncio streams, so its reads/writes are awaited.

The read/write primitives take a `read(n) -> bytes` / writer-agnostic byte
buffer so the same code works over a buffered file object (`rfile.read`) and a
raw socket (`sock.recv`). Server→client frames are unmasked; client→server
frames MUST be masked (RFC 6455 §5.3), hence the `mask` flag on encode_frame.
"""
import base64
import hashlib
import os
import struct

WS_MAGIC = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

OP_TEXT = 0x01
OP_CLOSE = 0x08
OP_PING = 0x09
OP_PONG = 0x0A


def accept_key(sec_websocket_key):
    """RFC 6455 Sec-WebSocket-Accept value for a client's Sec-WebSocket-Key."""
    return base64.b64encode(
        hashlib.sha1((sec_websocket_key + WS_MAGIC).encode()).digest()
    ).decode()


def recv_exact(read, n):
    """Read exactly n bytes via read(k) -> bytes (e.g. rfile.read or sock.recv)."""
    chunks = []
    remaining = n
    while remaining:
        chunk = read(remaining)
        if not chunk:
            raise EOFError("connection closed")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def read_frame(read, first_byte=None):
    """Read one frame, returning (opcode, payload).

    `first_byte` may be pre-read by the caller — the server reads the first byte
    under a timeout to detect an idle connection, then reads the rest of the
    frame blocking, so everything after that byte belongs to one frame.
    """
    if first_byte is None:
        byte1 = recv_exact(read, 1)[0]
    elif isinstance(first_byte, (bytes, bytearray)):
        byte1 = first_byte[0]
    else:
        byte1 = first_byte
    byte2 = recv_exact(read, 1)[0]
    opcode = byte1 & 0x0F
    masked = bool(byte2 & 0x80)
    length = byte2 & 0x7F
    if length == 126:
        length = struct.unpack("!H", recv_exact(read, 2))[0]
    elif length == 127:
        length = struct.unpack("!Q", recv_exact(read, 8))[0]
    mask = recv_exact(read, 4) if masked else None
    data = recv_exact(read, length) if length else b""
    if mask:
        data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
    return opcode, data


def encode_frame(data, opcode=OP_TEXT, mask=False):
    """Encode one (final, unfragmented) frame to bytes.

    Server→client frames are unmasked; client→server frames must pass mask=True.
    """
    if isinstance(data, str):
        data = data.encode()
    length = len(data)
    mask_bit = 0x80 if mask else 0
    if length < 126:
        header = struct.pack("!BB", 0x80 | opcode, mask_bit | length)
    elif length < 65536:
        header = struct.pack("!BBH", 0x80 | opcode, mask_bit | 126, length)
    else:
        header = struct.pack("!BBQ", 0x80 | opcode, mask_bit | 127, length)
    if not mask:
        return header + data
    mask_key = os.urandom(4)
    masked = bytes(b ^ mask_key[i % 4] for i, b in enumerate(data))
    return header + mask_key + masked
