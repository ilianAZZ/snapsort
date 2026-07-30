"""Writing metadata into the filed copies.

Snapchat ships the date and the location in `memories_history.json`, never in
the media themselves: once copied, a photo has neither a capture date nor GPS,
and Photos files it under the day it was imported. So we write that information
into the copy, in the destination — never into the source (see invariant 1).

Everything is written by hand, with no dependency:

    JPEG  an APP1/Exif segment inserted after the JFIF APP0
    PNG   an eXIf chunk inserted before the first IDAT
    MP4   mvhd/tkhd/mdhd dates, then ©day / ©xyz in moov/udta

Each function returns the bytes of the modified file, or None when the format
is unknown or the structure does not allow a safe write. In that case the copy
is left untouched: no metadata beats a damaged file.

Date convention: capture-time fields (Exif DateTimeOriginal, ©day) get local
time, like the file names, together with the UTC offset. Fields the spec
defines as UTC (mvhd, GPSDateStamp) get UTC.
"""

from __future__ import annotations

import struct
import zlib
from datetime import datetime, timezone

from .mp4 import box as _box, boxes as _boxes, find as _find

# Les atomes QuickTime comptent les secondes depuis le 1er janvier 1904.
MAC_EPOCH = 2_082_844_800

SOFTWARE = "Snapchat Memories Sorter"

# Types Exif
_BYTE, _ASCII, _SHORT, _LONG, _RATIONAL, _UNDEFINED = 1, 2, 3, 4, 5, 7


# ==========================================================================
# Construction du bloc Exif (TIFF little-endian)
# ==========================================================================

def _ascii(text: str) -> tuple[int, int, bytes]:
    raw = text.encode("ascii", "replace") + b"\x00"
    return _ASCII, len(raw), raw


def _rational(num: int, den: int) -> bytes:
    return struct.pack("<II", num, den)


def _dms(value: float) -> bytes:
    """Decimal degrees to three rationals: degrees / minutes / seconds."""
    value = abs(value)
    deg = int(value)
    minutes = int((value - deg) * 60)
    seconds = (value - deg - minutes / 60) * 3600
    # Four decimals of a second is about 3 mm, far beyond Snapchat precision.
    sec_num = min(int(round(seconds * 10_000)), 599_999)
    return _rational(deg, 1) + _rational(minutes, 1) + _rational(sec_num, 10_000)


def _pack_ifd(entries: list[tuple], base: int, next_ifd: int = 0) -> bytes:
    """Serialise an IFD placed at offset `base` in the TIFF stream.

    `entries` is (tag, type, count, raw value). Values longer than four bytes
    go into the data area that follows the IFD directly.
    """
    entries = sorted(entries)                       # tags must be in ascending order
    data_start = base + 2 + 12 * len(entries) + 4
    head = bytearray(struct.pack("<H", len(entries)))
    data = bytearray()
    for tag, typ, count, raw in entries:
        if len(raw) <= 4:
            field = raw.ljust(4, b"\x00")
        else:
            field = struct.pack("<I", data_start + len(data))
            data += raw
            if len(data) % 2:                       # les offsets restent pairs
                data += b"\x00"
        head += struct.pack("<HHI", tag, typ, count) + field
    head += struct.pack("<I", next_ifd)
    return bytes(head + data)


