import os
import time
import uuid

def uuid7() -> uuid.UUID:
    ms = time.time_ns() // 1_000_000
    b = bytearray(ms.to_bytes(6, "big") + os.urandom(10))
    b[6] = (b[6] & 0x0F) | 0x70   # versiunea 7
    b[8] = (b[8] & 0x3F) | 0x80   # varianta RFC
    return uuid.UUID(bytes=bytes(b))
