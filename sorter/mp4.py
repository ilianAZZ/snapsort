"""ISO base media (MP4) primitives, and lossless concatenation of segments.

Snapchat caps a single recording at ten seconds: anything longer comes back as
several consecutive memories. `concat()` glues those segments into one file by
copying their samples verbatim and rebuilding the sample tables. The audio and
video bitstreams are never decoded, so there is no quality loss and no encoder —
which keeps the zero-dependency promise.

Only the case that actually occurs here is supported: non-fragmented files whose
tracks share the same codec configuration (`stsd`), timescale and layout.
Anything else returns None and the caller keeps the segments apart. A merged
file is worthless if it plays badly; refusing is always the better answer.
"""

from __future__ import annotations

import struct

# Containers whose payload is a list of boxes, with no fields of their own.
CONTAINERS = {b"moov", b"trak", b"mdia", b"minf", b"stbl", b"edts", b"udta"}


# ==========================================================================
# Boxes
# ==========================================================================

def boxes(data, start: int, end: int):
    """Iterate one level of boxes: (type, offset, size, header size)."""
    off = start
    while off + 8 <= end:
        size, kind = struct.unpack_from(">I4s", data, off)
        header = 8
        if size == 1:
            if off + 16 > end:
                return
            size = struct.unpack_from(">Q", data, off + 8)[0]
            header = 16
        elif size == 0:
            size = end - off
        if size < header or off + size > end:
            return
        yield kind, off, size, header
        off += size


def find(data, start: int, end: int, kind: bytes):
    """First box of type `kind` at this level: (offset, size, header size)."""
    for found, off, size, header in boxes(data, start, end):
        if found == kind:
            return off, size, header
    return None


