# Progress

## Phase 10 — desktop application shell (started 2026-11-18)

- **Data root and bootstrap:** `mizan/config.py` defines `AppPaths` (database, managed documents, backups and logs under one root; default `%LOCALAPPDATA%\Mizan`, overridable with `MIZAN_DATA_DIR` or `--data-dir`). `mizan/bootstrap.py` creates the root, applies migrations, seeds the chart of accounts and the permission catalogue, wires the backup/schedule/audit services and runs the startup check before any screen. `initialize_first_run` creates the store profile and the single Owner account once (minimum 8-character password), records `STORE_INITIALIZED`, and refuses a second initialization.
- **Application entry point:** `mizan/app.py` / `python -m mizan` / the `mizan` GUI script start the Qt application, show a clear error dialog when the data root cannot be opened, and open the main window. `pyproject.toml` declares the GUI script for Phase 10 packaging.
- **Arabic RTL shell:** `mizan/ui/theme.py` configures one `QApplication` (Fusion, RTL, the bundled Noto Naskh Arabic UI font, light palette and stylesheet). `MainWindow` shows the login/first-run screen, then a shell with a sidebar and a permission-filtered navigation: لوحة التشغيل (`view_dashboard`), النسخ الاحتياطي (`backup_database`), سجل التدقيق (`view_audit_log`) and سلامة البيانات (`backup_database`). Cashier accounts see only the dashboard; logout returns to login.
- **Pages:** dashboard (active products/customers/suppliers, posted sales, open quotations, cash balance); master data (units, products with LOT/REEL tracking and deactivation, customers with RETAIL/WHOLESALE/CONTRACTOR types, suppliers, projects restricted to contractor customers — each tab appears only with its `manage_*` permission); backup page (daily/monthly backup through `BackgroundJob` with progress and cancellation, verification, off-device copy, restore from directory or encrypted file requiring a reason, optional recovery-key file); audit viewer (event/date/limit filters, pagination, read-only table); diagnostics (database integrity, foreign keys, schema state, per-document checks and the error list, with a re-run button).
- **Permission alignment:** the Manager role now holds `backup_database` as the docs/08 matrix states (the code previously excluded it); `restore_database` remains Owner-only. The backup page disables restore for Manager.
- **Restore progress:** `BackupService.restore` now accepts a `progress` callback (`verify`/`copy`/`publish` stages) so the UI can report progress and cancel before publication.
- **Test stability:** `tests/conftest.py` provides one session `QApplication` created before any `QGuiApplication` (offscreen only on non-Windows). The UI tests explicitly dispose their top-level widgets on the GUI thread; otherwise cyclic GC could finalise Qt widgets inside a backup worker thread and crash the process.
- Validation: **24 new tests** — `test_phase10_bootstrap.py` (9: paths, schema/seed data, first run, five validation cases, startup report) and `test_phase10_ui.py` (15: theme, first-run shell, login, permission navigation/logout, dashboard, master data for units/products/customers/suppliers/projects, audit filters, diagnostics, backup/verify, encrypted backup, offsite copy, restore permissions/reason). **Full suite after the shell: 238 passed, 1 skipped** (the skip remains the deferred physical A4 printer check; the Phase 7 rendering/golden tests pass unchanged under the widget `QApplication`).
- **Still open in Phase 10:** pricing screens (default prices, price lists, customer prices), purchasing, sales/returns, quotations, reports and settings screens; a full store-settings wizard; installer/PyInstaller build, signing and clean-machine acceptance; and a physical-device backup/restore check. The shell plus master data is a working foundation, not a finished V1 UI.

## Phase 9 — backup, restore, audit, startup checks (started 2026-11-18)

