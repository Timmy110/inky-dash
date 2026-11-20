"""Polyline decoders: Google encoded polyline (precision 5/6) and HERE flexible polyline."""

from __future__ import annotations

LatLon = tuple[float, float]


def decode_google(s: str, precision: int = 5) -> list[LatLon]:
    coords: list[LatLon] = []
    index = lat = lng = 0
    factor = 10 ** precision
    n = len(s)
    while index < n:
        vals = []
        for _ in range(2):
            shift = result = 0
            while True:
                b = ord(s[index]) - 63
                index += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            vals.append(~(result >> 1) if result & 1 else result >> 1)
        lat += vals[0]
        lng += vals[1]
        coords.append((lat / factor, lng / factor))
    return coords


def encode_google(coords: list[LatLon], precision: int = 5) -> str:
    factor = 10 ** precision
    out = []
    plat = plng = 0
    for lat, lng in coords:
        ilat, ilng = round(lat * factor), round(lng * factor)
        for v in (ilat - plat, ilng - plng):
            v = ~(v << 1) if v < 0 else v << 1
            while v >= 0x20:
                out.append(chr((0x20 | (v & 0x1F)) + 63))
                v >>= 5
            out.append(chr(v + 63))
        plat, plng = ilat, ilng
    return "".join(out)


_FLEX_TABLE = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
_FLEX_DECODE = {c: i for i, c in enumerate(_FLEX_TABLE)}


def _flex_unsigned(s: str):
    result = shift = 0
    for ch in s:
        v = _FLEX_DECODE[ch]
        result |= (v & 0x1F) << shift
        if v & 0x20:
            shift += 5
        else:
            yield result
            result = shift = 0
    if shift:
        raise ValueError("flexible polyline: truncated value")


def decode_flexible(s: str) -> list[LatLon]:
    """HERE flexible polyline → [(lat, lng)] (a third dimension, if any, is dropped)."""
    values = _flex_unsigned(s)
    version = next(values)
    if version != 1:
        raise ValueError(f"flexible polyline: unsupported version {version}")
    header = next(values)
    precision = header & 15
    third_dim = (header >> 4) & 7
    factor = 10 ** precision
    lat = lng = z = 0
    out: list[LatLon] = []

    def signed(v: int) -> int:
        return ~(v >> 1) if v & 1 else v >> 1

    for v in values:
        lat += signed(v)
        lng += signed(next(values))
        if third_dim:
            z += signed(next(values))
        out.append((lat / factor, lng / factor))
    return out


def encode_flexible(coords: list[LatLon], precision: int = 5) -> str:
    """Only for tests/fixtures."""
    def enc_unsigned(v: int) -> str:
        s = ""
        while v > 0x1F:
            s += _FLEX_TABLE[(v & 0x1F) | 0x20]
            v >>= 5
        return s + _FLEX_TABLE[v]

    def enc_signed(v: int) -> str:
        return enc_unsigned(~(v << 1) if v < 0 else v << 1)

    factor = 10 ** precision
    out = enc_unsigned(1) + enc_unsigned(precision)
    plat = plng = 0
    for lat, lng in coords:
        ilat, ilng = round(lat * factor), round(lng * factor)
        out += enc_signed(ilat - plat) + enc_signed(ilng - plng)
        plat, plng = ilat, ilng
    return out
