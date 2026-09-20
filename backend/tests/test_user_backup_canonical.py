from datetime import datetime, timezone
from decimal import Decimal

from src.backup.canonical import canonical_hash, canonical_json_bytes


def test_canonical_hash_ignores_order_and_normalizes_values():
    left = {"b": Decimal("1.2000"), "a": datetime(2026, 9, 19, tzinfo=timezone.utc)}
    right = {"a": "2026-09-19T00:00:00Z", "b": "1.2"}
    assert canonical_json_bytes(left) == canonical_json_bytes(right)
    assert canonical_hash(left) == canonical_hash(right)


def test_cosmos_metadata_is_removed_recursively():
    assert canonical_json_bytes({"id": "x", "_etag": "secret", "nested": {"_ts": 2}}) == (
        b'{"id":"x","nested":{}}'
    )
