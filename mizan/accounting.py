from __future__ import annotations

from datetime import datetime, timezone

from .db import Database
from .numbering import next_number


CHART = [
    ("1010", "Cash on Hand", "ASSET", "DEBIT", 0),
    ("1020", "Bank", "ASSET", "DEBIT", 0),
    ("1030", "E-Wallets", "ASSET", "DEBIT", 0),
    ("1100", "Accounts Receivable", "ASSET", "DEBIT", 1),
    ("1200", "Inventory", "ASSET", "DEBIT", 1),
    ("1210", "Damaged Inventory", "ASSET", "DEBIT", 0),
    ("1300", "Advances to Suppliers", "ASSET", "DEBIT", 0),
    ("2010", "Accounts Payable", "LIABILITY", "CREDIT", 1),
    ("2020", "Customer Credits", "LIABILITY", "CREDIT", 0),
    ("2030", "Accrued Expenses", "LIABILITY", "CREDIT", 0),
    ("2040", "Taxes Payable", "LIABILITY", "CREDIT", 0),
    ("3010", "Capital", "EQUITY", "CREDIT", 0),
    ("3020", "Retained Earnings", "EQUITY", "CREDIT", 0),
    ("3030", "Opening Balance Equity", "EQUITY", "CREDIT", 0),
    ("4010", "Sales Revenue", "REVENUE", "CREDIT", 0),
    ("4020", "Sales Returns", "REVENUE", "DEBIT", 0),
    ("4030", "Other Income", "REVENUE", "CREDIT", 0),
    ("5010", "Cost of Goods Sold", "EXPENSE", "DEBIT", 0),
    ("5020", "Inventory Loss/Damaged", "EXPENSE", "DEBIT", 0),
    ("5030", "Landed Cost Adjustment", "EXPENSE", "DEBIT", 0),
    ("5100", "Operating Expenses", "EXPENSE", "DEBIT", 0),
    ("5110", "Rent", "EXPENSE", "DEBIT", 0),
    ("5120", "Utilities", "EXPENSE", "DEBIT", 0),
    ("5130", "Salaries", "EXPENSE", "DEBIT", 0),
    ("5190", "Other Operating Expenses", "EXPENSE", "DEBIT", 0),
]

