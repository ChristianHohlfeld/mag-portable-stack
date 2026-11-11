"""UVs for the surface of an Idol model, in a time a job has.

xatlas makes the better charts, and on most models it is done within a
minute. On a rough surface (the porous shell a photo of a bathroom gives) it
cuts hundreds of thousands of charts and does not come to an end: a job sat
in it until its limit and failed after half an hour of work. So xatlas runs
in a process of its own for a limited time, and when that is over the
surface is unwrapped the plain way, which takes a second. A job always gets
its texture. Only numpy and scipy here (xatlas in its own process).
"""
import math
import multiprocessing

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components


def plain_unwrap(verts, faces, size, gap=2):
    """An unwrap that never takes long, for a surface xatlas cannot finish.

    Every face looks mostly along one of the six axis directions. Faces that
    look the same way and hang together are one chart, laid flat by leaving
    that axis out; the charts are put side by side in rows, `gap` texels
    apart, as large as fits (the bake carries every chart's colours some
    texels over its edge). Returns what xatlas.parametrize returns, and how
    many charts there are.
    """
    verts = np.asarray(verts, np.float64)
    faces = np.asarray(faces, np.int64)
    count = len(faces)
    corners = verts[faces]
    normal = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
    # Which faces share an edge.
    edges = np.sort(faces[:, [0, 1, 1, 2, 2, 0]].reshape(-1, 2), axis=1)
    owner = np.repeat(np.arange(count), 3)
    order = np.lexsort((edges[:, 1], edges[:, 0]))
    edges, owner = edges[order], owner[order]
    shared = (edges[1:] == edges[:-1]).all(axis=1)
    one, other = owner[:-1][shared], owner[1:][shared]
    beside = coo_matrix((np.ones(2 * len(one)), (np.concatenate((one, other)), np.concatenate((other, one)))),
                        shape=(count, count)).tocsr()
    # A rough surface looks a little differently with every face. Which way a
    # face looks is asked of its neighbourhood, so that charts are patches
    # and not single faces; a face that would lie nearly edge-on that way
    # keeps its own.
    around = normal / np.maximum(np.linalg.norm(normal, axis=1, keepdims=True), 1e-30)
    own = around.copy()
    for _ in range(4):
        around = around + beside @ around
        around /= np.maximum(np.linalg.norm(around, axis=1, keepdims=True), 1e-30)
    axis = np.abs(around).argmax(axis=1)
    edge_on = np.abs(own[np.arange(count), axis]) < 0.3
    axis[edge_on] = np.abs(own[edge_on]).argmax(axis=1)
    facing = np.where(edge_on[:, None], own, around)
    way = axis * 2 + (facing[np.arange(count), axis] > 0)
    # Faces that share an edge and look the same way belong together.
    same = way[one] == way[other]
    graph = coo_matrix((np.ones(int(same.sum()), np.int8), (one[same], other[same])), shape=(count, count))
    charts, chart = connected_components(graph, directed=False)
    chart = chart.astype(np.int64)
    # Every chart has its own vertices, laid flat.
    keys, corner = np.unique(chart[:, None] * len(verts) + faces, return_inverse=True)
    vmapping, of_chart = keys % len(verts), keys // len(verts)
    uv_faces = corner.reshape(-1, 3)
    kept = np.array([[1, 2], [2, 0], [0, 1]])[np.bincount(chart, weights=axis, minlength=charts).astype(np.int64)
                                              // np.maximum(np.bincount(chart, minlength=charts), 1)]
    flat = np.take_along_axis(verts[vmapping], kept[of_chart], axis=1)
    low = np.full((charts, 2), np.inf)
    high = np.full((charts, 2), -np.inf)
    np.minimum.at(low, of_chart, flat)
    np.maximum.at(high, of_chart, flat)
    span = high - low
    # Rows of charts, tallest first; as large as still fits the atlas.
    order = np.argsort(-span[:, 1], kind="stable")

    def rows(scale):
        wide, tall = span[order, 0] * scale + gap, span[order, 1] * scale + gap
        at = np.zeros((charts, 2))
        x = y = row = 0.0
        for i in range(charts):
            if x + wide[i] > size and x > 0:
                x, y, row = 0.0, y + row, 0.0
            at[order[i]] = (x, y)
            x, row = x + wide[i], max(row, tall[i])
        return at, y + row, float(wide.max())

    area = float((span[:, 0] * span[:, 1]).sum())
    most = math.sqrt(size * size / max(area, 1e-30))
    least = 0.0
    for _ in range(18):
        scale = (most + least) / 2
        at, height, widest = rows(scale)
        if height <= size and widest <= size:
            least = scale
        else:
            most = scale
    at, _, _ = rows(least)
    texel = at[of_chart] + (flat - low[of_chart]) * least + gap / 2
    uvs = np.column_stack((texel[:, 0] / (size - 1), 1.0 - texel[:, 1] / (size - 1)))
    return vmapping, uv_faces.astype(np.int32), np.clip(uvs, 0.0, 1.0).astype(np.float32), charts


def _xatlas(verts, faces, out):
    import xatlas
    out.send(xatlas.parametrize(verts, faces))


def unwrap(verts, faces, size, seconds):
    """UVs for a surface: by xatlas if it is done within `seconds`, the
    plain way if not (or if it fails). Returns for every new vertex the one
    it was, the faces by new vertices, and the new vertices' UVs."""
    verts = np.ascontiguousarray(verts, np.float32)
    faces = np.ascontiguousarray(faces, np.uint32)
    print(f"UV unwrap: {len(faces)} faces -> {size}² atlas", flush=True)
    here, there = multiprocessing.Pipe(duplex=False)
    worker = multiprocessing.get_context("fork").Process(target=_xatlas, args=(verts, faces, there), daemon=True)
    worker.start()
    there.close()
    found, why = None, f"not done in {seconds:g} s"
    try:
        if here.poll(seconds):
            found = here.recv()
    except (EOFError, OSError):
        why = "xatlas failed"
    if worker.is_alive():
        worker.kill()
    worker.join()
    if found is not None:
        vmapping, uv_faces, uvs = (np.asarray(found[0], np.int64), np.asarray(found[1], np.int32),
                                   np.asarray(found[2], np.float32))
        if len(uv_faces) == len(faces) and np.isfinite(uvs).all():
            return vmapping, uv_faces, uvs
        why = "xatlas failed"
    vmapping, uv_faces, uvs, charts = plain_unwrap(verts, faces, size)
    print(f"UV unwrap: {why}, laid out plainly in {charts} charts", flush=True)
    return vmapping, uv_faces, uvs