def build_exif(ts: float, lat: float | None = None, lon: float | None = None) -> bytes:
    """Bloc TIFF/Exif complet : date de prise de vue et, si connu, position."""
    local = datetime.fromtimestamp(ts, timezone.utc).astimezone()
    utc = datetime.fromtimestamp(ts, timezone.utc)
    stamp = local.strftime("%Y:%m:%d %H:%M:%S")
    offset = local.strftime("%z")
    offset = f"{offset[:3]}:{offset[3:]}" if offset else "+00:00"

    gps: list[tuple] = []
    if lat is not None and lon is not None:
        gps = [
            (0x0000, _BYTE, 4, bytes((2, 3, 0, 0))),                    # GPSVersionID
            (0x0001, *_ascii("N" if lat >= 0 else "S")),                # GPSLatitudeRef
            (0x0002, _RATIONAL, 3, _dms(lat)),                          # GPSLatitude
            (0x0003, *_ascii("E" if lon >= 0 else "W")),                # GPSLongitudeRef
            (0x0004, _RATIONAL, 3, _dms(lon)),                          # GPSLongitude
            (0x0007, _RATIONAL, 3, _rational(utc.hour, 1)               # GPSTimeStamp
             + _rational(utc.minute, 1) + _rational(utc.second, 1)),
            (0x0012, *_ascii("WGS-84")),                                # GPSMapDatum
            (0x001D, *_ascii(utc.strftime("%Y:%m:%d"))),                # GPSDateStamp
        ]

    exif_ifd = [
        (0x9000, _UNDEFINED, 4, b"0231"),                               # ExifVersion
        (0x9003, *_ascii(stamp)),                                       # DateTimeOriginal
        (0x9004, *_ascii(stamp)),                                       # DateTimeDigitized
        (0x9011, *_ascii(offset)),                                      # OffsetTimeOriginal
        (0x9012, *_ascii(offset)),                                      # OffsetTimeDigitized
    ]

    def ifd0(exif_ptr: int, gps_ptr: int) -> list[tuple]:
        out = [
            (0x0131, *_ascii(SOFTWARE)),                                # Software
            (0x0132, *_ascii(stamp)),                                   # DateTime
            (0x8769, _LONG, 1, struct.pack("<I", exif_ptr)),            # Exif IFD
        ]
        if gps:
            out.append((0x8825, _LONG, 1, struct.pack("<I", gps_ptr)))  # GPS IFD
        return out

    # IFD0's size does not depend on the pointer values: build it once to
    # measure it, then build it again with the real offsets.
    head = struct.pack("<2sHI", b"II", 42, 8)
    size0 = len(_pack_ifd(ifd0(0, 0), 8))
    exif_at = 8 + size0
    exif_block = _pack_ifd(exif_ifd, exif_at)
    gps_at = exif_at + len(exif_block)
    gps_block = _pack_ifd(gps, gps_at) if gps else b""
    return head + _pack_ifd(ifd0(exif_at, gps_at), 8) + exif_block + gps_block


# ==========================================================================
# JPEG
# ==========================================================================

