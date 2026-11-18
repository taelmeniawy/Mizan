# Decisions Approved for Implementation

**Owner approval:** 2026-11-16; OQ-INV-012 and OQ-INV-004 approved 2026-11-17; OQ-QUO-001, OQ-QUO-005, and OQ-DOC-001 approved 2026-11-17; ADR-0004 Option B (Qt `QTextDocument` + `QTextTable` + custom paginator) approved 2026-11-17; **Noto Naskh Arabic 2.000 (SIL OFL 1.1) approved 2026-11-17**; **V1 quotation template approved without a logo 2026-11-17 (option 1A)**. Documentation remains the spec; this file records implementation decisions only.

| ID | Decision | Source |
|---|---|---|
| ADR-0002 | SQLite/WAL/FKs/BEGIN IMMEDIATE/online backup approach approved for implementation. | ADR-0002 |
| ADR-0003 | Integer piastres, micros quantities, micro-EGP prices, ROUND_HALF_UP, largest remainder, no float, LCP layers, `R(k)` returns. | ADR-0003 |
| ADR-0004 | **Option B approved:** `QTextDocument` + `QTextTable` with a custom paginator, using Qt (`QPdfWriter`/`QPrinter`) for both PDF and printing. Runtime is `PySide6-Essentials`; no Qt WebEngine and no separate PDF library. T-DOC-007 is covered by the approved-template golden reference (2026-11-17); T-DOC-009 (real A4 printer) remains open. | Owner approval 2026-11-17 |
| FONT-001 | **Noto Naskh Arabic 2.000 approved** for Arabic documents. Regular and Bold TTF instances from upstream commit `8386d579f0fc5064dc8b66c6b5a9382f3822b82d` are bundled with `LICENCE` (SIL OFL 1.1) and `SOURCE.md`. The renderer loads them from package data and the PDF embeds the approved font; `rlig` is disabled to keep PDF text extraction complete. Latin glyphs absent from the Arabic font may use Qt system fallback. | Owner approval 2026-11-17 |
| OQ-DOC-002 | **Resolved for V1:** quotation documents use the approved template with **no logo**; Noto Naskh Arabic remains the approved font. Logo/branding may be reconsidered in a later version; no logo or branding asset is added. | Owner approval 2026-11-17 (option 1A) |
| TEMPLATE-001 | **V1 quotation template approved as reviewed** (`A4-QUOTATION-V1` layout, Noto Naskh Arabic, per-line money formatting, direction-isolated summary). The deterministic golden reference `tests/golden/quotation-a4-v1.json` covers T-DOC-007; it is a content/layout golden, not a pixel image, because Latin tokens fall back to a system font. | Owner approval 2026-11-17 (option 1A) |
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

Implementation status: phases 1–6 complete; Phase 7 is in progress/partial. The quotation core and the approved A4 rendering slice are implemented: shared QTextDocument/QTextTable paginator, official quotation PDF export with immutable snapshots/hash, managed file storage, reprint verification, the shared PDF/printer layout path, and the approved bundled Noto Naskh Arabic font with PDF embedding/license verification. The owner approved the reviewed V1 template without a logo (option 1A), and T-DOC-007 is covered by the deterministic golden reference `tests/golden/quotation-a4-v1.json`. Phase 7 is **not** complete: **T-DOC-009 (real A4 printer) is the only open Phase 7 gate item**. The owner authorized Phase 8 development on 2026-11-18 while T-DOC-009 remains pending until a physical A4 printer is available. Phase 7 acceptance requires inspection of the printed pages.

Phase 8 (reports/reconciliation) is implemented and reviewed (2026-11-18). Phase 9 is implemented and tested at the software level: verified and AES-256-GCM encrypted backups, restore into a new directory, scheduled daily/monthly backups with retention, verified off-device copies, audit-log viewing with database-enforced append-only rows (migration 10), and a startup database/document integrity check. Phase 9 still requires a restore on a physically separate device, RPO/RTO and off-device frequency (`OQ-BKP-003`), and audit retention duration (`OQ-SEC-003`).

Phase 10 started on 2026-11-18 with the desktop shell: data-root bootstrap and first-run Owner setup, Arabic RTL login and permission-aware navigation, dashboard, backup/restore with background progress, audit viewer, and the integrity report. Business screens, packaging/signing and clean-machine acceptance remain open. The Manager role now holds `backup_database` exactly as the docs/08 matrix states (the code previously excluded it); `restore_database` remains Owner-only.

**OQ-DOC-002:** resolved for V1 by the owner's 2026-11-17 decision (option 1A): the approved quotation template has no logo, and Noto Naskh Arabic remains the approved font. Logo support/assets may be reconsidered in a later version; no logo or branding asset was added.

LCP is approved for implementation by the owner instruction above, but remains documented as a proposal until the docs are formally updated.
