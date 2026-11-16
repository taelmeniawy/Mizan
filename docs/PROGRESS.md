# Progress

- Phase: 5 complete (phases 1–5)
- Done: foundation; auth/permissions; master data; accounting; purchasing/LCP/FIFO/reels/landed cost.
- Next: finish phase-5 acceptance dependencies that need sales returns (`T-INV-026`, `T-RB-002`) during phase 6, then continue phase 6 sales/pricing/credit/returns. Phase 6 not started.
- Tests: 43 passed (run), 0 failed.
- Deviations:
  - Phase 5 uses `purchase_reel_inputs` to persist reel lengths at draft posting; documented model doesn't name this table.
  - `T-DB-009` formula is enforced by `mizan.lcp.validate_layer`, not a DB trigger; DB enforces range/position/remaining-qty.
  - Sales-return/damaged-return paths and `T-RB-002` remain phase 6; only purchase rollback is covered now.
  - `ADJUSTMENT_IN` is modeled in schema but not posted by a service yet.
  - UI/PySide6 not started; headless core only.
- Blocked: none. LCP implemented per owner instruction; docs still label it PROPOSED.