def box(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I4s", 8 + len(payload), kind) + payload


def full_box(kind: bytes, payload: bytes, version: int = 0, flags: int = 0) -> bytes:
    return box(kind, struct.pack(">I", (version << 24) | flags) + payload)


def replace_child(container: bytes, kind: bytes, replacement: bytes) -> bytes:
    """Rebuild a container box with one of its children swapped out."""
    outer_kind, _off, _size, header = next(boxes(container, 0, len(container)))
    children = [replacement if child == kind else container[off:off + size]
                for child, off, size, _h in boxes(container, header, len(container))]
    return box(outer_kind, b"".join(children))


# ==========================================================================
# Reading a file's tracks
# ==========================================================================

def _table(data, stbl: tuple, kind: bytes):
    """Locate a table inside stbl and return (offset of its payload, version)."""
    off, size, header = stbl
    found = find(data, off + header, off + size, kind)
    if not found:
        return None
    f_off, f_size, f_header = found
    return f_off + f_header, f_off + f_size, data[f_off + f_header]


def _sizes(data, stbl) -> list[int] | None:
    table = _table(data, stbl, b"stsz")
    if not table:
        return None
    at, end, _version = table
    fixed, count = struct.unpack_from(">II", data, at + 4)
    if fixed:
        return [fixed] * count
    if at + 12 + 4 * count > end:
        return None
    return list(struct.unpack_from(f">{count}I", data, at + 12))


def _chunk_offsets(data, stbl) -> list[int] | None:
    table = _table(data, stbl, b"stco")
    wide = False
    if not table:
        table = _table(data, stbl, b"co64")
        wide = True
    if not table:
        return None
    at, end, _version = table
    count = struct.unpack_from(">I", data, at + 4)[0]
    step = 8 if wide else 4
    if at + 8 + count * step > end:
        return None
    fmt = f">{count}{'Q' if wide else 'I'}"
    return list(struct.unpack_from(fmt, data, at + 8))


def _samples_per_chunk(data, stbl, chunk_count: int) -> list[int] | None:
    table = _table(data, stbl, b"stsc")
    if not table:
        return None
    at, end, _version = table
    count = struct.unpack_from(">I", data, at + 4)[0]
    if at + 8 + count * 12 > end:
        return None
    entries = [struct.unpack_from(">III", data, at + 8 + 12 * i) for i in range(count)]
    out = []
    for i, (first, per_chunk, desc) in enumerate(entries):
        if desc != 1:
            return None                     # several descriptions: out of scope
        last = entries[i + 1][0] - 1 if i + 1 < len(entries) else chunk_count
        out += [per_chunk] * max(0, last - first + 1)
    return out if len(out) == chunk_count else None


def _runs(data, stbl, kind: bytes) -> list[int] | None:
    """Expand a run-length table (stts, ctts) into one value per sample."""
    table = _table(data, stbl, kind)
    if not table:
        return None
    at, end, version = table
    count = struct.unpack_from(">I", data, at + 4)[0]
    if at + 8 + count * 8 > end:
        return None
    signed = kind == b"ctts" and version == 1
    out: list[int] = []
    for i in range(count):
        n, value = struct.unpack_from(">Ii" if signed else ">II", data, at + 8 + 8 * i)
        out += [value] * n
    return out


def _sync(data, stbl) -> list[int] | None:
    table = _table(data, stbl, b"stss")
    if not table:
        return None
    at, end, _version = table
    count = struct.unpack_from(">I", data, at + 4)[0]
    if at + 8 + count * 4 > end:
        return None
    return list(struct.unpack_from(f">{count}I", data, at + 8))


def _read_track(data, trak: tuple) -> dict | None:
    off, size, header = trak
    mdia = find(data, off + header, off + size, b"mdia")
    if not mdia:
        return None
    m_off, m_size, m_header = mdia
    mdhd = find(data, m_off + m_header, m_off + m_size, b"mdhd")
    minf = find(data, m_off + m_header, m_off + m_size, b"minf")
    if not mdhd or not minf:
        return None
    version = data[mdhd[0] + mdhd[2]]
    base = mdhd[0] + mdhd[2] + 4
    timescale = struct.unpack_from(">I", data, base + (16 if version == 1 else 8))[0]

    stbl = find(data, minf[0] + minf[2], minf[0] + minf[1], b"stbl")
    if not stbl:
        return None
    stsd = find(data, stbl[0] + stbl[2], stbl[0] + stbl[1], b"stsd")
    if not stsd or struct.unpack_from(">I", data, stsd[0] + stsd[2] + 4)[0] != 1:
        return None                         # only one description supported

    sizes = _sizes(data, stbl)
    offsets = _chunk_offsets(data, stbl)
    deltas = _runs(data, stbl, b"stts")
    if sizes is None or offsets is None or deltas is None:
        return None
    per_chunk = _samples_per_chunk(data, stbl, len(offsets))
    if per_chunk is None or sum(per_chunk) != len(sizes) or len(deltas) != len(sizes):
        return None

    samples: list[tuple[int, int]] = []
    index = 0
    for chunk, count in zip(offsets, per_chunk):
        position = chunk
        for _ in range(count):
            samples.append((position, sizes[index]))
            position += sizes[index]
            index += 1

    return {
        "trak": bytes(data[off:off + size]),
        "timescale": timescale,
        "stsd": bytes(data[stsd[0]:stsd[0] + stsd[1]]),
        "samples": samples,
        "deltas": deltas,
        "ctts": _runs(data, stbl, b"ctts"),
        "sync": _sync(data, stbl),
    }


def duration(data: bytes) -> float | None:
    """Length in seconds, read from `mvhd`. None when `moov` is not in `data`.

    Meant to be fed the first few hundred kilobytes of a file rather than the
    whole thing: Snapchat writes `moov` before `mdat`, so the header is enough.
    A file laid out the other way round simply reports no duration, and the
    caller falls back to treating the clip as standalone.
    """
    off = 0
    try:
        while off + 8 <= len(data):
            size, kind = struct.unpack_from(">I4s", data, off)
            header = 8
            if size == 1:
                size = struct.unpack_from(">Q", data, off + 8)[0]
                header = 16
            elif size == 0:
                size = len(data) - off
            if size < header:
                return None
            if kind == b"moov":
                mvhd = find(data, off + header, min(off + size, len(data)), b"mvhd")
                if not mvhd:
                    return None
                base = mvhd[0] + mvhd[2] + 4        # past version and flags
                if data[mvhd[0] + mvhd[2]] == 1:
                    timescale, ticks = struct.unpack_from(">IQ", data, base + 16)
                else:
                    timescale, ticks = struct.unpack_from(">II", data, base + 8)
                return ticks / timescale if timescale else None
            off += size
    except struct.error:
        return None
    return None


def read(data: bytes) -> dict | None:
    """Parse a file into {movie_timescale, ftyp, tracks[]}, or None if unusable."""
    if b"moof" in data[:4096]:
        return None                         # fragmented: out of scope
    top = list(boxes(data, 0, len(data)))
    moov = next(((o, s, h) for k, o, s, h in top if k == b"moov"), None)
    ftyp = next(((o, s) for k, o, s, _h in top if k == b"ftyp"), None)
    if not moov or not ftyp:
        return None
    m_off, m_size, m_header = moov
    mvhd = find(data, m_off + m_header, m_off + m_size, b"mvhd")
    if not mvhd:
        return None
    version = data[mvhd[0] + mvhd[2]]
    base = mvhd[0] + mvhd[2] + 4
    movie_timescale = struct.unpack_from(">I", data, base + (16 if version == 1 else 8))[0]

    tracks = []
    for kind, off, size, header in boxes(data, m_off + m_header, m_off + m_size):
        if kind != b"trak":
            continue
        track = _read_track(data, (off, size, header))
        if not track:
            return None
        tracks.append(track)
    if not tracks:
        return None
    return {
        "movie_timescale": movie_timescale,
        "ftyp": bytes(data[ftyp[0]:ftyp[0] + ftyp[1]]),
        "tracks": tracks,
    }


# ==========================================================================
# Writing the merged file
# ==========================================================================

def _compact(values: list[int]) -> list[tuple[int, int]]:
    runs: list[list[int]] = []
    for value in values:
        if runs and runs[-1][1] == value:
            runs[-1][0] += 1
        else:
            runs.append([1, value])
    return [(n, v) for n, v in runs]


def _stbl(track: dict, data_offset: int) -> bytes:
    """Sample table for a merged track: one chunk holding every sample."""
    deltas = _compact(track["deltas"])
    parts = [track["stsd"]]
    parts.append(full_box(b"stts", struct.pack(">I", len(deltas))
                          + b"".join(struct.pack(">II", n, v) for n, v in deltas)))
    if track["ctts"] is not None:
        signed = any(v < 0 for v in track["ctts"])
        runs = _compact(track["ctts"])
        parts.append(full_box(b"ctts", struct.pack(">I", len(runs))
                              + b"".join(struct.pack(">Ii" if signed else ">II", n, v)
                                         for n, v in runs),
                              version=1 if signed else 0))
    if track["sync"] is not None:
        parts.append(full_box(b"stss", struct.pack(">I", len(track["sync"]))
                              + b"".join(struct.pack(">I", i) for i in track["sync"])))
    count = len(track["samples"])
    parts.append(full_box(b"stsc", struct.pack(">IIII", 1, 1, count, 1)))
    parts.append(full_box(b"stsz", struct.pack(">II", 0, count)
                          + b"".join(struct.pack(">I", s) for _o, s in track["samples"])))
    if data_offset > 0xFFFFFFFF:
        parts.append(full_box(b"co64", struct.pack(">IQ", 1, data_offset)))
    else:
        parts.append(full_box(b"stco", struct.pack(">II", 1, data_offset)))
    return box(b"stbl", b"".join(parts))


# Where the duration field sits inside tkhd / mdhd, counted from the end of the
# version+flags word: tkhd has creation, modification, track_ID and a reserved
# word before it; mdhd has creation, modification and the timescale.
_DURATION_AT = {
    (b"tkhd", 0): (16, ">I"), (b"tkhd", 1): (24, ">Q"),
    (b"mdhd", 0): (12, ">I"), (b"mdhd", 1): (20, ">Q"),
}


def _patch_duration(raw: bytes, kind: bytes, duration: int) -> bytes:
    """Rewrite the duration of a tkhd or mdhd in place (same byte length)."""
    _outer_kind, _off, _size, header = next(boxes(raw, 0, len(raw)))
    found = find(raw, header, len(raw), kind)
    if not found:
        return raw
    off, size, box_header = found
    buf = bytearray(raw)
    shift, fmt = _DURATION_AT[(kind, 1 if buf[off + box_header] == 1 else 0)]
    at = off + box_header + 4 + shift
    if at + struct.calcsize(fmt) <= off + size:
        struct.pack_into(fmt, buf, at, duration)
    return bytes(buf)


def _patch_edit_list(raw: bytes, track_duration: int) -> bytes:
    """Stretch a track's edit list to the merged length.

    An `elst` says which slice of the media to play. Copied unchanged from the
    first segment it caps playback at that segment's duration: the file holds
    every clip, players show only the first one. The leading entries are left
    alone — an empty edit is an audio delay, and moving it would break sync —
    and the last one takes up the slack.
    """
    edts = find(raw, next(boxes(raw, 0, len(raw)))[3], len(raw), b"edts")
    if not edts:
        return raw
    elst = find(raw, edts[0] + edts[2], edts[0] + edts[1], b"elst")
    if not elst:
        return raw
    off, size, header = elst
    at = off + header + 4                               # past version and flags
    count = struct.unpack_from(">I", raw, at)[0]
    wide = raw[off + header] == 1
    step = 20 if wide else 12
    if not count or at + 4 + count * step > off + size:
        return raw

    buf = bytearray(raw)
    fmt = ">Q" if wide else ">I"
    before = sum(struct.unpack_from(fmt, buf, at + 4 + step * i)[0] for i in range(count - 1))
    struct.pack_into(fmt, buf, at + 4 + step * (count - 1), max(0, track_duration - before))
    return bytes(buf)


def _build_trak(track: dict, data_offset: int, movie_timescale: int) -> bytes:
    raw = track["trak"]
    outer = next(boxes(raw, 0, len(raw)))
    mdia = find(raw, outer[3], len(raw), b"mdia")
    m_off, m_size, m_header = mdia
    mdia_raw = raw[m_off:m_off + m_size]
    minf = find(mdia_raw, m_header, len(mdia_raw), b"minf")
    minf_raw = mdia_raw[minf[0]:minf[0] + minf[1]]

    media_duration = sum(track["deltas"])
    new_minf = replace_child(minf_raw, b"stbl", _stbl(track, data_offset))
    new_mdia = replace_child(mdia_raw, b"minf", new_minf)
    new_mdia = _patch_duration(new_mdia, b"mdhd", media_duration)
    new_trak = replace_child(raw, b"mdia", new_mdia)
    track_duration = int(media_duration * movie_timescale / track["timescale"])
    new_trak = _patch_edit_list(new_trak, track_duration)
    return _patch_duration(new_trak, b"tkhd", track_duration)


def compatible(a: dict, b: dict) -> bool:
    """True when two parsed files can be glued without touching the bitstreams."""
    if len(a["tracks"]) != len(b["tracks"]):
        return False
    if a["movie_timescale"] != b["movie_timescale"]:
        return False
    return all(x["stsd"] == y["stsd"] and x["timescale"] == y["timescale"]
               for x, y in zip(a["tracks"], b["tracks"]))


def concat(segments: list[bytes]) -> bytes | None:
    """Glue MP4 segments into one file. None if they cannot be merged safely."""
    if not segments:
        return None
    if len(segments) == 1:
        return segments[0]

    parsed = []
    for raw in segments:
        info = read(raw)
        if not info or (parsed and not compatible(parsed[0][0], info)):
            return None
        parsed.append((info, raw))

    merged = []
    for index in range(len(parsed[0][0]["tracks"])):
        payload: list[bytes] = []
        deltas: list[int] = []
        ctts: list[int] = []
        sync: list[int] = []
        sizes: list[tuple[int, int]] = []
        has_ctts = parsed[0][0]["tracks"][index]["ctts"] is not None
        has_sync = parsed[0][0]["tracks"][index]["sync"] is not None
        for info, raw in parsed:
            track = info["tracks"][index]
            base = len(sizes)
            for offset, size in track["samples"]:
                payload.append(raw[offset:offset + size])
                sizes.append((0, size))
            deltas += track["deltas"]
            if has_ctts:
                ctts += track["ctts"] or [0] * len(track["samples"])
            if has_sync:
                sync += [base + n for n in (track["sync"] or [])]
        source = parsed[0][0]["tracks"][index]
        merged.append({
            "trak": source["trak"], "timescale": source["timescale"],
            "stsd": source["stsd"], "samples": sizes, "deltas": deltas,
            "ctts": ctts if has_ctts else None,
            "sync": sync if has_sync else None,
            "payload": b"".join(payload),
        })

    ftyp = parsed[0][0]["ftyp"]
    body = b"".join(t["payload"] for t in merged)
    wide = len(body) + 16 > 0xFFFFFFFF
    mdat_header = (struct.pack(">I4sQ", 1, b"mdat", len(body) + 16) if wide
                   else struct.pack(">I4s", len(body) + 8, b"mdat"))
    cursor = len(ftyp) + len(mdat_header)

    movie_timescale = parsed[0][0]["movie_timescale"]
    traks = []
    duration = 0
    for track in merged:
        traks.append(_build_trak(track, cursor, movie_timescale))
        cursor += len(track["payload"])
        duration = max(duration, int(sum(track["deltas"]) * movie_timescale
                                     / track["timescale"]))

    first = parsed[0][1]
    top = list(boxes(first, 0, len(first)))
    m_off, m_size, m_header = next((o, s, h) for k, o, s, h in top if k == b"moov")
    mvhd = find(first, m_off + m_header, m_off + m_size, b"mvhd")
    head = bytearray(first[mvhd[0]:mvhd[0] + mvhd[1]])
    version = head[8]
    if version == 1:
        struct.pack_into(">Q", head, 12 + 16 + 4, duration)
    else:
        struct.pack_into(">I", head, 12 + 8 + 4, duration)

    moov = box(b"moov", bytes(head) + b"".join(traks))
    out = ftyp + mdat_header + body + moov
    return out if _sane(out) else None


def _sane(data: bytes) -> bool:
    """Re-read the structure we just wrote: a wrong size means an unplayable file."""
    total = 0
    seen = set()
    for kind, _off, size, _header in boxes(data, 0, len(data)):
        total += size
        seen.add(kind)
    return total == len(data) and {b"ftyp", b"mdat", b"moov"} <= seen
