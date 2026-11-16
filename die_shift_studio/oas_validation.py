"""Streaming validation for the CRC32 OAS subset written by this application."""
from pathlib import Path
import struct
import zlib


def verify_crc(path):
    path = Path(path)
    size = path.stat().st_size
    if size < 280:
        return False
    with path.open('rb') as stream:
        if stream.read(13) != b'%SEMI-OASIS\r\n':
            return False
        stream.seek(-256, 2)
        end = stream.read()
        if len(end) != 256 or end[0] != 2 or end[-5] != 1:
            return False
        stream.seek(0)
        left, crc = size - 4, 0
        while left:
            data = stream.read(min(left, 1024 * 1024))
            if not data:
                return False
            left -= len(data)
            crc = zlib.crc32(data, crc)
    return crc == struct.unpack('<I', end[-4:])[0]
