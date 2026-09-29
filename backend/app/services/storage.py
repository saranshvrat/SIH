"""
Object storage with two interchangeable drivers (ADR-003).

`s3`    — MinIO / AWS S3 / Cloudflare R2 via boto3, presigned URLs.
`local` — a filesystem tree, with HMAC-signed URLs served back through FastAPI.

The key structure (services/keys.py) is identical for both. Nothing outside this
module knows which driver is active.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import shutil
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import BinaryIO, Iterable

from app.core.config import settings
from app.core.errors import NotFoundError, ServiceUnavailableError
from app.core.logging import get_logger

log = get_logger(__name__)

SCENES, JOBS, REPORTS = "scenes", "jobs", "reports"


def _bucket_name(bucket: str) -> str:
    return {SCENES: settings.S3_BUCKET_SCENES,
            JOBS: settings.S3_BUCKET_JOBS,
            REPORTS: settings.S3_BUCKET_REPORTS}[bucket]


class StorageDriver(ABC):
    @abstractmethod
    def put_file(self, bucket: str, key: str, path: Path, content_type: str | None = None) -> int: ...
    @abstractmethod
    def put_bytes(self, bucket: str, key: str, data: bytes, content_type: str | None = None) -> int: ...
    @abstractmethod
    def get_to_path(self, bucket: str, key: str, dest: Path) -> Path: ...
    @abstractmethod
    def get_bytes(self, bucket: str, key: str) -> bytes: ...
    @abstractmethod
    def exists(self, bucket: str, key: str) -> bool: ...
    @abstractmethod
    def delete(self, bucket: str, key: str) -> None: ...
    @abstractmethod
    def delete_prefix(self, bucket: str, prefix: str) -> int: ...
    @abstractmethod
    def list_prefix(self, bucket: str, prefix: str) -> Iterable[str]: ...
    @abstractmethod
    def presign_get(self, bucket: str, key: str, ttl: int, filename: str | None = None) -> str: ...
    @abstractmethod
    def stat(self, bucket: str, key: str) -> dict: ...
    @abstractmethod
    def healthcheck(self) -> dict: ...


class LocalDriver(StorageDriver):
    def __init__(self, root: str):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, bucket: str, key: str) -> Path:
        p = (self.root / _bucket_name(bucket) / key).resolve()
        # Defence in depth: keys.py already guarantees safety, assert it here too.
        if not p.is_relative_to(self.root):
            raise ValueError(f"key escapes storage root: {key!r}")
        return p

    def put_file(self, bucket, key, path, content_type=None) -> int:
        dest = self._path(bucket, key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, dest)
        return dest.stat().st_size

    def put_bytes(self, bucket, key, data, content_type=None) -> int:
        dest = self._path(bucket, key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return len(data)

    def get_to_path(self, bucket, key, dest) -> Path:
        src = self._path(bucket, key)
        if not src.exists():
            raise NotFoundError(f"Object not found: {bucket}/{key}")
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        return Path(dest)

    def get_bytes(self, bucket, key) -> bytes:
        src = self._path(bucket, key)
        if not src.exists():
            raise NotFoundError(f"Object not found: {bucket}/{key}")
        return src.read_bytes()

    def exists(self, bucket, key) -> bool:
        return self._path(bucket, key).exists()

    def delete(self, bucket, key) -> None:
        self._path(bucket, key).unlink(missing_ok=True)

    def delete_prefix(self, bucket, prefix) -> int:
        base = self.root / _bucket_name(bucket) / prefix
        if not base.exists():
            return 0
        n = sum(1 for _ in base.rglob("*") if _.is_file())
        shutil.rmtree(base, ignore_errors=True)
        return n

    def list_prefix(self, bucket, prefix):
        base = self.root / _bucket_name(bucket)
        target = base / prefix
        if not target.exists():
            return []
        return [str(p.relative_to(base)) for p in target.rglob("*") if p.is_file()]

    def presign_get(self, bucket, key, ttl, filename=None) -> str:
        exp = int(time.time()) + ttl
        msg = f"{bucket}:{key}:{exp}".encode()
        sig = hmac.new(settings.SECRET_KEY.encode(), msg, hashlib.sha256).hexdigest()[:32]
        q = f"?bucket={bucket}&key={key}&exp={exp}&sig={sig}"
        if filename:
            q += f"&filename={filename}"
        return f"{settings.API_V1_PREFIX}/storage/download{q}"

    @staticmethod
    def verify_signature(bucket: str, key: str, exp: int, sig: str) -> bool:
        if exp < int(time.time()):
            return False
        msg = f"{bucket}:{key}:{exp}".encode()
        want = hmac.new(settings.SECRET_KEY.encode(), msg, hashlib.sha256).hexdigest()[:32]
        return hmac.compare_digest(want, sig)

    def stat(self, bucket, key) -> dict:
        p = self._path(bucket, key)
        if not p.exists():
            raise NotFoundError(f"Object not found: {bucket}/{key}")
        st = p.stat()
        return {"bytes": st.st_size, "last_modified": st.st_mtime}

    def healthcheck(self) -> dict:
        try:
            probe = self.root / ".healthcheck"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            return {"ok": True, "driver": "local", "root": str(self.root)}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "driver": "local", "error": str(exc)}


class S3Driver(StorageDriver):
    def __init__(self):
        import boto3
        from botocore.client import Config
        self._client = boto3.client(
            "s3", endpoint_url=settings.S3_ENDPOINT_URL,
            aws_access_key_id=settings.S3_ACCESS_KEY,
            aws_secret_access_key=settings.S3_SECRET_KEY,
            region_name=settings.S3_REGION, config=Config(signature_version="s3v4"))

    def ensure_buckets(self) -> None:
        for b in (SCENES, JOBS, REPORTS):
            name = _bucket_name(b)
            try:
                self._client.head_bucket(Bucket=name)
            except Exception:
                try:
                    self._client.create_bucket(Bucket=name)
                except Exception as exc:  # noqa: BLE001
                    log.warning("bucket_create_failed", bucket=name, error=str(exc))

    def put_file(self, bucket, key, path, content_type=None) -> int:
        extra = {"ContentType": content_type} if content_type else {}
        self._client.upload_file(str(path), _bucket_name(bucket), key, ExtraArgs=extra or None)
        return Path(path).stat().st_size

    def put_bytes(self, bucket, key, data, content_type=None) -> int:
        kw = {"ContentType": content_type} if content_type else {}
        self._client.put_object(Bucket=_bucket_name(bucket), Key=key, Body=data, **kw)
        return len(data)

    def get_to_path(self, bucket, key, dest) -> Path:
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        try:
            self._client.download_file(_bucket_name(bucket), key, str(dest))
        except Exception as exc:  # noqa: BLE001
            raise NotFoundError(f"Object not found: {bucket}/{key}") from exc
        return Path(dest)

    def get_bytes(self, bucket, key) -> bytes:
        try:
            return self._client.get_object(Bucket=_bucket_name(bucket), Key=key)["Body"].read()
        except Exception as exc:  # noqa: BLE001
            raise NotFoundError(f"Object not found: {bucket}/{key}") from exc

    def exists(self, bucket, key) -> bool:
        try:
            self._client.head_object(Bucket=_bucket_name(bucket), Key=key)
            return True
        except Exception:
            return False

    def delete(self, bucket, key) -> None:
        self._client.delete_object(Bucket=_bucket_name(bucket), Key=key)

    def delete_prefix(self, bucket, prefix) -> int:
        keys = list(self.list_prefix(bucket, prefix))
        for i in range(0, len(keys), 1000):
            chunk = [{"Key": k} for k in keys[i:i + 1000]]
            self._client.delete_objects(Bucket=_bucket_name(bucket), Delete={"Objects": chunk})
        return len(keys)

    def list_prefix(self, bucket, prefix):
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=_bucket_name(bucket), Prefix=prefix):
            for obj in page.get("Contents", []):
                yield obj["Key"]

    def presign_get(self, bucket, key, ttl, filename=None) -> str:
        params = {"Bucket": _bucket_name(bucket), "Key": key}
        if filename:
            params["ResponseContentDisposition"] = f'attachment; filename="{filename}"'
        return self._client.generate_presigned_url("get_object", Params=params, ExpiresIn=ttl)

    def stat(self, bucket, key) -> dict:
        try:
            h = self._client.head_object(Bucket=_bucket_name(bucket), Key=key)
            return {"bytes": h["ContentLength"], "last_modified": h["LastModified"].timestamp()}
        except Exception as exc:  # noqa: BLE001
            raise NotFoundError(f"Object not found: {bucket}/{key}") from exc

    def healthcheck(self) -> dict:
        try:
            self._client.list_buckets()
            return {"ok": True, "driver": "s3", "endpoint": settings.S3_ENDPOINT_URL}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "driver": "s3", "error": str(exc)}


class StorageService:
    def __init__(self, driver: StorageDriver | None = None):
        self.driver = driver or _make_driver()

    def __getattr__(self, name):
        return getattr(self.driver, name)

    def put_json(self, bucket: str, key: str, obj: dict) -> int:
        return self.driver.put_bytes(bucket, key,
                                     json.dumps(obj, indent=2, default=str).encode(),
                                     "application/json")

    def get_json(self, bucket: str, key: str) -> dict:
        return json.loads(self.driver.get_bytes(bucket, key))

    @staticmethod
    def checksum(path: Path) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()


def _make_driver() -> StorageDriver:
    if settings.STORAGE_DRIVER == "s3":
        try:
            d = S3Driver()
            d.ensure_buckets()
            return d
        except Exception as exc:  # noqa: BLE001
            raise ServiceUnavailableError(f"Object storage unavailable: {exc}") from exc
    return LocalDriver(settings.STORAGE_LOCAL_ROOT)


_storage: StorageService | None = None


def get_storage() -> StorageService:
    global _storage
    if _storage is None:
        _storage = StorageService()
    return _storage