- **Backup foundation (2026-11-18):** `BackupService.create/verify/restore` uses SQLite Online Backup, SHA-256 and size manifests, database integrity/foreign-key/schema checks, and copied-file revalidation. Bundles contain registered document exports and snapshot logo assets; unrelated and temporary files are excluded.
- Creation and restoration publish a verified staging directory to a new destination. Existing destinations and destinations within managed documents are rejected. The active database and previous backups are preserved. Restored data can be opened independently and accepts new posting.
- Existing backup/restore permissions are enforced; restoration requires a reason. Backup success/failure and successful restoration are audited in the authorizing database.
- **Encrypted bundles:** `backup_encryption.py` seals a verified bundle into a single AES-256-GCM file with AAD-bound magic/nonce, streaming zip payload, authentication tag, strict member-name checks and `xb` destination creation. A missing, wrong or damaged key fails restore before publication. Keys are generated by the operator and kept off the backup medium (`OQ-BKP-002`; `docs/BACKUP_RESTORE.md`).
- **Scheduled backups and retention:** `ScheduledBackups.run` writes `<root>/daily/YYYY-MM-DD[.mizan]` and copies that verified bundle to `<root>/monthly/YYYY-MM[.mizan]`, taking one database snapshot per run and never overwriting an existing bundle. `prune` deletes only the oldest bundles beyond `RetentionPolicy` (default 30 daily / 12 monthly, ADR-0005 §3.4) after verifying the newest bundle in that category; encrypted bundles cannot be pruned without their key. `pre-upgrade` bundles are never pruned automatically.
- **Off-device copy:** `BackupService.copy_to` verifies the source, copies to a staging path, verifies the copy again and publishes it atomically, so an interrupted copy leaves no destination. Copies are recorded as `BACKUP_COPIED`/`BACKUP_COPY_FAILED` (`FR-SYS-018`).
- **Audit access and protection (T-SEC-008):** `view_audit_log` was added to the permission catalogue for Owner/Manager (Cashier denied). `AuditService.query` supports event/entity/actor/date filters with limit/offset and returns decoded before/after payloads. Migration 10 makes `audit_events` append-only at the database level (UPDATE/DELETE triggers) and adds event-type/actor indexes.
- **Startup check:** `diagnostics.startup_check` reports database integrity, foreign-key state, schema state (current/outdated/newer/unknown) and per-document `exists/size/SHA-256` against export/snapshot metadata, plus stale temporary renders. Missing or mismatched official documents are reported, never regenerated.
- **Failure/interruption coverage:** interrupted off-device copies leave no destination and are audited; a progress callback can cancel a backup before publication with no partial bundle; pruning refuses when the newest bundle is corrupt; restore still rechecks staged copies.
- **UI readiness:** `progress(stage)` hooks on `create`/`copy_to`/`restore`/`run` plus `mizan.background.BackgroundJob` (threaded execution, stage updates, cooperative cancellation, captured failure state) give the desktop shell progress and cancellation points without freezing the caller. The shell now uses them on the backup page (see the Phase 10 entry); the "no freeze" claim is UI-wired but still not measured on a physical click-through.
- Validation: **40 passed** in the Phase 9 files (`test_phase9_backup.py` 12, `test_phase9_audit.py` 6, `test_phase9_diagnostics.py` 8, `test_phase9_retention.py` 10, `test_phase9_background.py` 4) with the bundled Python 3.12.14 runtime and the project's installed dependencies. **Full suite: 214 passed, 1 skipped** (the only skip is the deferred physical A4 printer check `T-DOC-009`). The project-local venv was also repaired for local runs: `cryptography` 50.0.2 plus `cffi`/`_cffi_backend` were copied from the local runtime into the git-ignored `.venv` because PyPI was unreachable, and the same 16 backup/background tests passed under it. Use an in-workspace pytest `--basetemp` (e.g. `artifacts/…`) with the Windows Store venv Python, because it redirects `%TEMP%` and cannot create a temp basetemp outside the project.
- **Still open in Phase 9:** restore on a genuinely separate device (`T-BKP-003` manual acceptance), RPO/RTO scheduling and off-device frequency (`OQ-BKP-003`), audit-retention duration (`OQ-SEC-003`, currently retained permanently), and the deferred physical A4 printer check from Phase 7. UI wiring is now done in the Phase 10 shell.
- **Known limitation — backup/schema compatibility:** `verify`/`restore` require the backup's `schema_migrations` to match the running application exactly, so a backup taken by an older application schema (for example the pre-Phase-9 v9 schema) is rejected after an upgrade. Restoring therefore works with the same application version; upgrade-on-restore and versioned upgrade/rollback policy belong to the Phase 10 migration work (`OQ-SYS-003`). This is intentional for now (no silent schema upgrade inside restore) and is documented in `docs/BACKUP_RESTORE.md`.

## Phase 8 — reports and reconciliation (started 2026-11-18)

- Integrated acceptance review (2026-11-18): added `tests/test_phase8_acceptance.py`, covering opening cash, lot and reel purchases, a credit sale, a damaged return, an allocated receipt, a supplier payment, an excluded draft, monthly close and a subsequent-month sale. Independently calculated balances reconcile across aging, party statements, financial activity, profitability, inventory valuation and current layers; the entire historical report snapshot remains unchanged after the later sale.
- Fixed monthly closing to require the complete party reconciliation result (`matched`), including correct party assignment. Offsetting unassigned customer or supplier lines can have zero net difference but must still prevent closing. Both cases now have regression tests that also verify no lock or close audit event is committed after rejection.
- Focused acceptance and period tests: **5 passed** using the bundled Python runtime and existing project dependencies. The project-local venv launcher currently points at a Windows Store Python executable that could not start in this environment; no dependency or system Python changes were made.
- Full regression after this review: **174 passed, 1 skipped** in 409.65 seconds. The only skip is the deferred physical A4 print check (`T-DOC-009`). Run with the bundled Python 3.12 runtime, `PYTHONPATH=.venv/Lib/site-packages;.` and `-m pytest -q --basetemp=artifacts/pytest-full-review`.

