"""
Add authoritative websites to the Vertex AI Search website crawler datastore.
Run from the project root: python knowledge_base/add_websites.py

These sites are crawled and indexed automatically by Vertex AI Search,
keeping benchmark data fresh without manual document uploads.
"""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv()

from google.cloud import discoveryengine_v1 as discoveryengine

PROJECT_ID   = os.environ["GCP_PROJECT_ID"]
LOCATION     = os.environ.get("VERTEX_LOCATION", "global")
DATASTORE_ID = os.environ["VERTEX_DATASTORE_ID"]   # website crawler store

# Authoritative sources — chosen for PM benchmarks, timelines, and rates.
# Each entry: (url_pattern, description)
# Use "/*" suffix for full-site crawl, specific paths for scoped crawl.
TARGET_SITES = [
    # Project Management methodology & benchmarks
    ("www.pmi.org/learning/library/*",
     "PMI learning library - PM standards, templates, benchmarks"),
    ("www.projectmanagement.com/articles/*",
     "ProjectManagement.com articles - PM best practices and case studies"),

    # Mobile & software development timelines
    ("clutch.co/app-developers/resources/*",
     "Clutch app developer resources - real project cost and timeline data"),
    ("www.mobiloud.com/blog/*",
     "MobiLoud blog - mobile app development timelines and costs"),
    ("www.goodfirms.co/resources/*",
     "GoodFirms resources - software project benchmarks and cost surveys"),

    # Developer salary & rate benchmarks
    ("survey.stackoverflow.co/*",
     "Stack Overflow Developer Survey - developer salaries and tool usage"),
    ("www.levels.fyi/blog/*",
     "Levels.fyi blog - software engineer compensation data"),

    # Payment / fintech integration docs
    ("stripe.com/docs/*",
     "Stripe documentation - payment integration complexity reference"),
    ("docs.klarna.com/*",
     "Klarna developer docs - BNPL integration scope and effort"),
    ("developers.afterpay.com/*",
     "Afterpay developer docs - BNPL integration scope and effort"),

    # Agile / sprint velocity
    ("www.scrum.org/resources/*",
     "Scrum.org resources - sprint velocity and agile estimation standards"),
    ("www.atlassian.com/agile/*",
     "Atlassian agile guides - velocity, estimation, and team sizing"),
]


def add_websites():
    client_options = None
    if LOCATION and LOCATION != "global":
        from google.api_core.client_options import ClientOptions
        client_options = ClientOptions(api_endpoint=f"{LOCATION}-discoveryengine.googleapis.com")

    client = discoveryengine.SiteSearchEngineServiceClient(client_options=client_options)

    site_search_engine = (
        f"projects/{PROJECT_ID}/locations/{LOCATION}"
        f"/collections/default_collection/dataStores/{DATASTORE_ID}/siteSearchEngine"
    )

    print(f"Adding {len(TARGET_SITES)} website(s) to datastore: {DATASTORE_ID}\n")
    success, failed, skipped = 0, 0, 0

    for url_pattern, description in TARGET_SITES:
        target_site = discoveryengine.TargetSite(
            provided_uri_pattern=url_pattern,
            type_=discoveryengine.TargetSite.Type.INCLUDE,
            exact_match=False,
        )
        try:
            op = client.create_target_site(
                parent=site_search_engine,
                target_site=target_site,
            )
            # Long-running operation — we don't block on it
            print(f"  [QUEUED]   {url_pattern}")
            print(f"             {description}")
            success += 1
        except Exception as e:
            err = str(e)
            if "ALREADY_EXISTS" in err or "409" in err:
                print(f"  [EXISTS]   {url_pattern}")
                skipped += 1
            else:
                print(f"  [FAILED]   {url_pattern} -- {err}")
                failed += 1
        time.sleep(0.5)

    print(f"\nDone — {success} queued for crawl, {skipped} already existed, {failed} failed.")
    print("\nNote: Vertex AI Search will crawl and index these sites automatically.")
    print("Initial indexing may take 24–48 hours. Re-crawl frequency is managed in the console.")


if __name__ == "__main__":
    add_websites()
