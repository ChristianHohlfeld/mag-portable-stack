#!/usr/bin/env python3
"""What unwrap.py must keep: every model gets UVs in a time a job has. The
plain unwrap lays any surface out, quickly, with charts that do not lie on
one another; and when xatlas takes too long or fails, it is the plain one
that answers. (xatlas itself is not here: it is played by stand-ins.)"""
import os
import pathlib
import sys
import time

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import unwrap  # noqa: E402


def ball(rows, rough, seed=3):
    """A ball of rows x 2 rows quads, its surface pushed in and out at random."""
    rng = np.random.default_rng(seed)
    cols = 2 * rows
    lat = np.linspace(0.02, np.pi - 0.02, rows + 1)
    lon = np.linspace(0, 2 * np.pi, cols, endpoint=False)
    la, lo = np.meshgrid(lat, lon, indexing="ij")
    radius = 1.0 + rough * rng.standard_normal(la.shape)
    verts = np.stack([radius * np.sin(la) * np.cos(lo), radius * np.sin(la) * np.sin(lo), radius * np.cos(la)], axis=-1).reshape(-1, 3)
    faces = []
    for i in range(rows):
        for j in range(cols):
            a, b, c, d = i * cols + j, i * cols + (j + 1) % cols, (i + 1) * cols + j, (i + 1) * cols + (j + 1) % cols
            faces += [(a, c, b), (b, c, d)]
    return verts.astype(np.float32), np.array(faces, np.uint32)


def laid_out(verts, faces, size):
    start = time.time()
    vmapping, uv_faces, uvs, charts = unwrap.plain_unwrap(verts, faces, size)
    took = time.time() - start
    assert uv_faces.shape == faces.shape and uv_faces.max() < len(vmapping) and vmapping.max() < len(verts)
    assert np.array_equal(vmapping[uv_faces], faces.astype(np.int64))            # the same faces, of the same corners
    assert uvs.shape == (len(vmapping), 2) and np.isfinite(uvs).all() and uvs.min() >= 0.0 and uvs.max() <= 1.0
    return uv_faces, uvs * (size - 1), charts, took


def main():
    size = 512
    for rough in (0.0, 0.03):                              # a smooth ball, and one as rough as a model from a photo
        verts, faces = ball(48, rough)
        uv_faces, texel, charts, took = laid_out(verts, faces, size)
        assert took < 10.0, took
        assert 6 <= charts < len(faces) / 4, charts        # patches, not single faces
        # No chart lies on another: every vertex belongs to one chart, and the charts' boxes are apart.
        corner = np.sort(uv_faces, axis=1)
        owner = np.arange(len(texel))
        for _ in range(200):                               # a chart is what hangs together by its faces
            low = np.minimum.reduce([owner[uv_faces[:, k]] for k in range(3)])
            before = owner.copy()
            for k in range(3):
                np.minimum.at(owner, uv_faces[:, k], low)
            if np.array_equal(owner, before):
                break
        ids = np.unique(owner)
        assert len(ids) == charts, (len(ids), charts)
        boxes = np.array([np.concatenate((texel[owner == i].min(axis=0), texel[owner == i].max(axis=0))) for i in ids])
        taken = np.zeros((size + 2, size + 2), np.int32)
        for x0, y0, x1, y1 in boxes:
            taken[int(np.floor(y0)):int(np.ceil(y1)) + 1, int(np.floor(x0)):int(np.ceil(x1)) + 1] += 1
        assert taken.max() == 1, taken.max()
        # The atlas is used, and every part of the surface gets about as many texels as any other.
        a, b = texel[uv_faces[:, 1]] - texel[uv_faces[:, 0]], texel[uv_faces[:, 2]] - texel[uv_faces[:, 0]]
        drawn = 0.5 * np.abs(a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0])
        v = verts[faces.astype(np.int64)]
        real = 0.5 * np.linalg.norm(np.cross(v[:, 1] - v[:, 0], v[:, 2] - v[:, 0]), axis=1)
        dense = drawn / real
        assert drawn.sum() > 0.2 * size * size, drawn.sum() / size / size
        assert np.percentile(dense, 10) > 0.25 * np.percentile(dense, 90), (np.percentile(dense, 10), np.percentile(dense, 90))
        assert (drawn > 1e-9).mean() > 0.99                # (almost) no face is laid out edge-on

    # A large rough surface is still a matter of seconds.
    verts, faces = ball(220, 0.01)
    assert len(faces) > 190000
    *_, took = laid_out(verts, faces, 2048)
    assert took < 30.0, took

    # ---- with xatlas -------------------------------------------------------------
    verts, faces = ball(24, 0.02)
    good = (np.arange(len(verts)), faces.astype(np.int32), np.full((len(verts), 2), 0.5, np.float32))

    def quick(v, f, out):
        out.send(good)

    def slow(v, f, out):
        time.sleep(60)

    def broken(v, f, out):
        os._exit(1)                                        # as if xatlas were missing, or crashed

    def wrong(v, f, out):
        out.send((good[0], good[1][:5], good[2]))

    unwrap._xatlas = quick
    vmapping, uv_faces, uvs = unwrap.unwrap(verts, faces, 256, 20)
    assert np.array_equal(uv_faces, good[1]) and np.allclose(uvs, 0.5)        # done in time: xatlas' own answer
    for stand_in, most in ((slow, 6.0), (broken, 6.0), (wrong, 6.0)):
        unwrap._xatlas = stand_in
        start = time.time()
        vmapping, uv_faces, uvs = unwrap.unwrap(verts, faces, 256, 1.0)
        assert time.time() - start < most, (stand_in.__name__, time.time() - start)   # not a second longer than it was given
        assert np.array_equal(vmapping[uv_faces], faces.astype(np.int64)) and not np.allclose(uvs, 0.5), stand_in.__name__
    time.sleep(0.2)
    left = []
    for pid in (p for p in os.listdir("/proc") if p.isdigit()):
        try:
            if open("/proc/%s/stat" % pid).read().rsplit(")", 1)[1].split()[1] == str(os.getpid()):
                left.append(pid)
        except OSError:
            pass
    assert not left, left                                                      # and the one that took too long is gone
    print("unwrap ok")


if __name__ == "__main__":
    main()
