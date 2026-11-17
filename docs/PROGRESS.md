# Progress

- Phase: **7 in progress — partial.** Phases 1–6 are complete. Phase 7's renderer-independent quotation core is implemented and tested, but Phase 7 is **not complete**: ADR-0004 remains PROPOSED / NEEDS APPROVAL and the PDF/A4/printer acceptance criteria remain open. Phase 8 has not started and remains gated on the full Phase 7 gate.
- Done:
  - Phase 6 sales invoices: retail/wholesale/contractor, cash/credit, DRAFT edit/cancel, POSTED immutability, reversal record.
  - Price resolver priority customer price → price list → default; manual price source/reason; line and invoice discounts with largest-remainder allocation.
  - Below-cost guard: `approve_below_cost` permission plus a recorded reason; the check uses the actual allocated LCP cost at posting.
  - Atomic posting: LCP/FIFO lot allocations, reel allocations/cutting, frozen line price/cost, balanced AR/Cash/Revenue/COGS/Inventory journal, no negative stock, full rollback on failure.
  - Customer receipts DRAFT/POSTED with invoice allocation, overpayment → customer credit, credit application against an invoice, customer AR balance/statement.
  - Customer statement now shows account 1100 AR movements and account 2020 customer-credit movements separately: `kind`, `account_code`, `affects_ar`, `affects_credit`, `ar_balance`, `credit_balance`, and document references. Customer credits are not treated as negative AR. `customer_balance` remains AR-only; `customer_credit_balance` and `customer_account_summary` expose the credit separately.
  - Sellable sales returns linked to original invoice allocations: explicit selection or default “most recently consumed first”, reason recorded on the return, original cost via `R(k)`, duplicate/excess prevention; covers unpaid/partial/full AR, reissue and re-return child layers (`T-RET-009/014/016/018`), plus `T-RET-015` multiple-allocation/micro-reissue coverage.
  - `OQ-INV-012` reel returns: every returned length creates a new `RETURN` lot/reel/layer; `reels.source_reel_id` and `source_issue_allocation_id` link to the original reel and sale allocation; the original reel/layer is never increased or reopened; over-return/duplicate protections remain. `T-RET-013` covers partial and full reel returns and no double counting; reel invoice reversals use the same returned-reel mechanism.
  - `OQ-SAL-007` settlement: AR first, excess customer credit, cash/bank refund only with `issue_customer_refund` + a document/reference; AR never negative.
  - `T-BAL-006`: receipts and two returns reduce AR to zero without going negative; the statement identifies each AR movement and the resulting 2020 credit separately.
  - Damaged returns: `SALE_RETURN_IN` then `DAMAGE` in one transaction; cost is captured on the return line and DAMAGE movement; the captured damaged cost is posted as direct loss (`5020` debit / `5010` credit); no `1210` or disposal workflow; commercial settlement is unchanged.
  - `T-INV-026`: updated for the approved policy — a damaged reel return creates a new returned reel/layer linked to the original reel and sale allocation; the original reel remains unchanged.
  - `T-RB-002`: forced DAMAGE failure, or failure while posting the damaged-cost journal, rolls back movements, return layers, allocations, journal, posted document, customer credit, refund and settlement fields.
  - Authorization coverage: Cashier can post sales but cannot edit price, approve below cost, apply an invoice discount, cancel a draft, post a sales return, or reverse a cash sale without `issue_customer_refund`; a user with no `post_sales` permission is rejected in the service.
  - Return journal balance assertions cover sellable, damaged, fully-paid-credit, and mixed sellable/damaged returns; sale reversal journal balance and statement behavior are covered.
  - Migration 5 adds sales, sales lines/status history, receipts/allocations, returns/lines/selection rows, customer credits/applications, and posted-document immutability triggers; migration 6 adds returned-reel source links (`source_reel_id`, `source_issue_allocation_id`) and indexes.
  - Shared inventory ledger extracted to `mizan/inventory.py`; purchasing now reuses it, preserving existing FIFO/reel behaviour.
- Tests: 101 passed, 0 failed (57 Phase 6 tests + 44 other/pre-existing), run 2026-11-17 in the project-local `.venv` (Python 3.12.10, pytest 9.2.0). Command: `.venv\Scripts\python.exe -m pytest -q`. Pytest is declared under `[project.optional-dependencies] dev`; PyPI was unreachable during setup, so the venv was bootstrapped from an existing local pytest installation. No global Python was modified.

## Phase 7 — partial (2026-11-17)

