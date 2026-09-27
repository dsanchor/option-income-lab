#!/usr/bin/env python3
"""Post-deployment checks executed with the backup Job's real UAMI."""

from __future__ import annotations

import argparse
import os
import sys
import uuid


def verify_blob_rbac() -> None:
    from azure.identity import DefaultAzureCredential
    from azure.storage.blob import BlobServiceClient

    account = os.environ["AZURE_STORAGE_ACCOUNT_NAME"]
    container = os.environ.get("BACKUP_BLOB_CONTAINER", "user-data-backups")
    credential = DefaultAzureCredential()
    service = BlobServiceClient(
        account_url=f"https://{account}.blob.core.windows.net",
        credential=credential,
    )
    name = f"v1/staging/_rbac-check/{uuid.uuid4()}.txt"
    blob = service.get_blob_client(container=container, blob=name)
    try:
        blob.upload_blob(b"rbac-check", overwrite=False, tags={"purpose": "rbac-check"})
        if blob.download_blob().readall() != b"rbac-check":
            raise RuntimeError("Blob round-trip content mismatch")
        blob.set_blob_tags({"purpose": "rbac-check", "verified": "true"})
    finally:
        try:
            blob.delete_blob(delete_snapshots="include")
        except Exception:
            print("Sentinel cleanup failed.", file=sys.stderr)
            raise


def verify_internal_api(url: str) -> None:
    import requests

    response = requests.get(f"{url.rstrip('/')}/healthz", timeout=15)
    response.raise_for_status()
    if response.json() != {"status": "ok"}:
        raise RuntimeError("Internal API health response was unexpected")


def verify_cosmos_key() -> None:
    from azure.cosmos import CosmosClient

    client = CosmosClient(os.environ["COSMOSDB_ENDPOINT"], os.environ["COSMOSDB_KEY"])
    database_name = os.environ.get("COSMOSDB_DATABASE", "stock-options-manager")
    client.get_database_client(database_name).read()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--blob-rbac", action="store_true")
    parser.add_argument("--cosmos-key", action="store_true")
    parser.add_argument("--internal-api-url")
    args = parser.parse_args()
    if not args.blob_rbac and not args.cosmos_key and not args.internal_api_url:
        parser.error("select at least one verification")
    if args.blob_rbac:
        verify_blob_rbac()
    if args.cosmos_key:
        verify_cosmos_key()
    if args.internal_api_url:
        verify_internal_api(args.internal_api_url)
    print("Azure runtime access verification passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