- The owner authorized continuing development while `T-DOC-009` is deferred. Phase 7 acceptance remains open until the paper output is inspected.
- `AccountingService.trial_balance(as_of=...)` returns cumulative debit and credit movements through the inclusive journal date in integer piastres. It includes zero-movement accounts and accounts for both original and reversal entries. Calling it without a cutoff retains the existing cumulative result.
- `AccountingService.reconcile_parties(as_of=...)` compares 1100 and 2010 with customer- and supplier-tagged journal balances at the same date, in their respective debit and credit signs. Results include the per-party balances, difference and number of incorrectly or incompletely tagged lines. Offsetting unassigned entries still produce a failed match.
- `AccountingService.reconcile_inventory()` compares current 1200 with the current sellable cost layers and reports the difference. Each reel layer contributes its remaining cost once. This comparison uses current inventory state because cost layers store current remaining amounts.
- Focused accounting and purchasing suite: **30 passed**. Full suite: **153 passed, 1 skipped** (physical print check), using Python 3.12.14 with the installed project dependencies and a short pytest base temp path.
- `ReportingService(db).customer_receivables_aging(as_of, customer_id=...)` ages posted credit invoices as of an inclusive journal date. It uses the due date or falls back to the invoice date. Buckets are not yet due, 0–30, 31–60, 61–90 and over 90 days. The customer filter is optional.
- The aging report applies posted receipt allocations, the receivable portion of returns, applied customer credits and sale reversals at their journal dates. It shows each invoice reference, the bucket totals, account 1100 and the ledger difference in integer piastres. Negative invoice balances appear in the detail without a bucket. Ledger balances without an open invoice appear in the difference.
- The aging and related accounting/sales regression selection passed (17 tests). Both aging tests also passed on the final version with blank due-date coverage.
- `ReportingService(db).profitability(from_date, through_date)` uses inclusive journal dates. It shows net sales (4010 less 4020), gross profit after 5010 and operating profit after 51xx expenses. Other income and expenses are shown separately and included in the final result, including inventory losses on 5020. The account breakdown includes the period's debit and credit totals. A reversal affects its own journal period.
- Profitability, aging and accounting regression selection: **12 passed**, including damaged returns, a sale reversed in a later period, and the tie from net result to period journal totals.
- `ReportingService(db).supplier_payables_aging(as_of, supplier_id=...)` ages posted credit purchases by due date or invoice date. It deducts linked posted returns and payment allocations through the cutoff and excludes third-party purchase expenses from the opening invoice amount. Ledger movements without invoice allocations remain visible in the difference from account 2010.
- `ReportingService(db).supplier_statement(supplier_id, as_of=...)` shows purchases, returns, and other 2010 movements with document or journal references and a running credit-positive balance. `PurchasingService.create_invoice(..., due_date=...)` now accepts a due date through the normal invoice entry point.
- Purchasing and reporting regression selection: **27 passed**, including a posted return, a payment without invoice allocation, a draft purchase, and a report at the due-date boundary.
- `PurchasingService.post_supplier_payment(..., reference=..., actor_id=...)` records a fully allocated payment in one transaction. The existing `manage_supplier_payments` permission is required. The supplier reference is unique per supplier, allocation is limited by the outstanding amounts of the posted credit invoices and supplier, and the journal debits 2010 and credits the selected financial account. Migration 8 stores immutable payments and allocations. The supplier statement shows the payment document number.
- Supplier payment, migration and reporting regression selection: **34 passed**. The separate database run also verified upgrading a Version 7 database with existing data (**6 passed**).
- `ReportingService(db).inventory_valuation(as_of)` reconstructs quantities in each product's base unit and costs in piastres from the signed stock movements through the selected date. It groups by product and warehouse and compares the total with account 1200 from the same database snapshot. The cutoff uses the recorded calendar date of both movements and journal entries, including entries with a time component.
- `ReportingService(db).inventory_movements(from_date, through_date, product_id=...)` lists the supporting receipts, issues, returns and damage, with source references and lot or reel IDs when stored on the movement. Historical valuation and reporting regression selection: **31 passed**, including a stock-only outflow that produces an explicit reconciliation difference.
- `ReportingService(db).sales_detail(from_date, through_date, customer_id=..., product_id=..., posted_by=...)` shows posted invoice, return and sale reversal lines using inclusive journal dates. It uses frozen invoice values after discounts and the posted FIFO/reel costs. Results include source documents, journal numbers and separate totals for sales, returns, reversals, net sales and cost of sales.
- `ReportingService(db).purchase_detail(from_date, through_date, supplier_id=..., product_id=...)` shows posted purchase and return lines. Supplier invoice price, allocated expenses and landed costs are distinct. Supplier returns use posted inventory cost and reduce the net inventory acquisition total.
- Purchasing and Phase 8 reporting regression selection: **33 passed**, including the sales-to-profitability and purchase-to-inventory control checks.
- `ReportingService(db).financial_activity(from_date, through_date, financial_account_id=...)` shows opening balance, period debit and credit movements with references, running balances and closing balance. It groups by linked GL account from one read snapshot. When several financial accounts share a GL account, the report shows the combined ledger balance once and lists the linked accounts because some historical journal lines record only the GL account.
- Accounting and Phase 8 reporting regression selection: **20 passed**, including period boundaries, internal transfers, supplier payments, cash sale reversals, inactive periods and shared GL account mapping.
- `ReportingService(db).fifo_lots(product_id=..., warehouse_id=...)` shows original supplier and landed costs, original and remaining quantity, remaining layer cost and the source document for each lot. The lot totals include any associated reel layers.
- `ReportingService(db).reel_balances(product_id=..., warehouse_id=...)` lists the reel number, state, original and remaining length and cost, and the reel's own inbound movement. Returned reels also show the source reel and issue allocation. Both reports show current balances, including depleted lots and reels.
- Reporting and reel-return regression selection: **13 passed**. The three inventory and reel-return tests passed again after verifying distinct inbound references for reels from the same purchase.
- `ReportingService(db).project_balances(as_of, customer_id=...)` shows contractor net sales, cash sales, invoice-allocated receipts and outstanding receivables by project from posted documents. Sales and return amounts follow the project stored on each sale line. Receipts, credit applications and receivable reductions are linked to a single-project invoice when the allocation has one project. Invoice-level settlements on split invoices stay in the contractor's unassigned group, with their references. Unapplied customer credits and direct ledger adjustments are outside this document-based report.
- Phase 8 and contractor sales regression selection: **14 passed**, including two projects sharing one invoice and reconciliation of assigned plus unassigned receivables to the contractor's account.
- `ReportingService(db).income_expense_detail(from_date, through_date, account_type=..., account_code=...)` lists posted income and expense journal lines, debit and credit amounts, account subtotals, source references and the net result in integer piastres. Revenue uses credit less debit and expenses use debit less credit. Both this report and the profitability summary include timestamped entries on the final calendar date of the period.
- Focused profitability and inventory selection: **4 passed**, including a return, a later reversal and a timestamped stock damage expense that ties to the profitability result.
- Full suite after the reporting changes: **168 passed, 1 skipped**. The deferred physical A4 printing check was the only skip.
- `ReportingService(db).customer_period_statement(customer_id, from_date, through_date)` and `supplier_period_statement(supplier_id, from_date, through_date)` return opening balances, movements, running balances and closing balances for inclusive date ranges. Receivables (1100) use debit-positive balances; customer credit (2020) and payables (2010) use credit-positive balances. The two customer accounts are shown independently. Document references resolve through the original journal for reversals.
- Phase 8 and customer statement regression selection: **15 passed**, including an empty period, a customer credit application and a supplier payment reversal.
- `AccountingService(db).balance_sheet(as_of=...)` groups the recorded balances into assets, liabilities and equity through the inclusive cutoff. It adds the cumulative net balance still in revenue and expense accounts to posted equity and reports the difference between assets and total claims. If a posted journal transfers a result to equity, the report reflects both sides of that journal without counting the result twice. This is a ledger statement; it does not perform a fiscal close.
- Accounting and Phase 8 regression selection: **22 passed**, including timestamped cutoff entries and balance before and after a posted closing adjustment. A separate synthetic ledger test verified a balanced result across **10,000 journal entries / 20,000 lines**, with a 0.048 s one-off report measurement on this environment.
- `PeriodService(db).close_month(month, actor_id=..., reason=...)` checks the ledger and the 1100/2010 and dated 1200 control balances before locking a month. It uses the existing Owner settings permission. Lock and reopen transitions record the actor and reason in the audit log. Dates in a locked month are protected by the services and SQLite migration 9 for journals, journal lines and stock movements. Reversals can post in an open period and keep the original closed-period values. Locks apply to the specified months; the workflow does not automatically close other months or generate year-end entries.
- Full suite with monthly posting locks: **171 passed, 1 skipped**. The deferred physical A4 printing check was the only skip.
- Phase 8 acceptance review is complete (2026-11-18, see the first Phase 8 entry): the integrated scenario reconciles every report, and monthly closing requires a complete party reconciliation. Year-end closing entries and final financial-statement configuration remain separate work, not part of monthly posting locks.