def _jpeg_segments(data: bytes) -> tuple[list[tuple[int, bytes]], bytes] | None:
    """Split the JPEG header into segments, up to the compressed data."""
    if data[:2] != b"\xff\xd8":
        return None
    segs: list[tuple[int, bytes]] = []
    i = 2
    while i + 3 < len(data):
        if data[i] != 0xFF:
            return None                             # stream out of sync: leave it alone
        marker = data[i + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            segs.append((marker, data[i:i + 2]))
            i += 2
            continue
        if marker in (0xDA, 0xD9):                  # start of scan / end of image
            break
        length = struct.unpack_from(">H", data, i + 2)[0]
        end = i + 2 + length
        if length < 2 or end > len(data):
            return None
        segs.append((marker, data[i:end]))
        i = end
    return segs, data[i:]


def embed_jpeg(data: bytes, exif: bytes) -> bytes | None:
    parsed = _jpeg_segments(data)
    if not parsed:
        return None
    segs, tail = parsed
    payload = b"Exif\x00\x00" + exif
    if len(payload) + 2 > 0xFFFF:
        return None
    segment = b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload

    kept = [s for s in segs
            if not (s[0] == 0xE1 and s[1][4:10] == b"Exif\x00\x00")]
    # The Exif APP1 goes after the JFIF APP0 when there is one.
    at = 0
    while at < len(kept) and kept[at][0] == 0xE0:
        at += 1
    kept.insert(at, (0xE1, segment))
    return b"\xff\xd8" + b"".join(raw for _, raw in kept) + tail


# ==========================================================================
# PNG (les calques Snapchat)
# ==========================================================================

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def embed_png(data: bytes, exif: bytes) -> bytes | None:
    if data[:8] != PNG_MAGIC:
        return None
    out = bytearray(PNG_MAGIC)
    chunk = b"eXIf" + exif
    new = struct.pack(">I", len(exif)) + chunk + struct.pack(">I", zlib.crc32(chunk))

    i, done = 8, False
    while i + 8 <= len(data):
        length = struct.unpack_from(">I", data, i)[0]
        kind = data[i + 4:i + 8]
        end = i + 12 + length
        if end > len(data):
            return None
        if kind == b"eXIf":                         # on remplace l'existant
            i = end
            continue
        if kind == b"IDAT" and not done:
            out += new
            done = True
        out += data[i:end]
        i = end
        if kind == b"IEND":
            break
    return bytes(out) if done else None


# ==========================================================================
# MP4 / MOV
# ==========================================================================

def _patch_times(buf: bytearray, off: int, size: int, header: int, when: int):
    """Rewrite creation_time and modification_time of an mvhd / tkhd / mdhd.

    All three atoms start the same way: version+flags, then the two dates.
    Written in place, so the box size never changes.
    """
    at = off + header
    if at + 4 > off + size:
        return
    version = buf[at]
    at += 4
    if version == 1:
        if at + 16 <= off + size:
            struct.pack_into(">QQ", buf, at, when, when)
    elif at + 8 <= off + size:
        struct.pack_into(">II", buf, at, when & 0xFFFFFFFF, when & 0xFFFFFFFF)


def _udta_atom(kind: bytes, text: str) -> bytes:
    """QuickTime text atom: size, type, length, language code, text."""
    raw = text.encode("utf-8")
    return struct.pack(">I4sHH", 12 + len(raw), kind, len(raw), 0x55C4) + raw


def _quicktime_tags(local: datetime, lat: float | None, lon: float | None) -> list[tuple]:
    """The keys Photos actually reads from a video (the `mdta` namespace)."""
    tags = [
        (b"com.apple.quicktime.creationdate", local.strftime("%Y-%m-%dT%H:%M:%S%z")),
        (b"com.apple.quicktime.software", SOFTWARE),
    ]
    if lat is not None and lon is not None:
        tags.append((b"com.apple.quicktime.location.ISO6709",
                     f"{lat:+08.4f}{lon:+09.4f}/"))
    return tags


def _rebuild_udta(old: bytes | None, local: datetime,
                  lat: float | None, lon: float | None) -> bytes:
    """Copy the original `udta` minus any ©day/©xyz, then append ours."""
    kept = b""
    if old:
        for kind, off, size, _header in _boxes(old, 8, len(old)):
            if kind not in (b"\xa9day", b"\xa9xyz"):
                kept += old[off:off + size]
    extra = _udta_atom(b"\xa9day", local.strftime("%Y-%m-%dT%H:%M:%S%z"))
    if lat is not None and lon is not None:
        extra += _udta_atom(b"\xa9xyz", f"{lat:+08.4f}{lon:+09.4f}/")
    return _box(b"udta", kept + extra)


def _split_meta(old: bytes) -> tuple[int, bytes, list[bytes], list[bytes]] | None:
    """Open an existing `meta`: (header size, hdlr, keys entries, ilst entries).

    QuickTime's `meta` has no version/flags word, ISO-BMFF's does: we tell the
    two apart by trying to read a box at either position.
    """
    for header in (8, 12):
        first = next(_boxes(old, header, len(old)), None)
        if first and first[0] == b"hdlr":
            break
    else:
        return None
    hdlr = keys = ilst = None
    for kind, off, size, box_header in _boxes(old, header, len(old)):
        if kind == b"hdlr":
            hdlr = old[off:off + size]
            if old[off + box_header + 8:off + box_header + 12] != b"mdta":
                return None                         # another namespace: leave it alone
        elif kind == b"keys":
            keys = (off, size, box_header)
        elif kind == b"ilst":
            ilst = (off, size, box_header)
    if not hdlr:
        return None

    key_entries: list[bytes] = []
    if keys:
        off, size, box_header = keys
        at = off + box_header + 8                   # version/flags, then entry_count
        while at + 8 <= off + size:
            length = struct.unpack_from(">I", old, at)[0]
            if length < 8 or at + length > off + size:
                break
            key_entries.append(old[at:at + length])
            at += length
    ilst_entries: list[bytes] = []
    if ilst:
        off, size, box_header = ilst
        for _kind, i_off, i_size, _h in _boxes(old, off + box_header, off + size):
            ilst_entries.append(old[i_off:i_off + i_size])
    return header, hdlr, key_entries, ilst_entries


def _rebuild_meta(old: bytes | None, local: datetime,
                  lat: float | None, lon: float | None) -> bytes:
    """Build (or extend) moov's `meta` with the Apple keys.

    Existing entries are kept verbatim and keep their rank: ours are appended
    after them, in `keys` as well as in `ilst`.
    """
    # With no existing box we write the QuickTime form (no version/flags):
    # that is the one Apple readers recognise.
    header, hdlr, key_entries, ilst_entries = 8, None, [], []
    if old:
        parsed = _split_meta(old)
        if not parsed:
            return old                              # unexpected structure: leave it as is
        header, hdlr, key_entries, ilst_entries = parsed
    if hdlr is None:
        hdlr = _box(b"hdlr", bytes(8) + b"mdta" + bytes(12) + b"\x00")

    for name, value in _quicktime_tags(local, lat, lon):
        key_entries.append(struct.pack(">I4s", 8 + len(name), b"mdta") + name)
        raw = value.encode("utf-8")
        data = _box(b"data", struct.pack(">II", 1, 0) + raw)    # type 1 = UTF-8 text
        ilst_entries.append(struct.pack(">I", 8 + len(data))
                            + struct.pack(">I", len(key_entries)) + data)

    keys = _box(b"keys", struct.pack(">II", 0, len(key_entries)) + b"".join(key_entries))
    ilst = _box(b"ilst", b"".join(ilst_entries))
    prefix = bytes(4) if header == 12 else b""      # the ISO form's version/flags
    return _box(b"meta", prefix + hdlr + keys + ilst)


def _shift_chunk_offsets(buf: bytearray, start: int, end: int, at: int, delta: int):
    """Shift the chunk offsets (stco / co64) that sit past the insertion point."""
    for kind, off, size, header in _boxes(bytes(buf), start, end):
        if kind in (b"trak", b"mdia", b"minf", b"stbl"):
            _shift_chunk_offsets(buf, off + header, off + size, at, delta)
        elif kind in (b"stco", b"co64"):
            count = struct.unpack_from(">I", buf, off + header + 4)[0]
            wide = kind == b"co64"
            step = 8 if wide else 4
            base = off + header + 8
            if base + count * step > off + size:
                continue
            for i in range(count):
                pos = base + i * step
                value = struct.unpack_from(">Q" if wide else ">I", buf, pos)[0]
                if value >= at:
                    struct.pack_into(">Q" if wide else ">I", buf, pos, value + delta)


def _new_moov(buf: bytearray, m_off: int, m_size: int, local: datetime,
              lat: float | None, lon: float | None) -> bytes:
    """Rebuild moov with an up-to-date `udta` and `meta`, other boxes untouched."""
    children: list[bytes] = []
    seen = set()
    for kind, off, size, _header in _boxes(buf, m_off + 8, m_off + m_size):
        raw = bytes(buf[off:off + size])
        if kind == b"udta":
            raw = _rebuild_udta(raw, local, lat, lon)
        elif kind == b"meta":
            raw = _rebuild_meta(raw, local, lat, lon)
        seen.add(kind)
        children.append(raw)
    if b"udta" not in seen:
        children.append(_rebuild_udta(None, local, lat, lon))
    if b"meta" not in seen:
        children.append(_rebuild_meta(None, local, lat, lon))
    return _box(b"moov", b"".join(children))


def embed_mp4(data: bytes, ts: float, lat: float | None, lon: float | None) -> bytes | None:
    if b"moof" in data[:4096]:
        return None                                 # fragmented mp4: out of scope
    top = list(_boxes(data, 0, len(data)))
    moov = next(((o, s, h) for k, o, s, h in top if k == b"moov"), None)
    if not moov:
        return None
    m_off, m_size, m_header = moov
    if m_header != 8:
        return None                                 # 64-bit size: too rare to be worth the risk

    buf = bytearray(data)
    when = max(0, int(ts) + MAC_EPOCH)
    mvhd = _find(buf, m_off + m_header, m_off + m_size, b"mvhd")
    if mvhd:
        _patch_times(buf, *mvhd, when)
    for kind, off, size, header in _boxes(bytes(buf), m_off + m_header, m_off + m_size):
        if kind != b"trak":
            continue
        tkhd = _find(buf, off + header, off + size, b"tkhd")
        if tkhd:
            _patch_times(buf, *tkhd, when)
        mdia = _find(buf, off + header, off + size, b"mdia")
        if mdia:
            d_off, d_size, d_header = mdia
            mdhd = _find(buf, d_off + d_header, d_off + d_size, b"mdhd")
            if mdhd:
                _patch_times(buf, *mdhd, when)

    local = datetime.fromtimestamp(ts, timezone.utc).astimezone()
    delta = len(_new_moov(buf, m_off, m_size, local, lat, lon)) - m_size

    # A `free` box right after moov acts as a shock absorber: shrink it by the
    # same amount and nothing moves, so the chunk offsets stay valid.
    after = next(((k, o, s, h) for k, o, s, h in top if o == m_off + m_size), None)
    pad = None
    if after and after[0] in (b"free", b"skip") and after[2] - 8 - delta >= 0:
        old_pad = bytes(buf[after[1] + 8:after[1] + after[2]])
        pad = old_pad[delta:] if delta > 0 else old_pad + bytes(-delta)
    elif delta:
        # Otherwise everything after moov slides: stco/co64 must follow.
        _shift_chunk_offsets(buf, m_off + m_header, m_off + m_size,
                             m_off + m_size, delta)

    moov_bytes = _new_moov(buf, m_off, m_size, local, lat, lon)
    if pad is None:
        out = bytes(buf[:m_off]) + moov_bytes + bytes(buf[m_off + m_size:])
    else:
        tail = after[1] + after[2]
        out = (bytes(buf[:m_off]) + moov_bytes
               + struct.pack(">I4s", 8 + len(pad), after[0]) + pad
               + bytes(buf[tail:]))
    return out if _looks_sane(out) else None


def _looks_sane(data: bytes) -> bool:
    """Re-read what we produced: one wrong size and the video is unplayable."""
    total = 0
    seen = set()
    for kind, _off, size, _header in _boxes(data, 0, len(data)):
        total += size
        seen.add(kind)
    return total == len(data) and b"moov" in seen and b"mdat" in seen


# ==========================================================================
# Entry point
# ==========================================================================

def embed(path: str, ts: float, lat: float | None = None,
          lon: float | None = None, ext: str | None = None) -> bool:
    """Write date and location into `path`. True when something was written.

    `ext` forces the format: the copy lands in a `.part` file whose extension
    says nothing about its contents.

    The file is only rewritten when the transformation succeeded: when the
    structure looks doubtful we would rather leave it intact.
    """
    ext = (ext or path.rsplit(".", 1)[-1]).lower().lstrip(".")
    try:
        with open(path, "rb") as fh:
            data = fh.read()
        if ext in ("jpg", "jpeg"):
            out = embed_jpeg(data, build_exif(ts, lat, lon))
        elif ext == "png":
            out = embed_png(data, build_exif(ts, lat, lon))
        elif ext in ("mp4", "mov", "m4v"):
            out = embed_mp4(data, ts, lat, lon)
        else:
            return False
        if not out or out == data:
            return False
        with open(path, "wb") as fh:
            fh.write(out)
        return True
    except (OSError, ValueError, struct.error):
        return False
