# Lot Average Price and Dividend Economic Value

**Date:** 2026-09-27T05:28:55Z
**Status:** Lot price implemented and approved; economic value proposed

- Danny established the strict net-inclusive per-lot average price contract for
  ordinary Buy and Dividend · Buy.
- Linus implemented the backend-derived API field and frontend presentation
  while reusing the unrounded helper for FIFO BUY lots.
- Basher's tests exposed `CASH_TOP_UP` as incorrectly eligible. The helper was
  corrected to allow only ordinary BUY or `SHARE_ACQUISITION`, and the final
  gate passed 106 backend and 125 frontend targeted tests.
- Danny and Linus then designed a separate event-level dividend economic-value
  measure: cash net plus provenance-qualified share fair value, less
  investor-funded top-ups and attributable fees.
- The economic-value design keeps cash income unchanged, prevents double
  counting, records valuation/FX provenance, and reports incomplete coverage.
  It is proposed only and has not been implemented.