## Status through Phase 7

- Phase: **7 in progress — partial.** Phases 1–6 are complete. Phase 7's quotation core, the approved ADR-0004 Option B A4 renderer, the approved Noto Naskh Arabic font, and logo snapshot/hash/reprint stability are implemented and tested. The owner approved the reviewed quotation template for V1 without a logo (option 1A, 2026-11-17), and T-DOC-007 is covered by a deterministic golden reference. Phase 7 is **not complete**: T-DOC-009 (real A4 printer) remains open. The owner authorized Phase 8 development on 2026-11-18 while the physical print check is deferred until a printer is available.
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

**Status: IN PROGRESS — PARTIAL. Quotation core, the approved A4 rendering slice, the approved Noto Naskh Arabic font, and the owner-approved V1 template (no logo) are implemented and tested; T-DOC-007 is covered by a deterministic golden reference. T-DOC-009 (real A4 printer) remains open. Phase 8 development is authorized while the print check is pending.**

### Resolved decisions applied

- **`OQ-QUO-001` — conversion-draft cancellation/restoration:** `status_before_conversion` is recorded in `quotation_status_history`. Cancelling a conversion draft restores the saved status only when the quotation has not expired; an expired quotation stays expired and a new conversion requires `override_expired_quotation` and a reason.
- **`OQ-QUO-005` — cancellation policy:** `DRAFT` and `ISSUED` quotations can be cancelled with `cancel_quotation` and a recorded reason; an `ACCEPTED` quotation cannot be cancelled; `REJECTED`/`CANCELLED`/`SUPERSEDED`/`CONVERTED` are final.
- **`OQ-DOC-001` — document numbering:** `{TYPE}-{YYYY}-{NNNNNN}` per type/year from a database sequence; committed numbers are unique and not reused; gaps are allowed only on transaction failure. Quotation roots use `Q-YYYY-NNNNNN`; revisions add `-R{version_no}`.
- **`ADR-0004` — approved 2026-11-17:** Option B, `QTextDocument` + `QTextTable` with a custom paginator, using Qt for PDF (`QPdfWriter`) and printing (`QPrinter`). Runtime dependency is `PySide6-Essentials`; no Qt WebEngine, no separate PDF library.
- **`OQ-DOC-002` — resolved for V1 (2026-11-17):** Noto Naskh Arabic 2.000 (OFL 1.1) is approved and bundled; the approved quotation template has **no logo**, and no logo/branding asset was added. Logo support may be reconsidered in a later version.

