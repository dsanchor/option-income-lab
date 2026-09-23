# Session Log: Account-Local FIFO and Dividend Buy Labels

**Timestamp:** 2026-09-23T10:28:41Z

Consolidated holdings now preserve FIFO lot depletion per account before
aggregating residual shares and basis. Dividend-derived scrip share acquisitions
use one shared metadata-aware `Dividend · Buy` presentation label across six
movement surfaces without changing stored transaction types or accounting.

Basher approved both workstreams. The only recorded residual is missing
committed regression coverage for cross-account transfer preservation; the
independent probe passed.
