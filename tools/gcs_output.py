"""
GCS-backed per-user output file storage.

Files are stored at:  gs://OUTPUT_BUCKET/{username}/{filename}

Each user can only list and download their own files.
"""

import os
from pathlib import Path
from google.cloud import storage

OUTPUT_BUCKET = os.environ.get("OUTPUT_BUCKET", "pm-agent-outputs")
ICON_MAP = {".docx": "word", ".xlsx": "excel", ".pptx": "powerpoint"}


def _client():
    return storage.Client()


def upload_file(username: str, local_path: Path) -> str:
    """Upload a local file to GCS under the user's prefix. Returns the GCS blob name."""
    client = _client()
    bucket = client.bucket(OUTPUT_BUCKET)
    blob_name = f"{username}/{local_path.name}"
    blob = bucket.blob(blob_name)
    blob.upload_from_filename(str(local_path))
    return blob_name


def list_files(username: str) -> list:
    """List all output files for a user, newest first."""
    client = _client()
    bucket = client.bucket(OUTPUT_BUCKET)
    prefix = f"{username}/"
    blobs = list(bucket.list_blobs(prefix=prefix))
    files = []
    for blob in blobs:
        filename = blob.name[len(prefix):]   # strip the user prefix
        if not filename:
            continue
        suffix = Path(filename).suffix.lower()
        if suffix in ICON_MAP:
            files.append({
                "filename": filename,
                "doc_type": ICON_MAP[suffix],
                "size_kb": round((blob.size or 0) / 1024, 1),
                "modified": blob.updated.timestamp() if blob.updated else 0,
            })
    files.sort(key=lambda x: x["modified"], reverse=True)
    return files


def download_bytes(username: str, filename: str) -> bytes:
    """Download a file from GCS for the given user and return raw bytes."""
    client = _client()
    bucket = client.bucket(OUTPUT_BUCKET)
    blob = bucket.blob(f"{username}/{filename}")
    return blob.download_as_bytes()


def file_exists(username: str, filename: str) -> bool:
    """Return True if the file exists in GCS for this user."""
    client = _client()
    bucket = client.bucket(OUTPUT_BUCKET)
    return bucket.blob(f"{username}/{filename}").exists()


def delete_all_files(username: str) -> int:
    """Delete all output files for a user from GCS. Returns count deleted."""
    client = _client()
    bucket = client.bucket(OUTPUT_BUCKET)
    blobs = list(bucket.list_blobs(prefix=f"{username}/"))
    for blob in blobs:
        blob.delete()
    return len(blobs)


def delete_file(username: str, filename: str) -> bool:
    """Delete a single file for a user from GCS. Returns True if deleted."""
    client = _client()
    bucket = client.bucket(OUTPUT_BUCKET)
    blob = bucket.blob(f"{username}/{filename}")
    if blob.exists():
        blob.delete()
        return True
    return False
