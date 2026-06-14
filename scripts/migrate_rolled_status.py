#!/usr/bin/env python3
"""One-time migration: update positions with rolled_to from status='closed' to status='rolled'.

Usage:
    python scripts/migrate_rolled_status.py

Requires env vars (or config.yaml):
    COSMOSDB_ENDPOINT
    COSMOSDB_KEY
    COSMOSDB_DATABASE (optional, defaults to 'stock-options-manager')

Dry-run by default. Pass --apply to actually write changes.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    parser = argparse.ArgumentParser(description="Migrate rolled positions status")
    parser.add_argument("--apply", action="store_true",
                        help="Actually apply changes (default is dry-run)")
    args = parser.parse_args()

    # Try loading from config.yaml first, fall back to env vars
    endpoint = os.environ.get("COSMOSDB_ENDPOINT", "")
    key = os.environ.get("COSMOSDB_KEY", "")
    database = os.environ.get("COSMOSDB_DATABASE", "stock-options-manager")

    if not endpoint or not key:
        try:
            from src.config import Config
            cfg = Config()
            cosmos_cfg = cfg.config.get("cosmosdb", {})
            endpoint = endpoint or cosmos_cfg.get("endpoint", "")
            key = key or cosmos_cfg.get("key", "")
            database = cosmos_cfg.get("database", database)
            # Resolve env var references like ${COSMOSDB_ENDPOINT}
            if endpoint.startswith("${") and endpoint.endswith("}"):
                endpoint = os.environ.get(endpoint[2:-1], "")
            if key.startswith("${") and key.endswith("}"):
                key = os.environ.get(key[2:-1], "")
        except Exception:
            pass

    if not endpoint or not key:
        print("ERROR: COSMOSDB_ENDPOINT and COSMOSDB_KEY must be set.")
        sys.exit(1)

    from azure.cosmos import CosmosClient

    client = CosmosClient(endpoint, credential=key)
    db = client.get_database_client(database)
    container = db.get_container_client("symbols")

    # Query all symbol documents
    query = "SELECT * FROM c WHERE ARRAY_LENGTH(c.positions) > 0"
    docs = list(container.query_items(query=query, enable_cross_partition_query=True))

    total_updated = 0
    for doc in docs:
        modified = False
        for pos in doc.get("positions", []):
            if pos.get("status") == "closed" and pos.get("rolled_to"):
                if args.apply:
                    pos["status"] = "rolled"
                modified = True
                total_updated += 1
                print(f"  {'UPDATING' if args.apply else 'WOULD UPDATE'}: "
                      f"{doc.get('symbol', '?')} / {pos.get('position_id', '?')} "
                      f"(rolled_to: {pos.get('rolled_to')})")

        if modified and args.apply:
            container.replace_item(item=doc["id"], body=doc)

    print(f"\n{'Applied' if args.apply else 'Dry-run'}: {total_updated} position(s) "
          f"{'updated' if args.apply else 'would be updated'} to status='rolled'.")
    if not args.apply and total_updated > 0:
        print("Run with --apply to execute the migration.")


if __name__ == "__main__":
    main()