**Status: IN PROGRESS — PARTIAL. Renderer-independent quotation core implemented; PDF/A4/printer and document-identity work remains open.**

### Resolved decisions applied

The general owner instruction to adopt documented OQ recommendations was applied to the three Phase 7 OQs below; current source documents showed no conflict. The related `OQ-DOC-002` item remains deferred.

- **`OQ-QUO-001` — conversion-draft cancellation/restoration:** `status_before_conversion` is recorded in `quotation_status_history`. Cancelling a conversion draft restores the saved status only when the quotation has not expired; an expired quotation stays expired and a new conversion requires `override_expired_quotation` and a reason.
- **`OQ-QUO-005` — cancellation policy:** `DRAFT` and `ISSUED` quotations can be cancelled with `cancel_quotation` and a recorded reason; an `ACCEPTED` quotation cannot be cancelled; `REJECTED`/`CANCELLED`/`SUPERSEDED`/`CONVERTED` are final.
- **`OQ-DOC-001` — document numbering:** `{TYPE}-{YYYY}-{NNNNNN}` per type/year from a database sequence; committed numbers are unique and not reused; gaps are allowed only on transaction failure. Quotation roots use `Q-YYYY-NNNNNN`; revisions add `-R{version_no}`.
- **`OQ-DOC-002` — deferred, not applied:** logo/font selection remains open and blocks release/document identity, not quotation-core development. No logo asset or font was added or bundled.

### Implemented (renderer-independent)

- Migration 7 adds `quotation_series`, `quotations`, `quotation_lines`, `quotation_status_history`, `document_exports`, `document_snapshots`, `sales.source_quotation_id`, the partial unique index `ux_sales_source_quotation_active`, and immutability/root/issuance triggers.
- `QuotationService` covers create/edit draft, line snapshots, price resolution and manual-price permission/reason, item/invoice discounts with largest remainder, issue/accept/reject/cancel, revisions and supersession, expiry computation (last day valid), conversion to a sales `DRAFT`, expired-conversion override, and conversion-draft cancellation/restoration.
- Revision guard: no revision while an active (non-cancelled) sale exists in the series; only the latest version can be revised. Creating a revision does not supersede the previous version; issuing the revision does, in the same transaction.
- Conversion copies the quotation's frozen quantities/prices/discounts and line snapshots, creates the sales `DRAFT` only, performs an advisory stock check without reservation, and never creates inventory movements or journals.
- Duplicate-conversion prevention is enforced by `ux_sales_source_quotation_active` and tested directly against SQLite as well as through the service. Cancelling the draft releases the index.
- Sale posting marks the quotation `CONVERTED` in the same transaction; a failed posting leaves it unchanged. Cancelling a conversion draft leaves the sale row cancelled (no delete) and restores the quotation status per `OQ-QUO-001`.
- Quotation-generated sales drafts lock commercial fields; `update_draft_header` allows payment terms/account/due date/invoice date changes where the sales rules allow.
- `DocumentService` records export metadata and immutable document snapshots. `issue_quotation` requires an already-registered **official PDF export**; without one it raises a clear ADR-0004 prerequisite error. No PDF is generated, hashed from a real file, extracted, measured, or printed.
- Shared `pricing.py` and `inventory.available_stock` prevent quotation/sales logic from drifting.

### Tests (actual result)

- Phase 7 targeted: `24 passed` — `.venv\Scripts\python.exe -m pytest tests/test_phase7_quotations.py -q`.
- Full suite: **`125 passed, 0 failed`** — `.venv\Scripts\python.exe -m pytest -q` (run 2026-11-17, project-local `.venv`, Python 3.12.10, pytest 9.2.0).
- The Phase 7 issue tests use a **test-double export row** (`renderer_version='TEST_DOUBLE_NO_RENDERER'`) only to reach `ISSUED`; they do not claim PDF/A4 validation. See `tests/phase7_support.py`.

### Implementation notes / deviations

- The quote status is **not** changed when a conversion draft is created (per 05 §3.2); it becomes `CONVERTED` only when the sale is posted. Restoration therefore usually records the unchanged status; if the status changed while the draft existed, the saved `status_before_conversion` is restored unless the quotation has expired.
- `document_exports` records metadata only; file existence and real-file SHA-256 verification remain part of the open renderer/export gate.
- `document_snapshots` is immutable (trigger) and unique per `(document_type, document_id)`.
- Snapshot captures store/customer/project/lines/totals and export/template/renderer metadata when available. Store identity may be absent until configured; logo/font are deliberately not bundled (`OQ-DOC-002`).
- Revisions copy the source `issue_date`/`valid_until`; the draft can be edited before issuing. This is an implementation choice, not a new business approval.
- No renderer, UI, printer integration, logo/font asset, or new dependency was added.

