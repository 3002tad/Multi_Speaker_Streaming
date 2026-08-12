"""Qdrant boundary for voice-profile persistence.

The store holds only Meeting AI speaker embeddings; it never reaches into an
eCabinet database. Scoring and acceptance policy remain in ``core``.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import numpy as np
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams


class QdrantSpeakerStore:
    collection_name = "speakers"

    def __init__(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        self._client = QdrantClient(path=str(path))
        if not self._client.collection_exists(collection_name=self.collection_name):
            self._client.create_collection(
                collection_name=self.collection_name,
                vectors_config=VectorParams(size=512, distance=Distance.COSINE),
            )

    @staticmethod
    def _label(point: object) -> str:
        return str((getattr(point, "payload", None) or {}).get("speaker_label", "")).strip()

    def all_points(self, *, with_vectors: bool = False) -> list[object]:
        points: list[object] = []
        offset = None
        while True:
            page, offset = self._client.scroll(
                collection_name=self.collection_name,
                limit=256,
                offset=offset,
                with_payload=True,
                with_vectors=with_vectors,
            )
            points.extend(page)
            if offset is None:
                return points

    def count(self) -> int:
        return int(self._client.count(collection_name=self.collection_name, exact=True).count)

    def close(self) -> None:
        """Close local Qdrant before interpreter teardown releases its locks."""
        self._client.close()

    def delete_profile(
        self,
        speaker_name: str | None = None,
        *,
        profile_key: str | None = None,
        user_id: str | None = None,
    ) -> None:
        normalized = (speaker_name or "").strip().casefold()
        point_ids = [
            point.id
            for point in self.all_points()
            if (
                (profile_key and str((point.payload or {}).get("profile_key", "")) == profile_key)
                or (user_id and str((point.payload or {}).get("speaker_user_id", "")) == user_id)
                or (speaker_name and self._label(point).casefold() == normalized)
            )
        ]
        if point_ids:
            self._client.delete(
                collection_name=self.collection_name,
                points_selector=point_ids,
                wait=True,
            )

    def query_scores(self, embedding: np.ndarray) -> dict[str, float]:
        result = self._client.query_points(
            collection_name=self.collection_name,
            query=embedding.tolist(),
            limit=64,
        )
        scores: dict[str, float] = {}
        for point in result.points:
            label = self._label(point)
            if label:
                scores[label] = max(scores.get(label, -1.0), float(point.score))
        return scores

    def upsert_profile(
        self,
        *,
        speaker_name: str,
        profile_key: str,
        user_id: str | None,
        prototypes: tuple[np.ndarray, ...],
    ) -> None:
        points = []
        for index, prototype in enumerate(prototypes):
            points.append(
                PointStruct(
                    id=str(uuid.uuid5(uuid.NAMESPACE_DNS, f"speaker-profile-v2:{profile_key}:{index}")),
                    vector=prototype.tolist(),
                    payload={
                        "speaker_label": speaker_name,
                        "profile_key": profile_key,
                        "speaker_user_id": user_id,
                        "profile_version": 2,
                        "prototype_index": index,
                        "prototype_kind": "centroid" if index == 0 else "sample",
                    },
                )
            )
        self._client.upsert(self.collection_name, points=points, wait=True)
