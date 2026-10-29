#!/usr/bin/env python3
"""Read GGUF header metadata without mapping or reading weight tensors.

This intentionally requires only Python's stdlib, and never writes to the GGUF.
"""
import argparse
import os
import struct
import sys

_SCALARS = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 4, 7: 1,
            10: 8, 11: 8, 12: 8}


def _read(f, fmt):
    size = struct.calcsize(fmt)
    value = f.read(size)
    if len(value) != size:
        raise ValueError('truncated GGUF metadata')
    return struct.unpack(fmt, value)[0]


def _skip_string(f, size):
    length = _read(f, '<Q')
    if length > size or f.tell() + length > size:
        raise ValueError('invalid GGUF string length')
    f.seek(length, os.SEEK_CUR)


def _string(f, size, max_length):
    length = _read(f, '<Q')
    if length > max_length or f.tell() + length > size:
        raise ValueError('invalid GGUF metadata key/string length')
    raw = f.read(length)
    if len(raw) != length:
        raise ValueError('truncated GGUF metadata string')
    return raw.decode('utf-8', errors='strict')


def _skip_value(f, typ, size):
    if typ == 8:
        _skip_string(f, size)
    elif typ == 9:
        elem_type = _read(f, '<I')
        count = _read(f, '<Q')
        if elem_type == 8:
            if count > 10_000_000:
                raise ValueError('GGUF string array too large')
            for _ in range(count):
                _skip_string(f, size)
        elif elem_type in _SCALARS:
            total = count * _SCALARS[elem_type]
            if total > size or f.tell() + total > size:
                raise ValueError('invalid GGUF array length')
            f.seek(total, os.SEEK_CUR)
        else:
            raise ValueError(f'unknown GGUF array type {elem_type}')
    elif typ in _SCALARS:
        count = _SCALARS[typ]
        if f.tell() + count > size:
            raise ValueError('invalid GGUF value length')
        f.seek(count, os.SEEK_CUR)
    else:
        raise ValueError(f'unknown GGUF value type {typ}')


def inspect(path):
    file_size = os.path.getsize(path)
    with open(path, 'rb', buffering=1024*1024) as f:
        if f.read(4) != b'GGUF':
            raise ValueError('missing GGUF signature')
        version = _read(f, '<I')
        if version not in (2, 3):
            raise ValueError(f'unsupported GGUF version {version}')
        tensors = _read(f, '<Q')
        count = _read(f, '<Q')
        if count > 100000:
            raise ValueError('GGUF metadata entry count too large')
        keys = set()
        arch = None
        for _ in range(count):
            key = _string(f, file_size, 4096)
            typ = _read(f, '<I')
            if key == 'general.architecture' and typ == 8:
                arch = _string(f, file_size, 256)
            else:
                _skip_value(f, typ, file_size)
            keys.add(key)
        return version, tensors, keys, arch


def elementwise_weight_types(path):
    """Inspect tensor descriptors only; no tensor payload is accessed."""
    file_size = os.path.getsize(path)
    result = []
    with open(path, 'rb', buffering=1024*1024) as f:
        if f.read(4) != b'GGUF':
            raise ValueError('missing GGUF signature')
        version = _read(f, '<I')
        if version not in (2, 3):
            raise ValueError('unsupported GGUF version')
        n_tensors = _read(f, '<Q')
        n_metadata = _read(f, '<Q')
        if n_tensors > 1_000_000 or n_metadata > 100_000:
            raise ValueError('unreasonably many GGUF tensors / metadata keys')
        for _ in range(n_metadata):
            _string(f, file_size, 4096)
            typ = _read(f, '<I')
            _skip_value(f, typ, file_size)
        for _ in range(n_tensors):
            name = _string(f, file_size, 4096)
            ndim = _read(f, '<I')
            if ndim < 1 or ndim > 4:
                raise ValueError('invalid GGUF tensor rank')
            for _ in range(ndim):
                _read(f, '<Q')
            typ = _read(f, '<I')
            _read(f, '<Q')  # tensor offset, not tensor contents
            if any(key in name for key in (
                    'indexer_kpool_ape', 'ssm_a', 'ssm_dt',
                    'hc_attn_scale', 'hc_attn_base',
                    'hc_ffn_scale', 'hc_ffn_base')):
                result.append((name, {0: 'f32', 1: 'f16', 8: 'q8_0',
                                      20: 'iq4_nl', 29: 'iq1_m', 30: 'bf16'}.get(
                                          typ, f'ggml_type_{typ}')))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('model', help='first GGUF shard')
    parser.add_argument('--require', action='append', default=[], help='required metadata key')
    parser.add_argument('--require-kda-head-dim', action='store_true',
                        help='require compatible KDA head dimension (legacy glm5next aliases supported)')
    parser.add_argument('--show-elementwise-weights', action='store_true',
                        help='show GGUF tensor types of model weights used in elementwise GLM operations')
    args = parser.parse_args(argv)
    try:
        version, tensors, keys, arch = inspect(args.model)
    except (OSError, UnicodeError, ValueError) as e:
        print(f'GLM GGUF metadata: invalid header: {e}', file=sys.stderr)
        return 4
    print(f'GGUF metadata: version={version}, architecture={arch}, '
          f'keys={len(keys)}, tensors={tensors}')
    relevant = sorted(k for k in keys if k.startswith(('glm5next.', 'glm5-next.')) and
                      any(x in k for x in ('kda', 'ssm', 'head', 'attention', 'indexer', 'hyper')))
    for key in relevant:
        print('  ' + key)
    if args.show_elementwise_weights:
        try:
            for name, typ in elementwise_weight_types(args.model):
                print(f'  tensor {name}: {typ}')
        except (OSError, UnicodeError, ValueError) as e:
            print(f'GLM GGUF tensor-directory error: {e}', file=sys.stderr)
            return 4
    required = list(args.require)
    if args.require_kda_head_dim and arch in ('glm5next', 'glm5-next'):
        # The original glm5next converter stores the same KDA head width as
        # ssm.state_size, and the k-pool width as attention.indexer.block_size.
        # Never accept these aliases for the canonical glm5-next architecture.
        if arch == 'glm5next' and f'{arch}.kda.head_dim' not in keys:
            required.extend([f'{arch}.ssm.state_size',
                             f'{arch}.attention.indexer.block_size'])
        else:
            required.append(f'{arch}.kda.head_dim')
    missing = [key for key in required if key not in keys]
    if missing:
        print(f'GLM GGUF incompatible: missing required metadata: {", ".join(missing)}', file=sys.stderr)
        print('Refusing model switch; existing NUMA1 service unchanged', file=sys.stderr)
        return 4
    if arch not in ('glm5next', 'glm5-next'):
        print(f'GLM GGUF incompatible: architecture={arch}', file=sys.stderr)
        return 4
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