### What remains in Phase 7 (open)

- **ADR-0004 is still PROPOSED / NEEDS APPROVAL.** No A4/PDF engine has been selected or implemented.
- Official PDF generation, file movement, real-file SHA-256 verification, and reprint/missing-file behavior (`T-QUO-001/002/018`).
- PDF text extraction, Arabic shaping/RTL, A4 dimensions, repeated table headers, row-splitting behavior, golden/pagination checks (`T-DOC-001..008`, `AC-QUO-020/021`).
- Real A4 printer test; Microsoft Print to PDF does not substitute (`T-DOC-009/010`, `AC-QUO-022`).
- Document identity: logo asset handling, font licensing, and bundling (`OQ-DOC-002`, `T-QUO-019`, `AC-QUO-003`).
- The full Phase 7 exit criteria in `docs/11_IMPLEMENTATION_ROADMAP.md` remain unchanged. **Phase 8 must not start until they all pass.**


## Phase 6 acceptance checklist

Status: `COVERED` = implemented and directly tested; `PARTIAL` = implemented in part, but a documented owner/accountant decision blocks complete acceptance; `BLOCKED` = cannot pass until a decision; `LATER` = later-phase test/requirement, not Phase 6.

| Phase 6 exit criterion | Status | Evidence / note |
|---|---|---|
| T-RET-*, T-BAL-001/006, T-HIS-001/002/006 | COVERED | All cited cases are covered, including `T-RET-002/013/017` and the added reel return/reversal coverage. |
| Sales invoice + journal + stock in one transaction | COVERED | `test_posting_creates_balanced_journal_and_freezes_cost`, `test_posting_rolls_back_on_journal_failure`. |
| No sale without stock | COVERED | `test_fifo_spans_lots_and_keeps_no_negative_stock`, `test_multi_line_shortage_rolls_back_all_lines`, `test_shortage_reports_cumulative_demand`. |
| Return at original cost | COVERED | Non-reel returns (`T-RET-003/004/014/015/016/018`), reel return (`T-RET-013`), and damaged-cost `5020/5010` (`T-RET-002/017`) are covered. |
| No editing a POSTED invoice | COVERED | `test_posted_invoice_is_immutable`, reversal immutability, DB triggers. |

| Cited case | Status | Evidence / note |
|---|---|---|
| T-RET-001 | COVERED | Sellable return restores inventory/COGS at original cost. |
| T-RET-002 | COVERED | Damaged flow, no sellable stock, and direct loss `5020/5010` are covered. |
| T-RET-003 | COVERED | Partial return cost 1,167 and remaining 1,166. |
| T-RET-004 | COVERED | Re-sale of returned layer at 1,167; original history fixed. |
| T-RET-005 | COVERED | Over-return rejected without partial effects. |
| T-RET-006 | COVERED | Duplicate return rejected. |
| T-RET-007 | COVERED | Partially paid invoice reduces AR, no refund. |
| T-RET-008 | COVERED | Fully paid invoice → customer credit by default; cash refund branch covered with permission/document. |
| T-RET-009 | COVERED | Unpaid invoice reduces AR only. |
| T-RET-010 | COVERED | Return above outstanding never makes AR negative; excess to 2020/refund. |
| T-RET-011 | COVERED | Full sale reversal creates reversal journal and opposite inventory. |
| T-RET-012 | COVERED | Purchase return remains covered by the Phase 5 purchase-return tests. |
| T-RET-013 | COVERED | New returned reel/layer linked to the original reel/allocation; original reel unchanged; partial/full returns and no double counting covered. |
| T-RET-014 | COVERED | Split returns 583 + 584 = 1,167. |
| T-RET-015 | COVERED | First/third allocations returned to their own allocations; 0.001 reissue is zero-cost but non-negative. |
| T-RET-016 | COVERED | Reissued return layer uses 1,167; original allocation unchanged. |
| T-RET-017 | COVERED | Mixed sellable/damaged flow creates the return layer then DAMAGE; sellable cost returns `1200/5010`, damaged cost posts `5020/5010`. |
| T-RET-018 | COVERED | Reissued return creates a child layer 1/34; Q0/C0 conserved. |
| T-RET-019 | COVERED | Over-return rejected with no layer/movement/journal. |
| T-BAL-001 | COVERED | Statement balance and per-movement document references covered. |
| T-BAL-006 | COVERED | Receipts + returns leave AR non-negative; statement identifies AR and 2020 credit movements separately. |
| T-HIS-001 | COVERED | Price-list change after posting does not change invoice price. |
| T-HIS-002 | COVERED | Purchase-cost change after posting does not change invoice cost. |
| T-HIS-006 | COVERED | Return after price/cost change uses original price and original allocation cost. |
| T-SEC-001 | COVERED | Phase 2 Cashier cost-view denial. |
| T-SEC-002 | COVERED | Manual price edit denied to Cashier. |
| T-SEC-003 | COVERED | Cashier posts a sale successfully. |
| T-SEC-004 | COVERED | Direct test: Manager cannot manage users (role matrix). |
| T-SEC-005 | COVERED | User without `post_sales` rejected in the service. |
| T-SEC-006 | COVERED | Phase 2 role-change audit. |
| T-SEC-007 | COVERED | Phase 2 disabled-user login denial. |
| T-SEC-008 | LATER (Phase 9) | Audit-log viewing is Phase 9 security work, not Phase 6. |

