from __future__ import annotations

import math
import struct
from collections.abc import Iterable, Sequence


def normalize(vector: Sequence[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        return [0.0 for _ in vector]
    return [float(value) / norm for value in vector]


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    left_n = normalize(left)
    right_n = normalize(right)
    return sum(a * b for a, b in zip(left_n, right_n, strict=True))


def encode_vector(vector: Sequence[float]) -> bytes:
    return struct.pack(f"<{len(vector)}f", *[float(value) for value in vector])


def decode_vector(blob: bytes) -> list[float]:
    if not blob:
        return []
    count = len(blob) // 4
    return list(struct.unpack(f"<{count}f", blob))


def average(vectors: Iterable[Sequence[float]]) -> list[float]:
    vectors = [list(vector) for vector in vectors]
    if not vectors:
        return []
    size = len(vectors[0])
    out = [0.0] * size
    for vector in vectors:
        for idx, value in enumerate(vector):
            out[idx] += value
    return [value / len(vectors) for value in out]
