"""
Per-user Vertex AI Search datastores for project document RAG.

Each user gets a dedicated datastore with ID: pm-user-{sanitized_username}
Uploaded files are stored in GCS at:
  gs://pm-agent-knowledge-docs/user-uploads/{username}/{filename}

Workflow:
  1. User uploads a file via the web UI
  2. File is stored in GCS under the user's prefix
  3. A personal Vertex AI Search datastore is created if it doesn't exist yet
  4. The GCS file is imported into that datastore (async — takes a few minutes)
  5. vertex_search() optionally queries the user's datastore alongside the global ones
"""

import re
from pathlib import Path
from google.cloud import discoveryengine_v1 as discoveryengine
from google.api_core.exceptions import AlreadyExists, NotFound
from config import GCP_PROJECT_ID, VERTEX_LOCATION

UPLOAD_BUCKET = "pm-agent-knowledge-docs"
UPLOAD_PREFIX = "user-uploads"

ALLOWED_EXTENSIONS = {
    ".pdf":  "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".txt":  "text/plain",
}

MAX_FILE_SIZE_MB = 50


# ── Datastore naming ──────────────────────────────────────────────────────────

def _sanitize(username: str) -> str:
    """Convert a username to a valid Vertex AI datastore ID segment."""
    s = re.sub(r"[^a-z0-9]+", "-", username.lower()).strip("-")
    if not s or not s[0].isalpha():
        s = "u-" + s
    return s[:40]


def datastore_id(username: str) -> str:
    return f"pm-user-{_sanitize(username)}"


def _ds_parent() -> str:
    return (
        f"projects/{GCP_PROJECT_ID}/locations/{VERTEX_LOCATION}"
        "/collections/default_collection"
    )


def _ds_name(username: str) -> str:
    return f"{_ds_parent()}/dataStores/{datastore_id(username)}"


def _branch(username: str) -> str:
    return f"{_ds_name(username)}/branches/default_branch"


# ── GCS helpers ───────────────────────────────────────────────────────────────

def _gcs_client():
    from tools.gcs_output import _client
    return _client()


def _gcs_uri(username: str, filename: str) -> str:
    return f"gs://{UPLOAD_BUCKET}/{UPLOAD_PREFIX}/{username}/{filename}"


# ── Datastore lifecycle ───────────────────────────────────────────────────────

def get_or_create_datastore(username: str) -> dict:
    """
    Ensure a personal Vertex AI Search datastore exists for this user.
    Returns {"status": "exists"|"creating", "datastore_id": str}.
    Datastore creation is async and takes ~15 minutes to become searchable.
    """
    client = discoveryengine.DataStoreServiceClient()
    name = _ds_name(username)

    try:
        client.get_data_store(name=name)
        return {"status": "exists", "datastore_id": datastore_id(username)}
    except NotFound:
        pass

    try:
        ds = discoveryengine.DataStore(
            display_name=f"Project Docs — {username}",
            industry_vertical=discoveryengine.IndustryVertical.GENERIC,
            content_config=discoveryengine.DataStore.ContentConfig.CONTENT_REQUIRED,
        )
        op = client.create_data_store(
            parent=_ds_parent(),
            data_store=ds,
            data_store_id=datastore_id(username),
        )
        return {
            "status": "creating",
            "datastore_id": datastore_id(username),
            "operation": op.operation.name,
        }
    except AlreadyExists:
        return {"status": "exists", "datastore_id": datastore_id(username)}


def datastore_ready(username: str) -> bool:
    """Return True if the user's datastore exists and is ready."""
    try:
        discoveryengine.DataStoreServiceClient().get_data_store(name=_ds_name(username))
        return True
    except NotFound:
        return False
    except Exception:
        return False


# ── Document upload + import ──────────────────────────────────────────────────

