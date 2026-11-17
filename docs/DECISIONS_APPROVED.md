# Decisions Approved for Implementation

**Owner approval:** 2026-11-16; OQ-INV-012 and OQ-INV-004 approved 2026-11-17. Documentation remains the spec; this file records implementation decisions only.

| ID | Decision | Source |
|---|---|---|
| ADR-0002 | SQLite/WAL/FKs/BEGIN IMMEDIATE/online backup approach approved for implementation. | ADR-0002 |
| ADR-0003 | Integer piastres, micros quantities, micro-EGP prices, ROUND_HALF_UP, largest remainder, no float, LCP layers, `R(k)` returns. | ADR-0003 |
| ADR-0004 | A4-only rendering; UI deferred. | ADR-0004 |
| ADR-0005 | Offline local SQLite; backup/restore policy accepted. | ADR-0005 |
| ADR-0001 override | Python 3.12 + stdlib sqlite3 + pytest + PySide6 later; own migration runner; no ORM. | Owner override |
| OQ-ACC-001 | No tax/VAT fields in V1. | OQ recommendation |
| OQ-ACC-002 | Calendar fiscal year, closable monthly periods, 4-digit COA. | OQ recommendation |
| OQ-ACC-003 | Adopt LCP and documented rounding; no float. | OQ recommendation |
| OQ-INV-001 | Mandatory landed-cost allocation by line value; largest remainder. | OQ recommendation |
| OQ-PRC-002 | Below-cost sale forbidden unless `approve_below_cost` + reason. | OQ recommendation |
| OQ-SAL-007 | Sales return settles AR first, then customer credit; cash refund requires permission. | OQ recommendation |
| OQ-SEC-001 | Owner/Manager/Cashier matrix from docs/08. | OQ recommendation |
| OQ-INV-012 | Every returned reel length creates a new returned reel/layer, explicitly linked to the original reel and original sale allocation; it is never merged into or added to the original reel's remaining quantity. Over-return and duplicate-return protections remain. | Owner approval 2026-11-17 |
| OQ-INV-004 | Damaged sales returns keep `SALE_RETURN_IN` then `DAMAGE` in one transaction and post the captured damaged cost as direct loss: debit `5020`, credit `5010`. No `1210` damaged-inventory account or disposal workflow; commercial settlement behavior is unchanged. | Owner approval 2026-11-17 |
| Other OQs | Adopt the "التوصية" in `docs/10_OPEN_QUESTIONS.md`; unresolved implementation details follow the cited ADR. | Owner instruction |

Implementation status: phases 1–6 implemented; Phase 6 acceptance is covered by the recorded test suite. LCP is approved for implementation by the owner instruction above, but remains documented as a proposal until the docs are formally updated.
