"""Small ICC v4 display profiles, and helpers that embed them in PNG, JPEG and TIFF files.

Blender 5.0 and newer tag the images they save. Blender 4.x writes untagged files, so
Photoshop, browsers and phones assume sRGB and wide-gamut renders look dull. This module
fills that gap. It has no Blender dependency so it can be tested on its own.
"""

import hashlib
import os
import struct
import zlib

# xy chromaticities of the red, green and blue primaries.
_REC709 = ((0.640, 0.330), (0.300, 0.600), (0.150, 0.060))
_P3 = ((0.680, 0.320), (0.265, 0.690), (0.150, 0.060))
_REC2020 = ((0.708, 0.292), (0.170, 0.797), (0.131, 0.046))
_D65 = (0.3127, 0.3290)

# The ICC connection space white (D50), exactly as the spec encodes it.
_PCS_WHITE = (0xF6D6 / 65536, 1.0, 0xD32D / 65536)
_PCS_WHITE_BYTES = struct.pack(">3i", 0xF6D6, 0x10000, 0xD32D)

# (parametric curve type, parameters) as defined for the ICC 'para' tag.
_SRGB_CURVE = (3, (2.4, 1 / 1.055, 0.055 / 1.055, 1 / 12.92, 0.04045))
_GAMMA_24 = (0, (2.4,))

# Blender display name -> (profile description, primaries, transfer curve).
# These match the displays in Blender's own OCIO config.
DISPLAYS = {
    "sRGB": ("sRGB IEC61966-2.1", _REC709, _SRGB_CURVE),
    "Display P3": ("Display P3", _P3, _SRGB_CURVE),
    "Rec.1886": ("Rec. ITU-R BT.709 (BT.1886 gamma 2.4)", _REC709, _GAMMA_24),
    "Rec.2020": ("Rec. ITU-R BT.2020 (BT.1886 gamma 2.4)", _REC2020, _GAMMA_24),
}

_BRADFORD = (
    (0.8951, 0.2664, -0.1614),
    (-0.7502, 1.7135, 0.0367),
    (0.0389, -0.0685, 1.0296),
)

_profiles = {}


def _xy_to_xyz(x, y):
    return (x / y, 1.0, (1.0 - x - y) / y)


def _mul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def _apply(m, v):
    return [sum(m[i][k] * v[k] for k in range(3)) for i in range(3)]


def _inverse(m):
    (a, b, c), (d, e, f), (g, h, i) = m
    det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
    return [
        [(e * i - f * h) / det, (c * h - b * i) / det, (b * f - c * e) / det],
        [(f * g - d * i) / det, (a * i - c * g) / det, (c * d - a * f) / det],
        [(d * h - e * g) / det, (b * g - a * h) / det, (a * e - b * d) / det],
    ]


def _rgb_to_xyz(primaries, white):
    columns = [_xy_to_xyz(*p) for p in primaries]
    m = [[columns[j][i] for j in range(3)] for i in range(3)]
    scale = _apply(_inverse(m), _xy_to_xyz(*white))
    return [[m[i][j] * scale[j] for j in range(3)] for i in range(3)]


def _bradford(src_white, dst_white):
    src = _apply(_BRADFORD, src_white)
    dst = _apply(_BRADFORD, dst_white)
    scale = [[dst[i] / src[i] if i == j else 0.0 for j in range(3)] for i in range(3)]
    return _mul(_inverse(_BRADFORD), _mul(scale, _BRADFORD))


def _s15(value):
    return struct.pack(">i", int(round(value * 65536)))


def _xyz_tag(xyz):
    return b"XYZ \0\0\0\0" + b"".join(_s15(c) for c in xyz)


def _text_tag(text):
    encoded = text.encode("utf-16-be")
    return b"mluc\0\0\0\0" + struct.pack(">II2s2sII", 1, 12, b"en", b"US", len(encoded), 28) + encoded


def _curve_tag(curve):
    kind, params = curve
    return b"para\0\0\0\0" + struct.pack(">HH", kind, 0) + b"".join(_s15(p) for p in params)


def _matrix_tag(m):
    return b"sf32\0\0\0\0" + b"".join(_s15(v) for row in m for v in row)


def build_profile(display):
    """Return the ICC profile bytes for one of the DISPLAYS."""
    if display in _profiles:
        return _profiles[display]

    description, primaries, curve = DISPLAYS[display]
    chad = _bradford(_xy_to_xyz(*_D65), _PCS_WHITE)
    to_pcs = _mul(chad, _rgb_to_xyz(primaries, _D65))
    trc = _curve_tag(curve)
    tags = [
        (b"desc", _text_tag(description)),
        (b"cprt", _text_tag("No copyright, use freely")),
        (b"wtpt", b"XYZ \0\0\0\0" + _PCS_WHITE_BYTES),
        (b"chad", _matrix_tag(chad)),
        (b"rXYZ", _xyz_tag([row[0] for row in to_pcs])),
        (b"gXYZ", _xyz_tag([row[1] for row in to_pcs])),
        (b"bXYZ", _xyz_tag([row[2] for row in to_pcs])),
        (b"rTRC", trc),
        (b"gTRC", trc),
        (b"bTRC", trc),
    ]

    # Tag data follows the tag table, 4-byte aligned. The three curves share one block.
    data_start = 128 + 4 + 12 * len(tags)
    table = bytearray(struct.pack(">I", len(tags)))
    data = bytearray()
    placed = {}
    for signature, body in tags:
        if body not in placed:
            placed[body] = data_start + len(data)
            data += body + b"\0" * (-len(body) % 4)
        table += struct.pack(">4sII", signature, placed[body], len(body))

    size = data_start + len(data)
    header = struct.pack(
        ">I4sI4s4s4s6H4s4sI4sIQI",
        size, b"\0\0\0\0", 0x04200000, b"mntr", b"RGB ", b"XYZ ",
        2026, 1, 1, 0, 0, 0,
        b"acsp", b"\0\0\0\0", 0, b"\0\0\0\0", 0, 0, 0,
    ) + _PCS_WHITE_BYTES + b"\0" * 48
    profile = bytearray(header + table + data)
    profile[84:100] = hashlib.md5(bytes(profile)).digest()
    _profiles[display] = bytes(profile)
    return _profiles[display]


