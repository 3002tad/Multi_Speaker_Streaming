from __future__ import annotations

import io
from pathlib import Path
from typing import Protocol

from meeting_service.app.config import settings


class ObjectStorage(Protocol):
    def put(self, key: str, content: bytes, content_type: str) -> None: ...
    def get(self, key: str) -> bytes: ...
    def delete(self, key: str) -> None: ...


class FilesystemObjectStorage:
    """Small local fallback used by tests and offline development."""

    def __init__(self, root: str) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if self.root.resolve() not in path.parents:
            raise ValueError("invalid object key")
        return path

    def put(self, key: str, content: bytes, content_type: str) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def delete(self, key: str) -> None:
        path = self._path(key)
        if path.exists():
            path.unlink()


class MinioObjectStorage:
    def __init__(self) -> None:
        try:
            from minio import Minio
        except ImportError as exc:  # pragma: no cover - exercised by deployment image
            raise RuntimeError("MinIO storage requires the minio package") from exc
        self._client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
        )
        if not self._client.bucket_exists(settings.minio_bucket):
            self._client.make_bucket(settings.minio_bucket)

    def put(self, key: str, content: bytes, content_type: str) -> None:
        self._client.put_object(
            settings.minio_bucket,
            key,
            io.BytesIO(content),
            length=len(content),
            content_type=content_type,
        )

    def get(self, key: str) -> bytes:
        response = self._client.get_object(settings.minio_bucket, key)
        try:
            return response.read()
        finally:
            response.close()
            response.release_conn()

    def delete(self, key: str) -> None:
        self._client.remove_object(settings.minio_bucket, key)


def build_object_storage() -> ObjectStorage:
    if settings.minio_endpoint and settings.minio_access_key and settings.minio_secret_key:
        return MinioObjectStorage()
    return FilesystemObjectStorage(settings.export_root)


object_storage = FilesystemObjectStorage(settings.export_root)
