"""From the Gaussians of a splat to the closed body they stand for.

A splat is a cloud of small flat Gaussians lying on what a photo showed.
`opacity_field` writes them into a grid as they are, each with its own size
and its own turn; `body_of` floods that grid from outside and keeps what the
flood does not reach. mesh.py takes the surface from the result. Only numpy
and scipy here, so this is tested without the Idol environment.
"""
import numpy as np
from scipy.ndimage import binary_dilation, binary_erosion, distance_transform_edt, label

THIN = 0.7      # a Gaussian is at least this many cells thick: thinner ones would fall between the cells
REACH = 2.5     # a Gaussian is written out to this many of its widths
WIDEST = 8      # ... and at most this many cells far
PORE = 2        # holes in the shell up to about twice this many cells are closed
LEAK = 0.1      # a gap in the shell up to about twice this part of the grid is a tear, not an opening
OWN = 0.03      # a piece this large against the largest is a figure of its own, not a loose bit


def opacity_field(xyz, alpha, scales, turns, lo, spacing, resolution):
    """How opaque every cell of the grid is: 1 - the product of (1 - what each
    Gaussian covers of it). A Gaussian is an ellipsoid with its own three
    widths (`scales`) and its own turn (`turns`, quaternions w x y z)."""
    cells = resolution ** 3
    clear = np.zeros(cells, dtype=np.float64)                   # the logarithm of what still shines through
    widths = np.maximum(scales, THIN * spacing).astype(np.float32)
    q = turns / np.maximum(np.linalg.norm(turns, axis=1, keepdims=True), 1e-12)
    w, x, y, z = q[:, 0], q[:, 1], q[:, 2], q[:, 3]
    turn = np.stack([np.stack([1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)], axis=1),
                     np.stack([2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)], axis=1),
                     np.stack([2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)], axis=1)], axis=1).astype(np.float32)
    # d' * shape * d is how many widths a point is from the middle, squared.
    shape = np.einsum("nij,nj,nkj->nik", turn, 1.0 / (widths * widths), turn)
    far = np.clip(np.ceil(REACH * widths.max(axis=1) / spacing), 1, WIDEST).astype(np.int64)
    middle = ((xyz - lo) / spacing).astype(np.float32)
    home = np.rint(middle).astype(np.int64)
    for r in np.unique(far):
        pick = np.flatnonzero(far == r)
        side = np.arange(-r, r + 1)
        box = np.stack(np.meshgrid(side, side, side, indexing="ij"), axis=-1).reshape(-1, 3)
        for start in range(0, len(pick), max(1, 1500000 // len(box))):
            part = pick[start:start + max(1, 1500000 // len(box))]
            cell = home[part][:, None, :] + box[None, :, :]
            off = (cell.astype(np.float32) - middle[part][:, None, :]) * np.float32(spacing)
            apart = np.einsum("nki,nij,nkj->nk", off, shape[part], off)
            covers = alpha[part][:, None] * np.exp(-0.5 * apart)
            inside = np.all((cell >= 0) & (cell < resolution), axis=2) & (apart <= REACH * REACH)
            flat = (cell[..., 0] * resolution + cell[..., 1]) * resolution + cell[..., 2]
            clear += np.bincount(flat[inside], weights=np.log1p(-np.minimum(covers[inside], 0.999)), minlength=cells)
    return (1.0 - np.exp(clear)).astype(np.float32).reshape((resolution,) * 3)


def flooded(shell, pore):
    """What a flood from the edge of the grid reaches when holes in the shell
    up to about twice `pore` cells are closed first (True: outside)."""
    closed = binary_dilation(shell, iterations=pore) if pore <= 3 else distance_transform_edt(~shell) <= pore
    rooms, _ = label(~closed)
    edge = np.unique(np.concatenate([rooms[0].ravel(), rooms[-1].ravel(), rooms[:, 0].ravel(), rooms[:, -1].ravel(),
                                     rooms[:, :, 0].ravel(), rooms[:, :, -1].ravel()]))
    outside = np.isin(rooms, edge[edge > 0])
    if pore <= 3:
        return ~binary_erosion(~outside, iterations=pore, border_value=0)
    return distance_transform_edt(~outside) <= pore           # what closing added around the body is taken back


def walled(solid):
    """For every cell, whether something solid lies on each of its six sides:
    towards lower x, higher x, lower y, higher y, lower z, higher z."""
    sides = []
    for axis in range(3):
        sides.append(np.logical_or.accumulate(solid, axis=axis))
        sides.append(np.flip(np.logical_or.accumulate(np.flip(solid, axis), axis=axis), axis))
    return sides


def body_of(opaque, down=None, parts=False):
    """The solid body the shell stands for (see the top of this file): True
    where a cell belongs to it. `down` is the side the thing stands on, as in
    walled() (3: towards higher y); None: it is not known. With `parts`
    also returns which cells were added as rooms and ways into them.

    A photo shows one side. On the others the Gaussians are thin, and where
    they leave a gap the flood runs into the thing and hollows it out. So a
    room the flood reached is still part of the body when it is walled in on
    all six sides, or on all but the one the thing stands on (nobody saw it
    from below: its underside is closed flat). The tear the flood came in by
    is closed as well, out to where the shell would run. A bowl stays a
    bowl (it is open upwards, and wide), and a hollow in the surface that
    leads into no room stays a hollow."""
    shell = opaque >= 0.5
    if not shell.any():
        raise ValueError("Der Splat ergibt keine Oberfläche")
    walls = np.ones((3, 3, 3), dtype=bool)
    body = ~flooded(shell, PORE) | shell
    torn = ~flooded(shell, max(PORE + 1, int(round(LEAK * max(opaque.shape)))))        # also behind a tear
    wide = ~flooded(shell, max(PORE + 1, int(round(2 * LEAK * max(opaque.shape)))))    # ... and a wide one
    up = None if down is None else down ^ 1
    first = body.copy()
    for _ in range(6):
        sides = walled(body)
        count = np.sum(sides, axis=0, dtype=np.uint8)
        room = count == 6
        if down is not None:
            room |= (count == 5) & ~sides[down]
        room &= ~body
        if not room.any():
            break
        body |= room
    # The way into such a room, out to where the shell would run: it is
    # walled on three sides (on four where the tear is wide), and it leads
    # into the room. (A hollow in the surface that leads nowhere, an eye
    # socket, stays as it is.)
    for _ in range(6):
        rooms = body & ~first
        if not rooms.any():
            break
        sides = walled(body)
        count = np.sum(sides, axis=0, dtype=np.uint8)
        way = ~body & ((torn & (count >= 3)) | (wide & (count >= 4)))
        joined, _ = label(way | rooms)
        leads = np.unique(joined[rooms])
        way &= np.isin(joined, leads[leads > 0])
        if not way.any():
            break
        body |= way
    # Loose bits fall away; what is big enough is a figure of its own.
    pieces, count = label(body, structure=walls)
    if count > 1:
        sizes = np.bincount(pieces.ravel())
        sizes[0] = 0
        body = np.isin(pieces, np.flatnonzero(sizes >= OWN * sizes.max()))
    return (body, body & ~first) if parts else body