### Implemented (quotation core)

- Migration 7 adds `quotation_series`, `quotations`, `quotation_lines`, `quotation_status_history`, `document_exports`, `document_snapshots`, `sales.source_quotation_id`, the partial unique index `ux_sales_source_quotation_active`, and immutability/root/issuance triggers.
- `QuotationService` covers create/edit draft, line snapshots, price resolution and manual-price permission/reason, item/invoice discounts with largest remainder, issue/accept/reject/cancel, revisions and supersession, expiry computation (last day valid), conversion to a sales `DRAFT`, expired-conversion override, and conversion-draft cancellation/restoration.
- Revision guard: no revision while an active (non-cancelled) sale exists in the series; only the latest version can be revised. Creating a revision does not supersede the previous version; issuing the revision does, in the same transaction.
- Conversion copies the quotation's frozen quantities/prices/discounts and line snapshots, creates the sales `DRAFT` only, performs an advisory stock check without reservation, and never creates inventory movements or journals.
- Duplicate-conversion prevention is enforced by `ux_sales_source_quotation_active` and tested directly against SQLite as well as through the service. Cancelling the draft releases the index.
- Sale posting marks the quotation `CONVERTED` in the same transaction; a failed posting leaves it unchanged. Cancelling a conversion draft leaves the sale row cancelled (no delete) and restores the quotation status per `OQ-QUO-001`.
- Quotation-generated sales drafts lock commercial fields; `update_draft_header` allows payment terms/account/due date/invoice date changes where the sales rules allow.
- Shared `pricing.py` and `inventory.available_stock` prevent quotation/sales logic from drifting.

### Implemented (approved A4 rendering slice)

- `mizan/rendering.py`: shared `A4QuotationRenderer` using `QTextDocument` + `QTextTable`, a focused paginator, repeated table headers, whole-row page breaks, RTL layout, EGP/quantity formatting, footer page numbers, and the same page composer for `QPdfWriter` and `QPrinter`.
- `mizan/document_store.py`: managed documents root, atomic install, path containment, SHA-256 calculation/verification, and explicit missing-file/hash-mismatch errors. A missing official file is never silently regenerated.
- `mizan/quotation_export.py`: official quotation issue renders from the quotation snapshot content, installs the PDF atomically, records the official export inside the same transaction as `ISSUED` and the immutable snapshot, verifies that the quotation did not change during rendering, and supports reprint and shared-layout printing.
- `DocumentService.record_export_in_conn` lets the official export row and quotation status commit/rollback together.
- The renderer captures store/customer/project/line/totals data from the immutable snapshot; historical reprints use the stored PDF and hash.

### Verified rendering results

- PDF pages have A4 MediaBox 595×842pt (±1) and page counts match the paginator.
- Multi-page PDFs repeat the table header; the page plans partition all line rows exactly once and keep rows whole (no row is split across pages in the tested plans).
- Arabic text is extractable from the generated PDF after NFKC normalization, including `عرض سعر`, store/customer, line descriptions, notes/terms, and totals. The PDF uses the bundled Noto Naskh Arabic font; text extraction passes with `rlig` disabled because Qt 6.12 does not map some required-ligature glyphs in PDF ToUnicode.
- The same snapshot produces the same page plans and the same extracted text on repeat renders.
- Official export records path, SHA-256, file size, template/renderer versions and `is_official=1`; reprint returns the same file/hash; deleting the file makes reprint and print fail without regeneration.
- A `QPrinter` PDF-output test confirms the printer path uses the same page composer/layout; it is not a physical-printer test.
- Per-line table discounts/net amounts use piastres formatting and reconcile with the invoice summary; unit prices remain micro-EGP and quantities remain micro-units.

### Line money formatting fix (2026-11-17)

The review preview showed `0.00`/`0.01` in the table's discount/net columns while the summary was correct. Verified data units before editing: `quotation_lines.final_unit_price_micros` is micro-EGP, while `item_discount_piastres` and `net_piastres` are piastres. The table had passed all three values through `format_unit_price()`, which divides by 1,000,000.

