"""Desktop report selections and projections over the reporting services."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ..auth import PermissionDenied
from ..bootstrap import AppContext
from ..reporting import ReportingService
from .common import format_scaled, money

FINANCE = "view_sensitive_accounting_reports"
SALES = "view_sales_reports"
PURCHASES = "view_purchase_reports"
PURCHASE_COST = "view_purchase_cost"
INVENTORY_COST = "view_inventory_cost"
STOCK_REPORTS = ("manage_inventory", INVENTORY_COST)
CUSTOMER_BALANCE = "view_customer_balance"
PROFIT = "view_profit_reports"

LABELS = {
    "SALE": "بيع", "PURCHASE": "شراء", "RETURN": "مرتجع", "REVERSAL": "عكس",
    "IN": "وارد", "OUT": "صادر", "LOT": "دفعة", "REEL": "بكرة",
    "OPEN": "مفتوح", "EXHAUSTED": "نفد", "SEALED": "مختومة", "OPENED": "مفتوحة",
    "EMPTY": "فارغة", "DAMAGED": "تالفة", "RETURNED": "مرتجعة",
    "EXPENSE": "مصروف", "REVENUE": "إيراد",
    "not_due": "غير مستحق", "0_30": "٠–٣٠ يومًا", "31_60": "٣١–٦٠ يومًا",
    "61_90": "٦١–٩٠ يومًا", "over_90": "أكثر من ٩٠ يومًا",
    "RECEIPT": "استلام", "SALE_ISSUE": "صرف بيع", "SALE_RETURN_IN": "مرتجع بيع",
    "PURCHASE_RETURN": "مرتجع شراء", "DAMAGE": "تالف",
    "CREDIT_APPLICATION": "استخدام رصيد دائن",
}


@dataclass(frozen=True)
class Column:
    key: str
    title: str
    format: str = "text"
    permission: str | None = None

    def display(self, value) -> str:
        if value is None or value == "":
            return "—"
        if self.format == "money":
            return money(value)
        if self.format == "quantity":
            return format_scaled(value)
        if self.format == "label":
            return LABELS.get(value, str(value))
        if self.format == "date":
            return value[:10]
        return str(value)


@dataclass(frozen=True)
class ReportTable:
    title: str
    source: str
    columns: tuple[Column, ...]


@dataclass(frozen=True)
class Report:
    title: str
    method: str
    permissions: tuple[str, ...]
    dates: str
    filters: tuple[str, ...]
    tables: tuple[ReportTable, ...]
    totals: tuple[Column, ...] = ()
    prepare: Callable[[dict], dict] | None = None
    accounting: bool = False
    required_filter: str | None = None


def _party(result: dict) -> dict:
    names = {"1100": "ذمم العملاء", "2010": "ذمم الموردين", "2020": "أرصدة العملاء الدائنة"}
    return result | {
        "balances": [{"account": names[code], **values} for code, values in result["accounts"].items()],
        "movements": [row | {"account": names[row["account_code"]]} for row in result["movements"]],
    }


def _cash(result: dict) -> dict:
    accounts = result["accounts"]
    return result | {"movements": [
        row | {"account": f"{account['gl_code']} — {account['gl_name']}"}
        for account in accounts for row in account["movements"]
    ]}


def _projects(result: dict) -> dict:
    groups = [group | {"project": group["project_name"] or "بدون مشروع"}
              for group in (*result["projects"], *result["unassigned"])]
    return result | {
        "groups": groups,
        "movements": [
            row | {"project": group["project"], "customer_name": group["customer_name"],
                   "kind": "تحصيل" if row["kind"] == "RECEIPT" else row["kind"]}
            for group in groups for row in group["movements"]
        ],
    }


def _aging(result: dict) -> dict:
    return result | {"buckets": [
        {"bucket": key, "amount_piastres": value} for key, value in result["buckets_piastres"].items()
    ]}


def _trial(result: dict) -> dict:
    debit = sum(row["debit"] for row in result["rows"])
    credit = sum(row["credit"] for row in result["rows"])
    return result | {"debit": debit, "credit": credit, "difference_piastres": debit - credit}


def _balance_sheet(result: dict) -> dict:
    sections = {"assets": "الأصول", "liabilities": "الالتزامات", "equity": "حقوق الملكية"}
    return result | {"balances": [
        row | {"section": label} for key, label in sections.items() for row in result[key]
    ] + [{"code": "—", "name_ar": "نتيجة الفترة غير المقفلة",
          "balance_piastres": result["unclosed_result_piastres"], "section": "حقوق الملكية"}]}


def _reconciliation(result: dict) -> dict:
    names = {"1100": "ذمم العملاء", "2010": "ذمم الموردين"}
    return {"balances": [
        {"account": f"{code} — {names[code]}", **row,
         "result": "مطابق" if row["matched"] else "يوجد فرق"}
        for code, row in result.items()
    ]}


ENTRY_DATE = Column("entry_date", "التاريخ", "date")
DOCUMENT = Column("document_no", "المستند")
PRODUCT = Column("product_code", "الصنف")
UNIT = Column("base_unit_code", "وحدة الأساس")
QUANTITY = Column("quantity_micros", "الكمية", "quantity")
KIND = Column("kind", "النوع", "label")
DEBIT = Column("debit_piastres", "مدين", "money")
CREDIT = Column("credit_piastres", "دائن", "money")
AMOUNT = Column("amount_piastres", "المبلغ", "money")
ACCOUNT = Column("account", "الحساب")
ACCOUNT_CODE = Column("code", "رقم الحساب")
ACCOUNT_NAME = Column("name_ar", "الحساب")
OUTSTANDING = Column("outstanding_piastres", "المستحق", "money")
LEDGER = Column("ledger_piastres", "رصيد الأستاذ", "money")
DIFFERENCE = Column("difference_piastres", "الفرق", "money")
OPENING = Column("opening_piastres", "افتتاحي", "money")
CLOSING = Column("closing_piastres", "ختامي", "money")
REFERENCE = Column("source_document_no", "المستند الأصلي")
REMAINING_COST = Column("remaining_cost_piastres", "التكلفة المتبقية", "money", INVENTORY_COST)
INVENTORY_AMOUNT = Column("inventory_piastres", "أثر المخزون", "money", PURCHASE_COST)
SALES_AMOUNT = Column("net_sales_piastres", "صافي المبيعات", "money")
PERIOD_BALANCES = ReportTable("أرصدة الفترة", "balances", (ACCOUNT, OPENING, DEBIT, CREDIT, CLOSING))
PARTY_MOVEMENTS = ReportTable("الحركات", "movements", (
    ENTRY_DATE, DOCUMENT, ACCOUNT, DEBIT, CREDIT, Column("balance_piastres", "الرصيد", "money"),
))
AGING_BUCKETS = ReportTable("فئات الاستحقاق", "buckets", (Column("bucket", "الفئة", "label"), AMOUNT))
AGING_TOTALS = (Column("total_piastres", "إجمالي الفواتير الموجبة", "money"), LEDGER, DIFFERENCE)


def _aging_invoices(party: Column) -> ReportTable:
    return ReportTable("الفواتير", "invoices", (
        DOCUMENT, party, Column("invoice_date", "تاريخ الفاتورة", "date"),
        Column("aging_date", "تاريخ الاحتساب", "date"), Column("bucket", "الفئة", "label"), OUTSTANDING,
    ))


REPORTS = {
    "sales": Report("المبيعات والمرتجعات", "sales_detail", (SALES,), "range",
        ("customer_id", "product_id", "posted_by"), (
            ReportTable("تفاصيل البيع", "lines", (
                ENTRY_DATE, DOCUMENT, KIND, Column("customer_code", "العميل"), PRODUCT, UNIT,
                QUANTITY, Column("net_piastres", "الصافي", "money"),
            )),
        ), (Column("sales_piastres", "المبيعات", "money"), Column("returns_piastres", "المرتجعات", "money"),
            Column("reversals_piastres", "العكس", "money"), SALES_AMOUNT)),
    "purchases": Report("المشتريات والمرتجعات", "purchase_detail", (PURCHASES,), "range",
        ("supplier_id", "product_id"), (
            ReportTable("تفاصيل الشراء", "lines", (
                ENTRY_DATE, DOCUMENT, KIND, Column("supplier_code", "المورد"), PRODUCT, UNIT, QUANTITY,
                Column("supplier_piastres", "قيمة المورد", "money", PURCHASE_COST), INVENTORY_AMOUNT,
            )),
        ), (Column("supplier_purchases_piastres", "مشتريات المورد", "money", PURCHASE_COST),
            Column("purchase_expenses_piastres", "المصروف المباشر", "money", PURCHASE_COST),
            Column("return_cost_piastres", "تكلفة المرتجعات", "money", PURCHASE_COST))),
    "inventory_moves": Report("حركات المخزون", "inventory_movements", STOCK_REPORTS, "range", ("product_id",), (
        ReportTable("الحركات", "rows", (
            Column("occurred_at", "التاريخ", "date"), Column("movement_no", "الحركة"), PRODUCT,
            Column("movement_type", "النوع", "label"), Column("direction", "الاتجاه", "label"), QUANTITY,
            Column("cost_piastres", "التكلفة", "money", INVENTORY_COST),
        )),
    )),
    "lots": Report("دفعات المخزون", "fifo_lots", STOCK_REPORTS, "current", ("product_id",), (
        ReportTable("الدفعات", "rows", (
            Column("lot_no", "الدفعة"), PRODUCT, UNIT, REFERENCE,
            Column("received_at", "الاستلام", "date"), Column("original_qty_micros", "الأصل", "quantity"),
            Column("remaining_qty_micros", "المتبقي", "quantity"),
            Column("lot_status", "الحالة", "label"), REMAINING_COST,
        )),
    )),
    "reels": Report("بكرات المخزون", "reel_balances", STOCK_REPORTS, "current", ("product_id",), (
        ReportTable("البكرات", "rows", (
            Column("reel_no", "البكرة"), Column("source_reel_no", "البكرة الأصلية"), PRODUCT, UNIT,
            Column("status", "الحالة", "label"), Column("remaining_length_micros", "الطول المتبقي", "quantity"),
            REMAINING_COST, REFERENCE,
        )),
    )),
    "valuation": Report("تقييم المخزون ومطابقته", "inventory_valuation", (INVENTORY_COST,), "as_of", (), (
        ReportTable("المخزون", "products", (
            PRODUCT, Column("product_name", "الاسم"), UNIT, Column("tracking_mode", "التتبع", "label"),
            QUANTITY, Column("cost_piastres", "القيمة", "money"),
        )),
    ), (Column("inventory_piastres", "قيمة المخزون", "money"), LEDGER, DIFFERENCE)),
    "customer_statement": Report("كشف حساب عميل", "customer_period_statement", (CUSTOMER_BALANCE,), "range",
        ("customer_id",), (PERIOD_BALANCES, PARTY_MOVEMENTS), prepare=_party, required_filter="customer_id"),
    "supplier_statement": Report("كشف حساب مورد", "supplier_period_statement", (FINANCE,), "range",
        ("supplier_id",), (PERIOD_BALANCES, PARTY_MOVEMENTS), prepare=_party, required_filter="supplier_id"),
    "receivables": Report("أعمار ذمم العملاء", "customer_receivables_aging", (FINANCE,), "as_of", ("customer_id",),
        (AGING_BUCKETS, _aging_invoices(Column("customer_name", "العميل"))), AGING_TOTALS, _aging),
    "payables": Report("أعمار ذمم الموردين", "supplier_payables_aging", (FINANCE,), "as_of", ("supplier_id",),
        (AGING_BUCKETS, _aging_invoices(Column("supplier_name", "المورد"))), AGING_TOTALS, _aging),
    "projects": Report("أرصدة المشاريع", "project_balances", (FINANCE,), "as_of", ("customer_id",), (
        ReportTable("الأرصدة", "groups", (
            Column("customer_name", "العميل"), Column("project", "المشروع"), SALES_AMOUNT,
            Column("cash_sales_piastres", "النقدي", "money"), Column("receipts_piastres", "التحصيل", "money"), OUTSTANDING,
        )),
        ReportTable("الحركات", "movements", (
            ENTRY_DATE, Column("invoice_no", "فاتورة البيع"), KIND,
            Column("project", "المشروع"), Column("customer_name", "العميل"),
            SALES_AMOUNT, Column("ar_delta_piastres", "أثر الذمة", "money"),
        )),
    ), prepare=_projects),
    "cash": Report("حركة الحسابات المالية", "financial_activity", (FINANCE,), "range", ("financial_account_id",), (
        ReportTable("الأرصدة", "accounts", (
            Column("gl_code", "رقم الحساب"), Column("gl_name", "الحساب"), OPENING, DEBIT, CREDIT, CLOSING,
        )),
        PARTY_MOVEMENTS,
    ), prepare=_cash),
    "income_expenses": Report("الإيرادات والمصروفات", "income_expense_detail", (FINANCE,), "range", ("account_type",), (
        ReportTable("الحسابات", "accounts", (ACCOUNT_CODE, ACCOUNT_NAME, DEBIT, CREDIT, AMOUNT)),
        ReportTable("الحركات", "lines", (ENTRY_DATE, DOCUMENT, ACCOUNT_NAME,
            Column("account_type", "النوع", "label"), DEBIT, CREDIT, AMOUNT)),
    ), (Column("income_piastres", "الإيرادات", "money"), Column("expenses_piastres", "المصروفات", "money"),
        Column("net_result_piastres", "صافي النتيجة", "money"))),
    "profit": Report("الربحية", "profitability", (PROFIT,), "range", (), (
        ReportTable("الحسابات", "accounts", (ACCOUNT_CODE, ACCOUNT_NAME, DEBIT, CREDIT, AMOUNT)),
    ), (SALES_AMOUNT, Column("cost_of_sales_piastres", "تكلفة المبيعات", "money"),
        Column("gross_profit_piastres", "مجمل الربح", "money"),
        Column("net_result_piastres", "صافي الربح أو الخسارة", "money"))),
    "trial": Report("ميزان المراجعة", "trial_balance", (FINANCE,), "as_of", (), (
        ReportTable("الحسابات", "rows", (ACCOUNT_CODE, ACCOUNT_NAME,
            Column("debit", "مدين", "money"), Column("credit", "دائن", "money"))),
    ), (Column("debit", "إجمالي المدين", "money"), Column("credit", "إجمالي الدائن", "money"), DIFFERENCE),
        _trial, accounting=True),
    "balance_sheet": Report("المركز المالي", "balance_sheet", (FINANCE,), "as_of", (), (
        ReportTable("الأرصدة", "balances", (Column("section", "البند"), ACCOUNT_CODE, ACCOUNT_NAME,
            Column("balance_piastres", "الرصيد", "money"))),
    ), (Column("assets_piastres", "الأصول", "money"),
        Column("liabilities_and_equity_piastres", "الالتزامات وحقوق الملكية", "money"), DIFFERENCE),
        _balance_sheet, accounting=True),
    "reconcile": Report("مطابقة ذمم العملاء والموردين", "reconcile_parties", (FINANCE,), "as_of", (), (
        ReportTable("المطابقة", "balances", (
            ACCOUNT, LEDGER, Column("subledger_piastres", "إجمالي الذمم", "money"), DIFFERENCE,
            Column("unassigned_lines", "سطور دون طرف"), Column("result", "النتيجة"),
        )),
    ), prepare=_reconciliation, accounting=True),
}
REPORT_PERMISSIONS = tuple(dict.fromkeys(permission for report in REPORTS.values() for permission in report.permissions))


def load_report(context: AppContext, user_id: int, key: str, from_date: str, through_date: str,
                filters: dict) -> tuple[dict, set[str]]:
    report = REPORTS[key]
    permissions = context.permissions.permissions(user_id)
    if not permissions.intersection(report.permissions):
        raise PermissionDenied("الحساب لا يملك صلاحية هذا التقرير.")
    if report.required_filter and filters.get(report.required_filter) is None:
        raise ValueError("اختر العميل أو المورد لهذا الكشف.")
    arguments = {name: filters.get(name) for name in report.filters}
    if key == "sales" and FINANCE not in permissions:
        arguments["posted_by"] = user_id
    if report.dates == "range":
        arguments |= {"from_date": from_date, "through_date": through_date}
    elif report.dates == "as_of":
        arguments["as_of"] = through_date
    service = context.accounting if report.accounting else ReportingService(context.db)
    result = getattr(service, report.method)(**arguments)
    if isinstance(result, list):
        result = {"rows": result}
    if report.prepare:
        result = report.prepare(result)
    visible = {
        column.key: result[column.key]
        for column in report.totals
        if column.permission is None or column.permission in permissions
    }
    for table in report.tables:
        keys = [column.key for column in table.columns
                if column.permission is None or column.permission in permissions]
        visible[table.source] = [{key: row[key] for key in keys} for row in result[table.source]]
    return visible, permissions
