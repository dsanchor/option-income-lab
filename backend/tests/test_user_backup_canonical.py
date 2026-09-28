from datetime import datetime, timezone
from decimal import Decimal

from src.backup.canonical import canonical_hash, canonical_json_bytes




def test_cosmos_metadata_is_removed_recursively():
    assert canonical_json_bytes({"id": "x", "_etag": "secret", "nested": {"_ts": 2}}) == (
        b'{"id":"x","nested":{}}'
    )