Fix: added `format_egp_amount()` (piastres → two-decimal EGP amount without suffix) and used it for line discount/net cells; `format_unit_price()` remains for unit prices and `format_quantity()` for quantities. The column headers already say `(EGP)`, so table cells stay uncluttered and pagination is unchanged.

Regression tests in `tests/test_phase7_line_amounts.py` cover line 1 net `12.50`, line 2 discount/net `1.00 / 21.13`, lines 21 and 42 net `52.00`, and reconciliation of line discounts/nets with the invoice summary. The re-rendered synthetic review quotation keeps 3 pages with 20/20/5 rows; its line 42 shows `51.00` because that preview row carries a `1.00` discount. The preview remains review-only, not a golden reference.

### Summary direction fix and separator verification (2026-11-17)

- **Confirmed defect:** in the RTL totals block, `1,281.41 EGP` was reordered to `EGP 1,281.41`; the amount resolved as an Arabic-context number while the Latin `EGP` token drifted to the far left, so the currency read as a prefix and the extracted order was unstable.
- **Fix:** `_totals_line()` in `mizan/rendering.py` inserts `<label>: <amount> EGP` with U+200E left-to-right marks around the money token so it renders as one LTR run. The table is unchanged: cells stay bare numbers under the `(EGP)` column headers, and no currency suffix is duplicated in cells.
- **Verified:** extracted text keeps `<amount> EGP` and never `EGP <amount>`; no bidi control characters reach the copyable text; pages 1–2 of the 45-line preview are identical to the baseline after normalization; numeric tokens are identical on all three pages; pagination stays 20/20/5.
- **Separator investigation:** the PDF text layer uses ASCII `.` everywhere (`28.88` present, `28,88` absent, no U+066B/U+066C, no Arabic-Indic digits). Rendering the same cells at 96 dpi reproduces the comma-like appearance — the Tahoma 8.2 pt period is about one pixel and anti-aliasing merges it with adjacent digits — while at 300 dpi it is a distinct dot. The cause is raster resolution, not formatting, locale, or localized substitution: `locl=0` was **not** added, and no font, size, or money format was changed. Review page images are now rendered at 300 dpi grayscale.
- **Versions:** `RENDERER_VERSION` bumped to `PySide6-QTextDocument-QTextTable-Paginator/3`; `TEMPLATE_VERSION` stays `A4-QUOTATION-V1` because the template elements, column order, and pagination are unchanged and the template is still awaiting owner approval.
- **Review artifacts:** candidate PDF/page images are `artifacts/phase7-review/mizan-phase7-review-candidate.pdf` and `page-0N-candidate.png`; the previous preview is retained as the baseline. These are untracked review artifacts and are not committed.

### Owner approval of the V1 template and T-DOC-007 golden (2026-11-17)

- **Owner decision (option 1A):** the reviewed quotation template is approved for V1 **without a logo**. Noto Naskh Arabic remains the approved font. V1 quotation documents have no logo; logo support/assets may be reconsidered in a later version, and no logo or branding asset was added.
- **Golden reference:** `tests/golden/quotation-a4-v1.json` is generated from the approved 45-line synthetic preview layout (`REVIEW-Q-0001`, 3 pages, 20/20/5 rows, totals `1,281.41 / 90.00 / 0.00 / 1,191.41`). It is a deterministic content/layout manifest: template and renderer versions, A4 geometry, page plan, and canonical extracted text per page. `tests/test_phase7_golden.py` verifies the exact match, determinism across two renders, and font ownership of money glyphs.
- **Why not a pixel golden:** per-font attribution of the generated PDF shows the approved Noto font renders all Arabic text, ASCII digits, and the `.`/`,` separators, but Latin letters and punctuation (`EGP`, document codes, dashes, parentheses) are absent from that font and fall back to the Windows Tahoma system font. A pixel or PDF-hash reference would therefore depend on an unapproved system font; the golden deliberately compares content/layout instead. A pixel golden becomes possible only if an approved Latin font is added.
- **Fixture:** synthetic data only; no database, clock, filesystem, or random input; 7 KB manifest.

### Approved Arabic font — Noto Naskh Arabic (2026-11-17)

- **Source/version/license:** Noto Naskh Arabic 2.000, SIL OFL 1.1, pinned upstream commit `8386d579f0fc5064dc8b66c6b5a9382f3822b82d` (`notofonts/NotoNaskhArabic`). Regular and Bold hinted TTF instances are bundled at `mizan/assets/fonts/NotoNaskhArabic/` with the upstream `LICENCE` and a `SOURCE.md` containing source URLs, version, and SHA-256 hashes.
- **Loading:** the renderer loads the faces with `importlib.resources.files("mizan")` and `QFontDatabase.addApplicationFont`; it does not rely on the development working directory or a system Arabic font.
- **Embedding:** generated PDFs embed Noto Naskh Arabic; `tests/test_phase7_fonts.py` verifies the pinned package hashes, OFL license text, Qt family loading, PDF font names, FontFile presence, ToUnicode, and Arabic extraction.
- **Text extraction:** required ligatures (`rlig`) are disabled for PDF output because Qt 6.12 leaves some Noto ligature glyphs out of ToUnicode. Arabic contextual shaping (init/medi/fina) remains; this keeps PDF text searchable/copyable.
- **Packaging:** `pyproject.toml` package data and `MANIFEST.in` include the font assets; an installed package target was verified to load the font and render an embedded-font PDF. PyInstaller/executable packaging remains Phase 10. See [packaging notes](PACKAGING_NOTES.md).
- **Known boundary:** Noto Naskh Arabic contains the Arabic script, ASCII digits, and the `.`/`,`/`:` separators, but not Latin letters or all punctuation; Qt falls back to the Windows Tahoma font for `EGP`, document codes, dashes, and parentheses. The approved font is embedded and licensed (T-DOC-005); the golden reference therefore compares content/layout rather than glyph pixels.
- **Not changed:** no logo, UI, golden image, template approval, or physical-printer claim was added.