MAPPINGS = {
    "AR_CONTROL": "1100",
    "AP_CONTROL": "2010",
    "INVENTORY_CONTROL": "1200",
    "DAMAGED_INVENTORY": "1210",
    "COGS": "5010",
    "SALES_REVENUE": "4010",
    "SALES_RETURNS": "4020",
    "CUSTOMER_CREDITS": "2020",
    "INVENTORY_LOSS": "5020",
    "LANDED_COST_ADJUSTMENT": "5030",
    "OPENING_EQUITY": "3030",
}


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class AccountingService:
    def __init__(self, db: Database):
        self.db = db

    def seed_coa(self) -> None:
        with self.db.transaction() as conn:
            for code, name, account_type, normal, is_control in CHART:
                conn.execute(
                    "INSERT OR IGNORE INTO accounts(code,name_ar,account_type,normal_balance,is_control,is_postable) "
                    "VALUES(?,?,?,?,?,?)",
                    (code, name, account_type, normal, is_control, 0 if code in {"5100"} else 1),
                )
            for key, code in MAPPINGS.items():
                account_id = conn.execute("SELECT id FROM accounts WHERE code=?", (code,)).fetchone()["id"]
                conn.execute(
                    "INSERT OR IGNORE INTO account_mappings(mapping_key,account_id) VALUES(?,?)",
                    (key, account_id),
                )

    def account(self, code: str):
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM accounts WHERE code=?", (code,)).fetchone()
        if row is None:
            raise ValueError(f"unknown account: {code}")
        if not row["is_postable"]:
            raise ValueError(f"account is not postable: {code}")
        return row

    def post_journal(
        self,
        entry_date: str,
        source_type: str,
        source_id: int,
        lines: list[dict],
        description: str = "",
        posted_by: int | None = None,
        entry_kind: str = "POSTING",
        reversal_of_id: int | None = None,
    ) -> int:
        if not lines:
            raise ValueError("journal must have lines")
        normalized = []
        total_debit = total_credit = 0
        with self.db.connect() as conn:
            for index, line in enumerate(lines, start=1):
                account = conn.execute("SELECT * FROM accounts WHERE code=?", (line["account_code"],)).fetchone()
                if account is None:
                    raise ValueError(f"unknown account: {line['account_code']}")
                if not account["is_postable"] or not account["is_active"]:
                    raise ValueError(f"account is not postable: {line['account_code']}")
                debit = int(line.get("debit", 0))
                credit = int(line.get("credit", 0))
                if debit < 0 or credit < 0 or (debit > 0) == (credit > 0):
                    raise ValueError("each journal line must have exactly one positive side")
                total_debit += debit
                total_credit += credit
                normalized.append((account["id"], index, debit, credit, line))
        if total_debit != total_credit or total_debit <= 0:
            raise ValueError("journal is not balanced")

        with self.db.transaction() as conn:
            existing = conn.execute(
                "SELECT id FROM journal_entries WHERE source_type=? AND source_id=? AND entry_kind=?",
                (source_type, source_id, entry_kind),
            ).fetchone()
            if existing:
                raise ValueError("journal already exists for this source")
            entry_no = next_number(conn, "JOURNAL_ENTRY", int(entry_date[:4]), "JV-")
            idem = f"{source_type}:{source_id}:{entry_kind}"
            cur = conn.execute(
                "INSERT INTO journal_entries(entry_no,entry_date,source_type,source_id,entry_kind,description,posted_at,posted_by,reversal_of_id,idempotency_key) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (entry_no, entry_date, source_type, source_id, entry_kind, description, _now(), posted_by, reversal_of_id, idem),
            )
            entry_id = int(cur.lastrowid)
            for account_id, line_no, debit, credit, line in normalized:
                conn.execute(
                    "INSERT INTO journal_lines(journal_entry_id,line_no,account_id,debit_piastres,credit_piastres,party_type,party_id,project_id,description,source_line_id) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        entry_id,
                        line_no,
                        account_id,
                        debit,
                        credit,
                        line.get("party_type"),
                        line.get("party_id"),
                        line.get("project_id"),
                        line.get("description"),
                        line.get("source_line_id"),
                    ),
                )
        return entry_id

    def reverse(self, entry_id: int, posted_by: int | None = None) -> int:
        with self.db.connect() as conn:
            original = conn.execute("SELECT * FROM journal_entries WHERE id=?", (entry_id,)).fetchone()
            if original is None:
                raise ValueError("journal entry not found")
            if original["status"] != "POSTED":
                raise ValueError("journal entry is not reversible")
            lines = conn.execute(
                "SELECT a.code,l.* FROM journal_lines l JOIN accounts a ON a.id=l.account_id WHERE journal_entry_id=? ORDER BY line_no",
                (entry_id,),
            ).fetchall()
        reversal_lines = [
            {
                "account_code": row["code"],
                "debit": row["credit_piastres"],
                "credit": row["debit_piastres"],
                "party_type": row["party_type"],
                "party_id": row["party_id"],
                "project_id": row["project_id"],
                "description": f"reversal of {original['entry_no']}",
            }
            for row in lines
        ]
        new_id = self.post_journal(
            original["entry_date"],
            "REVERSAL",
            entry_id,
            reversal_lines,
            description=f"عكس القيد {original['entry_no']}",
            posted_by=posted_by,
            entry_kind="REVERSAL",
            reversal_of_id=entry_id,
        )
        with self.db.transaction() as conn:
            conn.execute("UPDATE journal_entries SET status='REVERSED', reversed_by_id=? WHERE id=?", (new_id, entry_id))
        return new_id

    def account_balance(self, code: str) -> int:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(l.debit_piastres-l.credit_piastres),0) AS bal "
                "FROM journal_lines l JOIN accounts a ON a.id=l.account_id "
                "JOIN journal_entries e ON e.id=l.journal_entry_id WHERE a.code=?",
                (code,),
            ).fetchone()
        return int(row["bal"])

    def party_balance(self, code: str, party_type: str, party_id: int) -> int:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(l.debit_piastres-l.credit_piastres),0) AS bal "
                "FROM journal_lines l JOIN accounts a ON a.id=l.account_id "
                "JOIN journal_entries e ON e.id=l.journal_entry_id "
                "WHERE a.code=? AND l.party_type=? AND l.party_id=?",
                (code, party_type, party_id),
            ).fetchone()
        return int(row["bal"])

    def trial_balance(self) -> list[dict]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT a.code,a.name_ar,COALESCE(SUM(l.debit_piastres),0) AS debit,"
                "COALESCE(SUM(l.credit_piastres),0) AS credit "
                "FROM accounts a LEFT JOIN journal_lines l ON l.account_id=a.id "
                "LEFT JOIN journal_entries e ON e.id=l.journal_entry_id "
                "GROUP BY a.id ORDER BY a.code"
            ).fetchall()
        return [dict(r) for r in rows]

    def create_financial_account(self, code: str, name_ar: str, account_type: str, gl_code: str) -> int:
        with self.db.transaction() as conn:
            gl = conn.execute("SELECT id FROM accounts WHERE code=?", (gl_code,)).fetchone()
            if gl is None:
                raise ValueError("GL account not found")
            cur = conn.execute(
                "INSERT INTO financial_accounts(code,name_ar,account_type,gl_account_id) VALUES(?,?,?,?)",
                (code, name_ar, account_type, gl["id"]),
            )
            return int(cur.lastrowid)

    def transfer(self, financial_account_id: int, to_gl_code: str, amount: int, entry_date: str, actor_id: int | None = None) -> int:
        if amount <= 0:
            raise ValueError("amount must be positive")
        with self.db.connect() as conn:
            account = conn.execute("SELECT * FROM financial_accounts WHERE id=?", (financial_account_id,)).fetchone()
            if account is None:
                raise ValueError("financial account not found")
            gl_code = conn.execute("SELECT code FROM accounts WHERE id=?", (account["gl_account_id"],)).fetchone()["code"]
        return self.post_journal(
            entry_date,
            "FINANCIAL_TRANSFER",
            financial_account_id,
            [
                {"account_code": to_gl_code, "debit": amount},
                {"account_code": gl_code, "credit": amount},
            ],
            description="تحويل مالي",
            posted_by=actor_id,
        )

    def post_opening(self, entry_date: str, account_code: str, amount: int, actor_id: int | None = None) -> int:
        if amount <= 0:
            raise ValueError("amount must be positive")
        return self.post_journal(
            entry_date,
            "OPENING",
            1,
            [
                {"account_code": account_code, "debit": amount},
                {"account_code": "3030", "credit": amount},
            ],
            description="قيد افتتاحي",
            posted_by=actor_id,
        )
