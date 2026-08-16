"""Read the placed-entity documents that come with tiles and prefabs.

Every ``.tile`` has a ``.tileson`` beside it and every ``.prefab`` a
``.prefabson``, both plain JSON with the same shape: lists of assets,
harvestables and kinematics, each with a position, an Euler rotation and a
scale -- plus a list of nested prefabs, which is where a point of interest keeps
most of its bulk. The ruined city is 124 nested prefabs and a warehouse is one,
so a reader that stops at the top level draws the fences and misses the
buildings.

Everything is flattened into one list of placements in the document's own frame.
Rotation and scale travel as a 3x3 matrix rather than as angles, because
composing a child's rotation with a parent's non-uniform scale does not in
general give back a rotation and a scale -- and the water volumes, which are
unit boxes stretched 64 x 16 x 64, are exactly that case.
"""

import json
import os

import numpy as np

from . import discover
from .assets import euler_matrices

_KINDS = ("assets", "harvestables", "kinematics")


def _matrices(rot, scale):
    """R * diag(s) for (N, 3) Euler angles and (N, 3) scales."""
    return euler_matrices(rot) * np.asarray(scale, dtype=np.float32)[:, None, :]


class Placements(object):
    """Props in one frame: uuids, positions, rotate-and-scale matrices, tints."""

    __slots__ = ("uuid", "pos", "mat", "tint")

    def __init__(self, uuid, pos, mat, tint):
        self.uuid = uuid
        self.pos = pos
        self.mat = mat
        self.tint = tint

    def __len__(self):
        return len(self.uuid)

    def placed_at(self, pos, mat):
        """This document's props as seen from a parent that holds it at pos/mat."""
        return Placements(self.uuid,
                          pos + self.pos @ mat.T,
                          np.einsum("ij,njk->nik", mat, self.mat),
                          self.tint)


EMPTY = Placements([], np.zeros((0, 3), np.float32),
                   np.zeros((0, 3, 3), np.float32), [])


def _join(parts):
    parts = [p for p in parts if len(p)]
    if not parts:
        return EMPTY
    if len(parts) == 1:
        return parts[0]
    return Placements([u for p in parts for u in p.uuid],
                      np.concatenate([p.pos for p in parts]),
                      np.concatenate([p.mat for p in parts]),
                      [t for p in parts for t in p.tint])


class PropLoader(object):
    def __init__(self, game_dir):
        self.game_dir = game_dir
        self._cache = {}
        self._busy = set()

    def _resolve(self, path):
        p = (path.replace("$SURVIVAL_DATA", os.path.join(self.game_dir, "Survival"))
                 .replace("$GAME_DATA", os.path.join(self.game_dir, "Data"))
                 .replace("$CHALLENGE_DATA", os.path.join(self.game_dir, "ChallengeData"))
                 .replace("/", os.sep))
        return discover.resolve(os.path.splitext(p)[0] + ".prefabson")

    def expand(self, path):
        """Every prop in a .tileson or .prefabson, nested prefabs included."""
        key = os.path.normcase(path)
        hit = self._cache.get(key)
        if hit is not None:
            return hit
        if key in self._busy:
            return EMPTY               # a prefab that reaches itself
        self._busy.add(key)
        try:
            out = self._read(path)
        finally:
            self._busy.discard(key)
        self._cache[key] = out
        return out

    def _read(self, path):
        try:
            with open(path, "rb") as f:
                ents = json.load(f).get("entities") or {}
        except (OSError, ValueError):
            return EMPTY

        uuid, pos, rot, scale, tint = [], [], [], [], []
        for kind in _KINDS:
            for e in ents.get(kind) or ():
                t = self._transform(e)
                if t is None:
                    continue
                uuid.append(e["uuid"])
                pos.append(t[0])
                rot.append(t[1])
                scale.append(t[2])
                tint.append(e.get("colorMap"))

        parts = []
        if uuid:
            parts.append(Placements(uuid, np.array(pos, dtype=np.float32),
                                    _matrices(rot, scale), tint))

        for e in ents.get("prefabs") or ():
            t = self._transform(e, need_uuid=False)
            if t is None or not e.get("path"):
                continue
            child = self.expand(self._resolve(e["path"]))
            if not len(child):
                continue
            parts.append(child.placed_at(np.asarray(t[0], dtype=np.float32),
                                         _matrices([t[1]], [t[2]])[0]))
        return _join(parts)

    @staticmethod
    def _transform(e, need_uuid=True):
        if e.get("hidden") or e.get("exclude"):
            return None
        if need_uuid and "uuid" not in e:
            return None
        t = e.get("transform") or {}
        p, r = t.get("position"), t.get("rotation")
        if not p or not r:
            return None
        return p, r, t.get("scale") or (1.0, 1.0, 1.0)
