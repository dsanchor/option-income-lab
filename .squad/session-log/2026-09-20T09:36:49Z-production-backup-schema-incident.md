# Session Log: Production Backup Schema Incident

**Timestamp:** 2026-09-20T09:36:49Z
**Requested by:** dsanchor

## Work consolidated

- Recorded the production `SchemaError` and Livingston's read-only production
  diagnosis.
- Preserved the legitimate schema classifications for security migration
  provenance and ledger fields, plus runtime exclusion of
  `symbol_config.pricing_cache`.
- Recorded Basher's rejection of a global identifier-key exemption.
- Recorded Rusty's exact-path `$.source.activity_id` revision and the continued
  blocking of JWTs, credentials, and same-named keys elsewhere.
- Recorded Basher's final approval and the requirement to replace deployed
  image `sha-1d368a4`.
- Merged and deduplicated both current decision inbox records, then removed
  the merged files.
- Reused the useful Livingston, Rusty, and Basher history entries already
  present; added only this consolidation note to Scribe history.

## Validation evidence

Final gate: 69 backup/infrastructure tests, 218 persistence regressions, 71
Symbol Details backend regressions, 78 frontend contracts, independent
projection/archive and adversarial secret probes, Python compile, and
`git diff --check`.

No production, test, or product documentation files were modified by Scribe.
No commit was created. Deployment of a new image remains required.