**Roadmap reconciliation — `T-DB-001/002`:** these are not Phase 6 tests. Their definitions exercise quotation conversion and `source_quotation_id`, which belong to Phase 7; they remain Phase 7 exit tests (`T-DB-001/002/010/011`). The Phase 6 test list in `docs/11_IMPLEMENTATION_ROADMAP.md` has been corrected to remove them, so they are no longer presented as Phase 6 N/A cases.

Still implementable and missing for the applicable Phase 6 scope: **none**. No Phase 6 exit blocker remains.

Note: `T-DB-003/004/005/006/007/008/009/012` are inventory/accounting DB invariants from Phases 1/4/5, not Phase 6 exit cases. Phase 6 sales allocations use `lot_allocations` for LOT and `reel_allocations` for REEL; the service never produces a mixed allocation for one sales line.

## Phase 6 gate classification

| Remaining item | Class | Basis |
|---|---|---|
| `OQ-INV-012` / `T-RET-013`, `T-INV-026` | Resolved — 2026-11-17 | New returned reel/layer linked to original reel/allocation; original reel not increased; `T-RET-013` and updated `T-INV-026` covered. |
| `OQ-INV-004` / `T-RET-002`, `T-RET-017` | Resolved — 2026-11-17 | Damaged flow + direct loss `5020/5010`; both accounting assertions covered. |
| `OQ-INV-007` / `ADJUSTMENT_IN` | 2. Deferred feature | Not in the Phase 6 dependencies, outputs, exit criteria, or test list; `OQ-INV-007` is PROPOSED and its cost basis is OPEN. Not a Phase 6 exit blocker. |
| `OQ-SAL-003` / mixed or partial cash | 2. Deferred feature | Phase 6 exit criteria/tests do not require mixed/partial cash; the approved recommendation assumes cash = full payment and the service rejects `MIXED`. Not a Phase 6 exit blocker. |
| `T-DB-001/002` | 3. Later-phase test — now covered in Phase 7 partial | Quotation conversion/`source_quotation_id`, owned by Phase 7. Removed from the Phase 6 test list; covered headlessly by the Phase 7 tests. |
| `T-SEC-008` | 3. Later-phase requirement | Audit-log viewing is Phase 9. |
| Phase 7 gate | 2. In progress — partial | Phase 6 gate passed 2026-11-17. Phase 7 renderer-independent work is implemented/tested; `OQ-QUO-001/005` and `OQ-DOC-001` were resolved 2026-11-17. `ADR-0004` remains PROPOSED / NEEDS APPROVAL and `OQ-DOC-002` remains open, so the full Phase 7 gate has not passed and Phase 8 remains blocked. |

## Resolved Phase 6 decisions

### OQ-INV-012 — returned reel identity (approved 2026-11-17)

Every returned length creates a new `RETURN` lot/reel/layer. `reels.source_reel_id` and `reels.source_issue_allocation_id` link it explicitly to the original reel and original sale allocation; the original reel/layer is never increased or reopened; over-return and duplicate-return protections remain. Covered by `T-RET-013`, `T-INV-026`, and the reel reversal test. Recorded in `docs/DECISIONS_APPROVED.md`.

### OQ-INV-004 — damaged-return accounting (approved 2026-11-17)

