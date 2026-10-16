"""A small TIFF/BigTIFF reader for single-band rasters (elevation grids), with the GeoTIFF tags.
No imaging library is needed. Supports strips and tiles; no compression, Deflate, LZW, PackBits and LZMA; the
horizontal and floating-point predictors; 8/16/32/64-bit integer and 32/64-bit float samples."""
import lzma
import struct
import zlib

import numpy as np

TYPE_FORMAT = {1: "B", 2: "c", 3: "H", 4: "I", 5: "II", 6: "b", 7: "B", 8: "h", 9: "i", 10: "ii", 11: "f", 12: "d",
               16: "Q", 17: "q", 18: "Q"}
TYPE_SIZE = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8, 16: 8, 17: 8, 18: 8}
TAG = dict(width=256, height=257, bits=258, compression=259, photometric=262, strip_offsets=273, samples=277,
           rows_per_strip=278, strip_counts=279, planar=284, predictor=317, tile_width=322, tile_height=323,
           tile_offsets=324, tile_counts=325, sample_format=339, pixel_scale=33550, tiepoint=33922,
           transformation=34264, geokeys=34735, geo_doubles=34736, geo_ascii=34737, nodata=42113)


class TiffError(Exception):
    pass


class Tiff:
    """The first image of a TIFF file."""

    def __init__(self, path):
        self.path = path
        self._file = open(path, "rb")
        head = self._file.read(8)
        if head[:2] == b"II":
            self.e = "<"
        elif head[:2] == b"MM":
            self.e = ">"
        else:
            raise TiffError("This is not a TIFF file.")
        magic = struct.unpack(self.e + "H", head[2:4])[0]
        if magic == 42:
            self.big, offset = False, struct.unpack(self.e + "I", head[4:8])[0]
        elif magic == 43:
            self.big = True
            offset = struct.unpack(self.e + "Q", self._file.read(8))[0]
        else:
            raise TiffError("This is not a TIFF file.")
        self.tags = self._read_ifd(offset)

    def close(self):
        self._file.close()

    def _read_ifd(self, offset):
        f, e = self._file, self.e
        f.seek(offset)
        count = struct.unpack(e + ("Q" if self.big else "H"), f.read(8 if self.big else 2))[0]
        size, inline = (20, 8) if self.big else (12, 4)
        raw = f.read(count * size)
        tags = {}
        for k in range(count):
            entry = raw[k * size:(k + 1) * size]
            tag, kind = struct.unpack(e + "HH", entry[:4])
            n = struct.unpack(e + ("Q" if self.big else "I"), entry[4:4 + inline])[0]
            field = entry[4 + inline:]
            if kind not in TYPE_SIZE:
                continue
            total = n * TYPE_SIZE[kind]
            if total <= inline:
                data = field[:total]
            else:
                where = struct.unpack(e + ("Q" if self.big else "I"), field)[0]
                f.seek(where)
                data = f.read(total)
            tags[tag] = (kind, n, data)
        return tags

    def get(self, tag, default=None):
        """Tag values: a numpy array for numbers, a str for text; `default` when the tag is missing."""
        if tag not in self.tags:
            return default
        kind, n, data = self.tags[tag]
        if kind == 2:
            return data.split(b"\0")[0].decode("latin-1")
        if kind in (5, 10):
            numbers = np.frombuffer(data, self.e + ("u4" if kind == 5 else "i4")).astype(np.float64)
            return numbers[0::2] / numbers[1::2]
        dtype = {1: "u1", 3: "u2", 4: "u4", 6: "i1", 7: "u1", 8: "i2", 9: "i4", 11: "f4", 12: "f8", 16: "u8",
                 17: "i8", 18: "u8"}[kind]
        return np.frombuffer(data, np.dtype(dtype).newbyteorder(self.e))

    def one(self, tag, default=None):
        value = self.get(tag)
        if value is None:
            return default
        if isinstance(value, str):
            return value
        return value[0] if len(value) else default

    @property
    def width(self):
        return int(self.one(TAG["width"]))

    @property
    def height(self):
        return int(self.one(TAG["height"]))

    @property
    def samples(self):
        return int(self.one(TAG["samples"], 1))

    def dtype(self):
        bits = int(self.one(TAG["bits"], 1))
        fmt = int(self.one(TAG["sample_format"], 1))
        kind = {1: "u", 2: "i", 3: "f"}.get(fmt)
        if kind is None or bits not in (8, 16, 32, 64) or (kind == "f" and bits not in (32, 64)):
            raise TiffError("Unsupported sample type (%d-bit, format %d)." % (bits, fmt))
        return np.dtype("%s%d" % (kind, bits // 8)).newbyteorder(self.e)

    def geokeys(self):
        """{key id: value} of the GeoTIFF key directory (numbers; text keys are looked up in the ASCII tag)."""
        directory = self.get(TAG["geokeys"])
        keys = {}
        if directory is None or len(directory) < 4:
            return keys
        for k in range(int(directory[3])):
            key, location, count, value = (int(v) for v in directory[4 + 4 * k:8 + 4 * k])
            if location == 0:
                keys[key] = value
            elif location == TAG["geo_ascii"]:
                keys[key] = (self.get(TAG["geo_ascii"]) or "")[value:value + count].rstrip("|")
            elif location == TAG["geo_doubles"]:
                doubles = self.get(TAG["geo_doubles"])
                if doubles is not None:
                    keys[key] = float(doubles[value])
        return keys

    def read(self):
        """Pixels as an array (height, width)."""
        if self.samples != 1:
            raise TiffError("This TIFF has %d bands: it is an image, not an elevation grid." % self.samples)
        compression = int(self.one(TAG["compression"], 1))
        predictor = int(self.one(TAG["predictor"], 1))
        dtype = self.dtype()
        width, height = self.width, self.height
        out = np.empty((height, width), dtype)
        tiled = TAG["tile_width"] in self.tags
        if tiled:
            tw, th = int(self.one(TAG["tile_width"])), int(self.one(TAG["tile_height"]))
            offsets, counts = self.get(TAG["tile_offsets"]), self.get(TAG["tile_counts"])
            across = -(-width // tw)
        else:
            tw, th = width, int(min(self.one(TAG["rows_per_strip"], height), height))
            offsets, counts = self.get(TAG["strip_offsets"]), self.get(TAG["strip_counts"])
            across = 1
        for k, (offset, count) in enumerate(zip(offsets, counts)):
            self._file.seek(int(offset))
            chunk = decompress(self._file.read(int(count)), compression, th * tw * dtype.itemsize)
            row0, col0 = (k // across) * th, (k % across) * tw
            rows = th if tiled else min(th, height - row0)
            need = rows * tw * dtype.itemsize
            if len(chunk) < need:
                raise TiffError("A block of this TIFF is shorter than expected.")
            block = undo_predictor(chunk[:need], predictor, dtype, rows, tw)
            use_rows, use_cols = min(rows, height - row0), min(tw, width - col0)
            out[row0:row0 + use_rows, col0:col0 + use_cols] = block[:use_rows, :use_cols]
        return out


def decompress(data, compression, expected):
    if compression == 1:
        return data
    if compression in (8, 32946):
        return zlib.decompress(data)
    if compression == 5:
        return lzw_decode(data, expected)
    if compression == 32773:
        return packbits_decode(data, expected)
    if compression == 34925:
        return lzma.decompress(data)
    if compression == 50000:
        try:
            import zstandard
            return zstandard.ZstdDecompressor().decompress(data, max_output_size=expected)
        except ImportError:
            raise TiffError("This TIFF uses Zstandard compression, which is not available here.") from None
    raise TiffError("Unsupported TIFF compression (%d). Use none, Deflate, LZW, PackBits or LZMA." % compression)


def undo_predictor(chunk, predictor, dtype, rows, width):
    if predictor == 1:
        return np.frombuffer(chunk, dtype).reshape(rows, width)
    if predictor == 2:
        block = np.frombuffer(chunk, dtype).reshape(rows, width)
        return np.cumsum(block, axis=1, dtype=dtype)
    if predictor == 3:  # floating point: byte planes, most significant first, then summed along the row
        size = dtype.itemsize
        planes = np.cumsum(np.frombuffer(chunk, np.uint8).reshape(rows, size * width), axis=1, dtype=np.uint8)
        little = planes.reshape(rows, size, width)[:, ::-1, :].transpose(0, 2, 1)
        return np.ascontiguousarray(little).view("<f%d" % size).reshape(rows, width)
    raise TiffError("Unsupported TIFF predictor (%d)." % predictor)


def packbits_decode(data, expected):
    out, i, n = bytearray(), 0, len(data)
    while i < n and len(out) < expected:
        k = data[i]
        i += 1
        if k < 128:
            out += data[i:i + k + 1]
            i += k + 1
        elif k > 128:
            out += data[i:i + 1] * (257 - k)
            i += 1
    return bytes(out)


def lzw_decode(data, expected):
    """TIFF LZW (MSB first, 'early change')."""
    table = [bytes([i]) for i in range(256)] + [b"", b""]
    out = bytearray()
    buffer, bits, width, previous = 0, 0, 9, None
    for byte in data:
        buffer = (buffer << 8) | byte
        bits += 8
        while bits >= width:
            code = (buffer >> (bits - width)) & ((1 << width) - 1)
            bits -= width
            buffer &= (1 << bits) - 1
            if code == 257:
                return bytes(out)
            if code == 256:
                del table[258:]
                width, previous = 9, None
                continue
            if previous is None:
                entry = table[code]
            elif code < len(table):
                entry = table[code]
                table.append(previous + entry[:1])
            else:
                entry = previous + previous[:1]
                table.append(entry)
            out += entry
            previous = entry
            size = len(table) + 1
            width = 9 if size < 512 else 10 if size < 1024 else 11 if size < 2048 else 12
        if len(out) >= expected:
            break
    return bytes(out)
