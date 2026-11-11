#!/usr/bin/env python3
"""Supertonic's networks as the browser fetches them: half the download.

    python3 -I ops/tts/pack.py SOURCE_DIR OUT_DIR

Every large float32 weight of a network (1024 values or more) leaves the
.onnx and goes, as float16, into <net>.f16.data, one after the other; the
.onnx keeps the rest of the graph byte for byte and says where each weight
lies as float32 external data (location <net>.data, offset and length in
float32 bytes, which are twice those in the .f16 file). The worker widens the
.f16 file to float32 and hands it to onnxruntime under that name, so the
model computes in float32 as before: only the weights are rounded to float16
(their largest value is about 5, float16 keeps 11 bits of every one).
Plain protobuf and numpy, nothing else; the result is the same on every run.
"""
import os
import sys

import numpy as np


def varint(buf, i):
    shift = value = 0
    while True:
        b = buf[i]
        i += 1
        value |= (b & 0x7F) << shift
        if b < 0x80:
            return value, i
        shift += 7


def put_varint(value):
    out = bytearray()
    while True:
        b = value & 0x7F
        value >>= 7
        if value:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def fields(buf):
    """(number, wire type, raw bytes of the whole field, payload) of a message."""
    i = 0
    while i < len(buf):
        start = i
        key, i = varint(buf, i)
        number, wire = key >> 3, key & 7
        if wire == 0:
            value, i = varint(buf, i)
        elif wire == 1:
            value, i = buf[i:i + 8], i + 8
        elif wire == 2:
            n, i = varint(buf, i)
            value, i = buf[i:i + n], i + n
        elif wire == 5:
            value, i = buf[i:i + 4], i + 4
        else:
            raise ValueError('unexpected wire type %d' % wire)
        yield number, wire, buf[start:i], value


def field(number, payload):
    return put_varint(number << 3 | 2) + put_varint(len(payload)) + payload


def tensor(buf, location, data):
    """The tensor with its float32 weight moved into data (as float16), or as it was
    (a small one, another type, or one without raw_data)."""
    dims, kind, raw = [], None, None
    for number, wire, _, value in fields(buf):
        if number == 1:
            if wire == 2:
                j = 0
                while j < len(value):
                    d, j = varint(value, j)
                    dims.append(d)
            else:
                dims.append(value)
        elif number == 2:
            kind = value
        elif number == 9:
            raw = value
    count = int(np.prod(dims)) if dims else 1
    if kind != 1 or raw is None or count < 1024:
        return buf
    weights = np.frombuffer(raw, dtype='<f4')
    half = weights.astype('<f2')
    if weights.size != count or not np.isfinite(half).all():
        raise ValueError('a weight does not fit float16')
    offset = data.tell()
    data.write(half.tobytes())
    out = b''.join(whole for number, _, whole, _ in fields(buf) if number != 9)
    for key, value in (('location', location), ('offset', str(2 * offset)), ('length', str(4 * count))):
        out += field(13, field(1, key.encode()) + field(2, value.encode()))
    return out + put_varint(14 << 3) + put_varint(1)   # data_location = EXTERNAL


def pack(source, out_dir, name):
    with open(os.path.join(source, name + '.onnx'), 'rb') as f:
        model = f.read()
    with open(os.path.join(out_dir, name + '.f16.data'), 'wb') as data:
        out = bytearray()
        for number, _, whole, value in fields(model):
            if number != 7:
                out += whole
                continue
            graph = bytearray()
            for g_number, _, g_whole, g_value in fields(value):
                graph += field(5, tensor(g_value, name + '.data', data)) if g_number == 5 else g_whole
            out += field(7, bytes(graph))
    with open(os.path.join(out_dir, name + '.onnx'), 'wb') as f:
        f.write(out)


def main():
    source, out_dir = sys.argv[1], sys.argv[2]
    for name in ('duration_predictor', 'text_encoder', 'vector_estimator', 'vocoder'):
        pack(source, out_dir, name)


if __name__ == '__main__':
    main()