Keep `SALE_RETURN_IN` then `DAMAGE` in one transaction; post the captured damaged cost as direct loss: debit `5020`, credit `5010`. Do not use `1210` or a disposal workflow; commercial settlement behavior is unchanged. Covered by `T-RET-002/017` and the damaged reel case in `T-INV-026`. Recorded in `docs/DECISIONS_APPROVED.md`.

- Deviations / implementation notes:
  - Reversal keeps `sales.status='POSTED'` and records `reversal_journal_entry_id`/`reversed_at`; the original journal entry becomes `REVERSED`. No stored `REVERSED` sales status was added.
  - One settlement split is stored on `sales_returns` instead of a separate `sales_return_settlements` table.
  - `sales.paid_piastres`/`balance_piastres` are not stored; invoice/customer balances are derived from posted receipt allocations, return AR reductions and credit applications to avoid a second source of truth.
  - Return allocation selections are persisted in `sales_return_line_allocations`; the default order is original allocation `id DESC` (a deterministic proxy for consumption order), not a stored consumption timestamp.
  - Entering a line price when no reference price exists is accepted as `MANUAL` without `edit_sale_price`; a price that differs from an existing customer/list/default reference requires `edit_sale_price` plus a reason.
  - `MIXED` payment is present in the schema but rejected by the service because `OQ-SAL-003` partial/mixed cash payment is not approved.
  - Damaged-return cost is reclassified from `5010` to `5020` (`5020` debit / `5010` credit); no `1210` or disposal workflow. `damaged_cost_accounting` is `PENDING` on a damaged draft and `POSTED_5020_5010` after posting.
  - Reel returns create a new `RETURN` lot and a new reel per returned piece; `source_reel_id`/`source_issue_allocation_id` preserve the link; the original reel/layer is not modified. Reel invoice reversal uses the same mechanism.
  - No failed-attempt audit record is written; `T-RB-002` does not assume failed-attempt audit records roll back.
  - `customer_statement` is an AR/customer-credit statement: it lists 1100 and 2020 customer lines; cash-only documents with no 1100/2020 line are not AR statement lines. Credit-application lines have no formal document number; their `source_type`/`source_id` identify the application.
  - `sales_lines.project_id` is stored, but the sales journal tags AR/revenue with the invoice-level project; per-line project journal splitting and project-dimensioned returns are deferred to the reporting phase.
- Resolved decisions: `OQ-INV-012` and `OQ-INV-004` (see above; recorded in `docs/DECISIONS_APPROVED.md`).
- Deferred / open decisions:
  - `OQ-INV-007` `ADJUSTMENT_IN` — **deferred feature; not a Phase 6 exit blocker.** The posting service remains unimplemented because the cost basis is OPEN (explicit/FIFO/other/zero); no basis was invented. It is not in the Phase 6 dependencies, outputs, exit criteria, or test list.
  - `OQ-SAL-003` mixed/partial cash payment — **deferred feature; not a Phase 6 exit blocker.** The approved recommendation assumes cash = full payment; `MIXED` is rejected at the service boundary. It is not required by Phase 6 exit tests.
  - Phase 7: **IN PROGRESS — PARTIAL.** Phase 6 gate passed 2026-11-17. Renderer-independent quotation lifecycle/status history, revisions/root links, conversion + DB duplicate guard, cancellation/restoration, numbering, and snapshot/export model are implemented and tested. `OQ-QUO-001/005` and `OQ-DOC-001` were resolved 2026-11-17 by adopting the documented recommendations. `ADR-0004` remains **PROPOSED / NEEDS APPROVAL** and `OQ-DOC-002` remains open/deferred (logo/font; no font bundled).
  - A4/PDF/UI/real-printer checks remain deferred for Phase 7: A4 dimensions, Arabic shaping, PDF text extraction, physical printer output, print scaling/alignment, and logo/font snapshot behaviour.
- True Phase 6 exit blockers: **none**.
- Deferred features, not Phase 6 exit blockers: `OQ-INV-007` (`ADJUSTMENT_IN`) and `OQ-SAL-003` (mixed/partial cash).
- Later-phase items: `T-DB-001/002` are Phase 7 quotation-schema tests and are now covered headlessly; `T-SEC-008` is Phase 9 audit-log work. Phase 7 is in progress/partial, not complete.
- Next: resolve `ADR-0004` (A4/PDF engine) and `OQ-DOC-002` (logo/font), then implement/validate official PDF export, PDF text extraction, A4 dimensions, and a real A4 printer. Phase 8 must not start until the full Phase 7 gate passes. `OQ-INV-007` and `OQ-SAL-003` remain deferred.
