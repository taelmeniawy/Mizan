# Decisions Approved for Implementation

**Owner approval:** 2026-11-16; OQ-INV-012 and OQ-INV-004 approved 2026-11-17; OQ-QUO-001, OQ-QUO-005, and OQ-DOC-001 approved 2026-11-17; ADR-0004 Option B (Qt `QTextDocument` + `QTextTable` + custom paginator) approved 2026-11-17. Documentation remains the spec; this file records implementation decisions only.

| ID | Decision | Source |
|---|---|---|
| ADR-0002 | SQLite/WAL/FKs/BEGIN IMMEDIATE/online backup approach approved for implementation. | ADR-0002 |
| ADR-0003 | Integer piastres, micros quantities, micro-EGP prices, ROUND_HALF_UP, largest remainder, no float, LCP layers, `R(k)` returns. | ADR-0003 |
| ADR-0004 | **Option B approved:** `QTextDocument` + `QTextTable` with a custom paginator, using Qt (`QPdfWriter`/`QPrinter`) for both PDF and printing. Runtime is `PySide6-Essentials`; no Qt WebEngine and no separate PDF library. T-DOC-005 (licensed/embedded Arabic font), T-DOC-007 (golden image), and T-DOC-009 (real A4 printer) remain open. | Owner approval 2026-11-17 |
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
| OQ-QUO-001 | Save `status_before_conversion` in `quotation_status_history`. When a conversion draft is cancelled, restore the saved status only if the quotation has not expired; an expired quotation remains expired and a new conversion still requires `override_expired_quotation` and a reason. | Owner instruction 2026-11-17: adopt documented recommendation |
| OQ-QUO-005 | `DRAFT` and `ISSUED` quotations may be cancelled with `cancel_quotation` and a recorded reason. An `ACCEPTED` quotation cannot be cancelled. `REJECTED`, `CANCELLED`, `SUPERSEDED`, and `CONVERTED` are final. | Owner instruction 2026-11-17: adopt documented recommendation |
| OQ-DOC-001 | Document numbers use `{TYPE}-{YYYY}-{NNNNNN}` per type/year from a database sequence; committed numbers are unique and are not reused; gaps are permitted only when a transaction fails. Quotation revisions add `-R{version_no}` to the series base number. | Owner instruction 2026-11-17: adopt documented recommendation |
| Other OQs | Adopt the "التوصية" in `docs/10_OPEN_QUESTIONS.md`; unresolved implementation details follow the cited ADR. | Owner instruction |

Implementation status: phases 1–6 complete; Phase 7 is in progress/partial. The quotation core and the approved A4 rendering slice are implemented: shared QTextDocument/QTextTable paginator, official quotation PDF export with immutable snapshots/hash, managed file storage, reprint verification, and the shared PDF/printer layout path. Phase 7 is **not** complete: T-DOC-005 (approved/licensed Arabic font) and T-DOC-009 (real A4 printer) remain open, as does T-DOC-007 (golden image after template approval). `OQ-DOC-002` remains open/deferred; no font or logo was bundled. Phase 8 must not start until the full Phase 7 gate passes.

**OQ-DOC-002 remains open/deferred:** logo/font selection is not approved and no font is bundled. It blocks release/document identity, not quotation-core development.

LCP is approved for implementation by the owner instruction above, but remains documented as a proposal until the docs are formally updated.