### Long-document / performance check (2026-11-17)

Measured with the project-local `.venv` on this Windows machine (Python 3.12.10, PySide6-Essentials 6.12.0), three runs per size. Times are descriptive, not pass/fail thresholds; `render` is `render_pdf` (pagination + PDF writing) in seconds.

| Line items | Pages | Rows/page | Median paginate | Median render | PDF size |
|---:|---:|---|---:|---:|---:|
| 100 | 6 | 20/20/20/20/19/1 | 1.80s | 1.63s | 49.1 KB |
| 250 | 13 | 20×12, 10 | 2.46s | 2.30s | 74.9 KB |

Correctness on both sizes: every line appears exactly once in the page plans, no rows are lost, each page has A4 MediaBox 595×842pt (±1), and the repeated table header/footer appear once per page. The new 120-line regression test asserts the same invariants deterministically. Measurements above use the approved Noto Naskh Arabic font; its taller line height yields 20 rows/page.

Concrete defects found and fixed in this check:

- **Serial-column wrap:** the line-number column was too narrow (5%), so 3-digit serials began wrapping in the earlier system-font rendering, row heights increased, and rows/page dropped. Widening the serial column to 7% keeps 3-digit serials on one line; with Noto the documents paginate at 20 rows/page.
- **Pagination cost:** the previous paginator rebuilt the entire remaining document plus summary and used a broad binary search on every page. The paginator now uses a measured row-height estimate, a narrow fallback search only when the estimate does not fit, and reuses the built page documents for PDF/print output. It preserves the same page-plan invariants; there is no timing threshold in the tests.

### Logo snapshot stability — T-QUO-019 / AC-QUO-003 (2026-11-17)

- At official issue via `QuotationExportService`, if `store_profile.logo_path` points to an existing file, the logo is copied into managed content-addressed storage (`assets/logos/<sha256>.<suffix>`). The immutable snapshot records both the original configured `logo_path` and a `logo_asset` object with `sha256`, `relative_path`, and `file_size`. A missing/unusable configured path is treated as no asset; it does not block issue.
- A later change to the logo file at the same path, or changing `store_profile.logo_path`, affects only documents issued afterward. The old snapshot, official PDF bytes/SHA-256, and reprint result remain unchanged; the old content-addressed asset remains available. A new issue captures the new content hash/path.
- Verified with temporary synthetic PNG images only. The renderer still does **not** draw a logo, and no real store logo, branding asset, or UI choice was added; the approved Noto Naskh Arabic font is covered separately. Logo rendering is out of scope: V1 is approved without a logo (`OQ-DOC-002` resolved 2026-11-17).
- Status: the snapshot/hash/reprint-stability portion of `T-QUO-019`/`AC-QUO-003` is covered; logo-dependent visual acceptance is out of scope for V1 (owner decision: no logo).

### Tests (actual result)

- Phase 7 quotation tests: `24 passed` — `.venv\Scripts\python.exe -m pytest tests/test_phase7_quotations.py -q`.
- Phase 7 rendering tests: `10 passed, 1 skipped` — `.venv\Scripts\python.exe -m pytest tests/test_phase7_rendering.py -q`. The skip is the real A4-printer check because no physical printer is available.
- Logo snapshot tests: `2 passed` — `.venv\Scripts\python.exe -m pytest tests/test_phase7_logo_snapshot.py -q`.
- Font package/embedding tests: `3 passed` — `.venv\Scripts\python.exe -m pytest tests/test_phase7_fonts.py -q`.
- Line-amount and summary-format tests: `6 passed` — `.venv\Scripts\python.exe -m pytest tests/test_phase7_line_amounts.py -q` (per-line values, extracted summary order/isolation, two-decimal ASCII separators, totals reconciliation).
- Golden template tests: `3 passed` — `.venv\Scripts\python.exe -m pytest tests/test_phase7_golden.py -q` (exact reference match, determinism across two renders, approved-font money glyphs).
- Focused line-amount/rendering/quotation run: `37 passed, 1 skipped` — `.venv\Scripts\python.exe -m pytest tests/test_phase7_line_amounts.py tests/test_phase7_rendering.py tests/test_phase7_quotations.py -q`.
- Full suite: **`149 passed, 1 skipped`** — `.venv\Scripts\python.exe -m pytest -q` (run 2026-11-17 after the template approval and golden reference, project-local `.venv`, Python 3.12.10, pytest 9.2.0, PySide6-Essentials 6.12.0). The skip is the physical A4-printer check; T-DOC-009 remains open.
- Follow-up full suite after adding T-QUO-013 and the opt-in printer check: **`150 passed, 1 skipped`**. This run used the bundled Python 3.12.14 with the project's installed dependencies and `--basetemp` pointing to a shorter path to avoid the Windows path-length error in the logo snapshot fixtures. The physical A4-printer check was skipped.
- The older quotation issue tests still use a test-double export row; the rendering tests exercise the real renderer/export/PDF path.

