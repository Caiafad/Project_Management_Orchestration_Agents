import json
from google.cloud import discoveryengine_v1 as discoveryengine
from google.api_core.client_options import ClientOptions
from config import GCP_PROJECT_ID, VERTEX_DATASTORE_ID, VERTEX_LOCATION

# Second datastore: PDF documents (rate guides, salary benchmarks, industry reports)
VERTEX_DOCS_DATASTORE_ID = "pm-knowledge-docs"


def _search_one(client, project, location, datastore_id, query, num_results):
    """Run a search against a single datastore and return a list of result dicts."""
    serving_config = (
        f"projects/{project}"
        f"/locations/{location}"
        f"/collections/default_collection"
        f"/dataStores/{datastore_id}"
        f"/servingConfigs/default_search"
    )
    request = discoveryengine.SearchRequest(
        serving_config=serving_config,
        query=query,
        page_size=min(num_results, 10),
        query_expansion_spec=discoveryengine.SearchRequest.QueryExpansionSpec(
            condition=discoveryengine.SearchRequest.QueryExpansionSpec.Condition.AUTO,
        ),
        spell_correction_spec=discoveryengine.SearchRequest.SpellCorrectionSpec(
            mode=discoveryengine.SearchRequest.SpellCorrectionSpec.Mode.AUTO,
        ),
        content_search_spec=discoveryengine.SearchRequest.ContentSearchSpec(
            snippet_spec=discoveryengine.SearchRequest.ContentSearchSpec.SnippetSpec(
                return_snippet=True,
                max_snippet_count=3,
            ),
            # extractive_answers and extractive_segments are enterprise-only features.
            # Omitting ExtractiveContentSpec keeps this compatible with standard tier.
        ),
    )
    response = client.search(request)
    results = []
    for result in response.results:
        doc = result.document
        doc_data = {"id": doc.id, "name": doc.name, "datastore": datastore_id}
        if doc.struct_data:
            doc_data["data"] = dict(doc.struct_data)
        if doc.derived_struct_data:
            derived = dict(doc.derived_struct_data)
            if "snippets" in derived:
                doc_data["snippets"] = [
                    dict(s) if hasattr(s, "__iter__") else str(s)
                    for s in derived["snippets"]
                ]
            if "extractive_answers" in derived:
                doc_data["extractive_answers"] = [
                    dict(a) if hasattr(a, "__iter__") else str(a)
                    for a in derived["extractive_answers"]
                ]
            if "extractive_segments" in derived:
                doc_data["extractive_segments"] = [
                    dict(s) if hasattr(s, "__iter__") else str(s)
                    for s in derived["extractive_segments"]
                ]
            if "link" in derived:
                doc_data["link"] = str(derived["link"])
            if "title" in derived:
                doc_data["title"] = str(derived["title"])
        results.append(doc_data)
    return results


def vertex_search(query: str, num_results: int = 5, username: str = None) -> str:
    """Search the global knowledge bases and (optionally) the user's personal datastore.
    Returns combined results — global results first, then user project doc results."""
    if not GCP_PROJECT_ID or not VERTEX_DATASTORE_ID:
        return json.dumps({"error": "Vertex AI Search not configured."})

    try:
        client_options = None
        if VERTEX_LOCATION and VERTEX_LOCATION != "global":
            client_options = ClientOptions(
                api_endpoint=f"{VERTEX_LOCATION}-discoveryengine.googleapis.com"
            )
        client = discoveryengine.SearchServiceClient(client_options=client_options)

        all_results = []

        # 1. Website knowledge base (PM methodology, frameworks)
        try:
            website_results = _search_one(
                client, GCP_PROJECT_ID, VERTEX_LOCATION,
                VERTEX_DATASTORE_ID, query, num_results
            )
            all_results.extend(website_results)
        except Exception as e:
            all_results.append({"source": "website_kb", "error": str(e)})

        # 2. PDF document store (salary benchmarks, rate guides, industry reports)
        try:
            doc_results = _search_one(
                client, GCP_PROJECT_ID, VERTEX_LOCATION,
                VERTEX_DOCS_DATASTORE_ID, query, num_results
            )
            all_results.extend(doc_results)
        except Exception as e:
            all_results.append({"source": "docs_kb", "error": str(e)})

        # 3. User's personal project document datastore (if username provided)
        if username:
            try:
                from tools.user_datastore import search_user_datastore
                user_results = search_user_datastore(username, query, num_results)
                all_results.extend(user_results)
            except Exception as e:
                pass  # silently skip — user datastore may not exist yet

        if not all_results:
            return json.dumps({"message": "No results found", "query": query})

        return json.dumps(all_results, indent=2, default=str)

    except Exception as e:
        return json.dumps({"error": str(e)})
