# Linus — lot average price implementation

**Timestamp:** 2026-09-27T05:25:39Z
**Requested by:** Copilot
**Mode:** Background

Linus implemented `lot_average_price_eur` end to end. A shared strict Decimal
helper now supplies FIFO BUY-lot unit cost and movement response enrichment,
while the Stocks table and movement detail consume the server field. Targeted
validation completed successfully.