# --- PNG -------------------------------------------------------------------------------

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
# Chunks that also describe color; a newly assigned profile replaces them.
_PNG_COLOR_CHUNKS = {b"iCCP", b"sRGB", b"cICP", b"gAMA", b"cHRM"}


def _png_chunk(kind, body):
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)


def tag_png(data, profile, name):
    if not data.startswith(_PNG_SIGNATURE):
        raise ValueError("not a PNG file")
    out = bytearray(_PNG_SIGNATURE)
    pos = len(_PNG_SIGNATURE)
    while pos < len(data):
        length, kind = struct.unpack(">I4s", data[pos:pos + 8])
        chunk = data[pos:pos + 12 + length]
        pos += 12 + length
        if kind in _PNG_COLOR_CHUNKS:
            continue
        out += chunk
        if kind == b"IHDR":
            body = name.encode("latin-1", "replace")[:79] + b"\0\0" + zlib.compress(profile)
            out += _png_chunk(b"iCCP", body)
    return bytes(out)


# --- JPEG ------------------------------------------------------------------------------

_ICC_MARKER = b"ICC_PROFILE\0"


def tag_jpeg(data, profile):
    if not data.startswith(b"\xff\xd8"):
        raise ValueError("not a JPEG file")
    leading, others = [], []
    pos = 2
    while pos + 4 <= len(data) and data[pos] == 0xFF:
        marker = data[pos + 1]
        if marker == 0xDA:  # start of scan: compressed image data follows
            break
        length = struct.unpack(">H", data[pos + 2:pos + 4])[0]
        segment = data[pos:pos + 2 + length]
        pos += 2 + length
        if marker == 0xE2 and segment[4:16] == _ICC_MARKER:
            continue
        # JFIF and Exif headers must stay first.
        (leading if marker in (0xE0, 0xE1) and not others else others).append(segment)

    chunk_size = 65519
    chunks = [profile[i:i + chunk_size] for i in range(0, len(profile), chunk_size)]
    icc_segments = [
        b"\xff\xe2" + struct.pack(">H", 2 + len(_ICC_MARKER) + 2 + len(chunk))
        + _ICC_MARKER + bytes((index + 1, len(chunks))) + chunk
        for index, chunk in enumerate(chunks)
    ]
    return b"\xff\xd8" + b"".join(leading + icc_segments + others) + data[pos:]


# --- TIFF ------------------------------------------------------------------------------

_TIFF_ICC_TAG = 34675


def tag_tiff(data, profile):
    order = {b"II": "<", b"MM": ">"}.get(data[:2])
    if order is None or struct.unpack(order + "H", data[2:4])[0] != 42:
        raise ValueError("not a TIFF file")
    ifd = struct.unpack(order + "I", data[4:8])[0]
    count = struct.unpack(order + "H", data[ifd:ifd + 2])[0]
    entries = [data[ifd + 2 + 12 * i:ifd + 14 + 12 * i] for i in range(count)]
    next_ifd = data[ifd + 2 + 12 * count:ifd + 6 + 12 * count]
    entries = [e for e in entries if struct.unpack(order + "H", e[:2])[0] != _TIFF_ICC_TAG]

    # Append the profile and a rewritten first directory; the old directory is left unused.
    out = bytearray(data)
    out += b"\0" * (len(out) % 2)
    profile_offset = len(out)
    out += profile + b"\0" * (len(profile) % 2)
    entries.append(struct.pack(order + "HHII", _TIFF_ICC_TAG, 7, len(profile), profile_offset))
    entries.sort(key=lambda e: struct.unpack(order + "H", e[:2])[0])
    new_ifd = len(out)
    out += struct.pack(order + "H", len(entries)) + b"".join(entries) + next_ifd
    out[4:8] = struct.pack(order + "I", new_ifd)
    return bytes(out)


# --- Files -----------------------------------------------------------------------------

SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff"}


def tag_file(path, display):
    """Embed the profile for a Blender display name in an image file, in place."""
    profile = build_profile(display)
    with open(path, "rb") as f:
        data = f.read()
    if data.startswith(_PNG_SIGNATURE):
        tagged = tag_png(data, profile, DISPLAYS[display][0])
    elif data.startswith(b"\xff\xd8"):
        tagged = tag_jpeg(data, profile)
    elif data[:2] in (b"II", b"MM"):
        tagged = tag_tiff(data, profile)
    else:
        raise ValueError("only PNG, JPEG and TIFF files can carry a color profile")
    temp = path + ".ecm-tmp"
    with open(temp, "wb") as f:
        f.write(tagged)
    os.replace(temp, path)
