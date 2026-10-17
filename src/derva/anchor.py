"""Read and write audit chain anchors: a local file, or a GCS prefix.

An anchor is the chain head (sequence number and hash) copied somewhere the
database admin cannot change it. In staging that is a GCS bucket with a
retention policy: objects cannot be deleted or overwritten until they expire,
so a truncated or rewritten chain no longer matches the newest anchor.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _split_gs(uri: str) -> tuple[str, str]:
    bucket, _, prefix = uri.removeprefix("gs://").partition("/")
    return bucket, prefix.strip("/")


def write_anchor(head: dict[str, Any], target: str) -> str:
    """Write ``head`` to a file path, or as a new object under a ``gs://bucket/prefix``."""
    body = json.dumps(head, indent=2, sort_keys=True)
    if not target.startswith("gs://"):
        Path(target).write_text(body)
        return target
    from google.cloud import storage  # type: ignore[import-untyped, unused-ignore]

    bucket_name, prefix = _split_gs(target)
    # Zero-padded sequence first, so names sort in chain order.
    name = f"{prefix}/{head['seq']:012d}-{head['anchored_at']}.json".lstrip("/")
    blob = storage.Client().bucket(bucket_name).blob(name)
    blob.upload_from_string(body, content_type="application/json", if_generation_match=0)
    return f"gs://{bucket_name}/{name}"


def read_anchor(source: str) -> dict[str, Any]:
    """Read an anchor file, or the newest anchor under a ``gs://bucket/prefix``."""
    if not source.startswith("gs://"):
        return dict(json.loads(Path(source).read_text()))
    from google.cloud import storage  # type: ignore[import-untyped, unused-ignore]

    bucket_name, prefix = _split_gs(source)
    blobs = sorted(storage.Client().list_blobs(bucket_name, prefix=prefix), key=lambda b: b.name)
    if not blobs:
        raise FileNotFoundError(f"No anchors under {source}")
    return dict(json.loads(blobs[-1].download_as_text()))