### Implementation notes / deviations

- The quote status is **not** changed when a conversion draft is created (per 05 §3.2); it becomes `CONVERTED` only when the sale is posted. Restoration therefore usually records the unchanged status; if the status changed while the draft existed, the saved `status_before_conversion` is restored unless the quotation has expired.
- `document_snapshots` is immutable (trigger) and unique per `(document_type, document_id)`. The official export row is inserted in the same transaction as the snapshot and `ISSUED` status.
- `document_exports` metadata now has a real file behind it for official quotation PDFs; reprint/print verify the stored SHA-256.
- The renderer uses the bundled Noto Naskh Arabic 2.000 (OFL 1.1) Regular/Bold faces. No system Arabic font is used; required ligatures are disabled for PDF text extraction. T-DOC-005 is covered.
- `tests/golden/quotation-a4-v1.json` is the T-DOC-007 reference for the owner-approved template. It locks content/layout rather than glyph pixels because Latin tokens fall back to a system font (see the approval section above); a pixel golden would require an approved Latin font.
- The logo asset is not bundled; a configured logo is copied into managed content-addressed storage and recorded in the snapshot. Logo rendering/branding is not implemented: V1 documents are approved without a logo (`OQ-DOC-002` resolved 2026-11-17).
- Revisions copy the source `issue_date`/`valid_until`; the draft can be edited before issuing. This is an implementation choice, not a new business approval.
- No UI, Qt WebEngine, separate PDF library, real store logo asset, golden image, or physical-printer acceptance test was added.

### What remains in Phase 7 (open)

- **T-DOC-009:** real A4 printer test. The machine currently exposes only Microsoft Print to PDF, Microsoft XPS Document Writer, and Fax. The [opt-in printer check](09_TEST_AND_ACCEPTANCE_STRATEGY.md#t-doc-009) submits a multipage synthetic quotation through the native print path when a physical queue is selected. Acceptance requires inspection of the paper output. Selecting Microsoft Print to PDF was verified to fail before submission (T-DOC-010).
- **T-QUO-013:** a dedicated acceptance test verifies that the issued original is locked, only the accepted lines are converted through a revision, and the original issued snapshot is preserved.
- Phase 7 acceptance still requires the physical print check. The owner authorized continuing with Phase 8 development while it is pending.
- V1 logo: resolved by the owner's 2026-11-17 decision (option 1A) — no logo in V1 documents; logo support/assets may be reconsidered in a later version, and no logo asset is added.


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
| Phase 7 gate | 2. In progress — partial | Phase 6 gate passed 2026-11-17. Phase 7 quotation core, approved ADR-0004 Option B renderer, and approved Noto Naskh Arabic font are implemented/tested; `OQ-QUO-001/005` and `OQ-DOC-001` were resolved 2026-11-17. The owner approved the V1 template without a logo (option 1A, 2026-11-17), `OQ-DOC-002` is resolved for V1, and T-DOC-007 is covered by the deterministic golden reference. T-DOC-009 (real printer) remains open. Phase 8 development is authorized in parallel. |

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
  - Phase 7: **IN PROGRESS — PARTIAL.** Phase 6 gate passed 2026-11-17. Quotation core, the approved ADR-0004 Option B renderer, and the approved Noto Naskh Arabic font are implemented and tested. `OQ-QUO-001/005` and `OQ-DOC-001` were resolved 2026-11-17. The owner approved the V1 template without a logo (option 1A, 2026-11-17); `OQ-DOC-002` is resolved for V1 and T-DOC-007 is covered by the deterministic golden reference. T-DOC-009 (real A4 printer) remains open.
  - A4/PDF/UI/real-printer checks: PDF/A4/text extraction/pagination/font embedding are covered by tests; golden template comparison is covered by the content/layout reference; physical-printer output remains open for Phase 7.
- True Phase 6 exit blockers: **none**.
- Deferred features, not Phase 6 exit blockers: `OQ-INV-007` (`ADJUSTMENT_IN`) and `OQ-SAL-003` (mixed/partial cash).
- Later-phase items: `T-DB-001/002` are Phase 7 quotation-schema tests and are now covered headlessly; `T-SEC-008` is Phase 9 audit-log work. Phase 7 is in progress/partial, not complete.
- Next: resolve T-DOC-009 (real A4 printer test); T-DOC-007 is covered by the approved-template golden reference and `OQ-DOC-002` is resolved for V1 (no logo). Phase 8 development started on 2026-11-18 with the owner's approval. `OQ-INV-007` and `OQ-SAL-003` remain deferred.
