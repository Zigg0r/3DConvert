"""Polygon triangulation shared by the readers (needs mapbox_earcut, one of the bundled libraries)."""
import mapbox_earcut as earcut
import numpy as np


def newell_normal(ring):
    """Normal of a 3D polygon (also a concave one); its length is twice the area."""
    nxt = np.roll(ring, -1, 0)
    return np.array([((ring[:, 1] - nxt[:, 1]) * (ring[:, 2] + nxt[:, 2])).sum(),
                     ((ring[:, 2] - nxt[:, 2]) * (ring[:, 0] + nxt[:, 0])).sum(),
                     ((ring[:, 0] - nxt[:, 0]) * (ring[:, 1] + nxt[:, 1])).sum()])


def triangulate_rings(rings):
    """Ear clipping of a planar polygon: rings[0] is the outline, any others are holes, each an (n, 3) array.
    Returns (m, 3) triangles that index the rings stacked one after the other."""
    points = np.concatenate(rings)
    drop = int(np.argmax(np.abs(newell_normal(rings[0]))))
    flat = points[:, [k for k in range(3) if k != drop]]
    ends = np.cumsum([len(r) for r in rings]).astype(np.uint32)
    return np.asarray(earcut.triangulate_float64(flat, ends), np.int64).reshape(-1, 3)
