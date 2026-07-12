"""
Upload benchmark documents to the pm-knowledge-docs Vertex AI Search datastore.
Run from the project root: python knowledge_base/upload_benchmarks.py
"""
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv()

from google.cloud import discoveryengine_v1 as discoveryengine
from google.protobuf import struct_pb2

PROJECT_ID      = os.environ["GCP_PROJECT_ID"]
LOCATION        = os.environ.get("VERTEX_LOCATION", "global")
DATASTORE_ID    = "pm-knowledge-docs"   # PDF / structured doc store

DOCS_DIR = Path(__file__).parent

def dict_to_struct(d: dict) -> struct_pb2.Struct:
    s = struct_pb2.Struct()
    s.update(d)
    return s

def upload_documents():
    client_options = None
    if LOCATION and LOCATION != "global":
        from google.api_core.client_options import ClientOptions
        client_options = ClientOptions(api_endpoint=f"{LOCATION}-discoveryengine.googleapis.com")

    client = discoveryengine.DocumentServiceClient(client_options=client_options)

    parent = (
        f"projects/{PROJECT_ID}/locations/{LOCATION}"
        f"/collections/default_collection/dataStores/{DATASTORE_ID}/branches/default_branch"
    )

    # Load all JSON benchmark files (exclude this script)
    doc_files = sorted(DOCS_DIR.glob("*.json"))
    print(f"Found {len(doc_files)} document(s) to upload:\n")

    success, failed = 0, 0

    for fpath in doc_files:
        with open(fpath, encoding="utf-8") as f:
            data = json.load(f)

        doc_id    = data.get("id") or fpath.stem
        title     = data.get("title", doc_id)
        struct_d  = dict_to_struct(data)

        # Content datastores require raw_bytes — pass the full text content
        text_content = data.get("content", json.dumps(data, indent=2))
        document = discoveryengine.Document(
            id=doc_id,
            struct_data=struct_d,
            content=discoveryengine.Document.Content(
                raw_bytes=text_content.encode("utf-8"),
                mime_type="text/plain",
            ),
        )

        name = f"{parent}/documents/{doc_id}"

        # Try update first, fall back to create
        try:
            client.get_document(name=name)
            doc_with_name = discoveryengine.Document(
                name=name,
                id=doc_id,
                struct_data=struct_d,
                content=discoveryengine.Document.Content(
                    raw_bytes=text_content.encode("utf-8"),
                    mime_type="text/plain",
                ),
            )
            client.update_document(document=doc_with_name)
            print(f"  [UPDATED]  [{doc_id}]")
            print(f"             {title}")
            success += 1
        except Exception as e:
            if "NOT_FOUND" in str(e) or "404" in str(e):
                try:
                    client.create_document(parent=parent, document=document, document_id=doc_id)
                    print(f"  [CREATED]  [{doc_id}]")
                    print(f"             {title}")
                    success += 1
                except Exception as create_err:
                    print(f"  [FAILED]   [{doc_id}] -- {create_err}")
                    failed += 1
            else:
                print(f"  [FAILED]   [{doc_id}] -- {e}")
                failed += 1

        time.sleep(0.3)   # gentle rate-limiting

    print(f"\nDone — {success} uploaded, {failed} failed.")


if __name__ == "__main__":
    upload_documents()
