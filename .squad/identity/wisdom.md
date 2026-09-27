---
last_updated: 2026-03-26T14:28:18.920Z
---

# Team Wisdom

Reusable patterns and heuristics learned through work. NOT transcripts — each entry is a distilled, actionable insight.

## Patterns

<!-- Append entries below. Format: **Pattern:** description. **Context:** when it applies. -->

**Pattern:** Bound XML parsing by elements or bytes, never by line count; valid
provider XML may serialize an entire multi-decade dataset on one line.
**Context:** Large external reference feeds such as ECB full-history rates,
where deterministic coverage and memory limits must coexist.