def upload_and_import(username: str, file_bytes: bytes, filename: str) -> dict:
    """
    Upload a file to GCS then import it into the user's Vertex AI datastore.
    Returns a status dict immediately — indexing is async (2–5 min).
    """
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise ValueError(
            f"Unsupported file type '{suffix}'. "
            f"Allowed: {', '.join(ALLOWED_EXTENSIONS)}"
        )
    if len(file_bytes) > MAX_FILE_SIZE_MB * 1024 * 1024:
        raise ValueError(f"File exceeds {MAX_FILE_SIZE_MB} MB limit.")

    # 1. Upload to GCS
    gcs = _gcs_client()
    bucket = gcs.bucket(UPLOAD_BUCKET)
    blob = bucket.blob(f"{UPLOAD_PREFIX}/{username}/{filename}")
    blob.upload_from_string(file_bytes, content_type=ALLOWED_EXTENSIONS[suffix])
    gcs_uri = _gcs_uri(username, filename)

    # 2. Ensure datastore exists (fire-and-forget if creating)
    ds_status = get_or_create_datastore(username)

    # 3. Import document into Vertex AI Search
    doc_client = discoveryengine.DocumentServiceClient()
    request = discoveryengine.ImportDocumentsRequest(
        parent=_branch(username),
        gcs_source=discoveryengine.GcsSource(
            input_uris=[gcs_uri],
            data_schema="content",
        ),
        reconciliation_mode=(
            discoveryengine.ImportDocumentsRequest.ReconciliationMode.INCREMENTAL
        ),
    )
    doc_client.import_documents(request=request)  # async — don't wait

    return {
        "status": "importing",
        "filename": filename,
        "gcs_uri": gcs_uri,
        "datastore_id": ds_status["datastore_id"],
        "datastore_status": ds_status["status"],
    }


# ── Document listing ──────────────────────────────────────────────────────────

def list_user_documents(username: str) -> list:
    """
    List project documents uploaded by this user, sourced from GCS
    (always up-to-date regardless of datastore indexing status).
    """
    gcs = _gcs_client()
    bucket = gcs.bucket(UPLOAD_BUCKET)
    prefix = f"{UPLOAD_PREFIX}/{username}/"
    blobs = list(bucket.list_blobs(prefix=prefix))

    files = []
    for blob in blobs:
        filename = blob.name[len(prefix):]
        if not filename or "/" in filename:
            continue
        suffix = Path(filename).suffix.lower()
        if suffix in ALLOWED_EXTENSIONS:
            files.append({
                "filename": filename,
                "size_kb": round((blob.size or 0) / 1024, 1),
                "modified": blob.updated.timestamp() if blob.updated else 0,
            })
    files.sort(key=lambda x: x["modified"], reverse=True)
    return files


# ── Document deletion ─────────────────────────────────────────────────────────

def delete_user_document(username: str, filename: str) -> bool:
    """
    Delete a document from GCS and from the user's Vertex AI datastore.
    Returns True if found and deleted.
    """
    gcs = _gcs_client()
    bucket = gcs.bucket(UPLOAD_BUCKET)
    blob = bucket.blob(f"{UPLOAD_PREFIX}/{username}/{filename}")
    if not blob.exists():
        return False
    blob.delete()

    # Also remove from the Vertex datastore
    target_uri = _gcs_uri(username, filename)
    try:
        doc_client = discoveryengine.DocumentServiceClient()
        for doc in doc_client.list_documents(parent=_branch(username)):
            if doc.content and doc.content.uri == target_uri:
                doc_client.delete_document(name=doc.name)
                break
    except Exception:
        pass  # datastore may not exist yet — GCS deletion is the source of truth

    return True


# ── Search ────────────────────────────────────────────────────────────────────

def search_user_datastore(username: str, query: str, num_results: int = 5) -> list:
    """
    Search the user's personal Vertex AI datastore.
    Returns list of result dicts; silently returns [] if datastore isn't ready.
    """
    if not datastore_ready(username):
        return []

    try:
        client = discoveryengine.SearchServiceClient()
        serving_config = (
            f"{_ds_name(username)}/servingConfigs/default_search"
        )
        request = discoveryengine.SearchRequest(
            serving_config=serving_config,
            query=query,
            page_size=min(num_results, 10),
            query_expansion_spec=discoveryengine.SearchRequest.QueryExpansionSpec(
                condition=discoveryengine.SearchRequest.QueryExpansionSpec.Condition.AUTO,
            ),
            content_search_spec=discoveryengine.SearchRequest.ContentSearchSpec(
                snippet_spec=discoveryengine.SearchRequest.ContentSearchSpec.SnippetSpec(
                    return_snippet=True,
                    max_snippet_count=3,
                ),
                extractive_content_spec=(
                    discoveryengine.SearchRequest.ContentSearchSpec.ExtractiveContentSpec(
                        max_extractive_answer_count=2,
                        max_extractive_segment_count=3,
                    )
                ),
            ),
        )
        response = client.search(request)
        results = []
        for result in response.results:
            doc = result.document
            doc_data = {
                "id": doc.id,
                "datastore": datastore_id(username),
                "source": "user_project_docs",
            }
            if doc.derived_struct_data:
                derived = dict(doc.derived_struct_data)
                for key in ("snippets", "extractive_answers", "extractive_segments",
                            "link", "title"):
                    if key in derived:
                        doc_data[key] = str(derived[key])
            results.append(doc_data)
        return results
    except Exception:
        return []
