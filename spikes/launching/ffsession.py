#!/usr/bin/env python3
"""Print windows and their tabs from a Firefox profile's live session file.

Firefox writes sessionstore-backups/recovery.jsonlz4 (mozLz4: 8-byte magic, u32 LE decompressed size, LZ4 block)
every browser.sessionstore.interval ms. This is the only reliable window->tab ground truth we found; the AT-SPI
tree omits tabs for some Firefox windows.

Usage: ffsession.py <profile-dir>
Output: one line per window, tabs shown as the last path component of each tab's current URL.
"""
import json
import pathlib
import struct
import sys


def lz4_block_decompress(src: bytes, size: int) -> bytes:
    dst = bytearray()
    i = 0
    while i < len(src):
        token = src[i]
        i += 1
        literal_len = token >> 4
        if literal_len == 15:
            while True:
                b = src[i]
                i += 1
                literal_len += b
                if b != 255:
                    break
        dst += src[i:i + literal_len]
        i += literal_len
        if i >= len(src):
            break
        offset = src[i] | (src[i + 1] << 8)
        i += 2
        match_len = token & 15
        if match_len == 15:
            while True:
                b = src[i]
                i += 1
                match_len += b
                if b != 255:
                    break
        match_len += 4
        for _ in range(match_len):
            dst.append(dst[-offset])
    return bytes(dst[:size])


def main() -> None:
    path = pathlib.Path(sys.argv[1]) / 'sessionstore-backups' / 'recovery.jsonlz4'
    raw = path.read_bytes()
    if raw[:8] != b'mozLz40\0':
        sys.exit(f'{path}: not a mozLz4 file')
    (size,) = struct.unpack('<I', raw[8:12])
    session = json.loads(lz4_block_decompress(raw[12:], size))
    for w, window in enumerate(session.get('windows', [])):
        tabs = []
        for tab in window['tabs']:
            entries = tab.get('entries') or []
            url = entries[tab['index'] - 1]['url'] if entries else '?'
            tabs.append(url.rsplit('/', 1)[-1].removesuffix('.html'))
        print(f'  window[{w}] tabs={tabs}')


if __name__ == '__main__':
    main()
