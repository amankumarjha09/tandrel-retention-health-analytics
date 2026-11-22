"""
Build the Tandrel Retention & Health report (fictional data).

Steps
1. Load the raw CSV files into an in-memory SQLite database.
2. Run the SQL data checks and compare them with the pandas checks.
3. Clean the data (rules are in validate_data.py) and load the clean tables.
4. Run the SQL files: MRR bridge, GRR/NRR, cohort retention, health score.
5. Run reconciliation checks. The script stops if any check fails.
6. Write the Excel report to output/.

Run:  python scripts/build_report.py
"""
import re
import sqlite3
from pathlib import Path

import pandas as pd
from openpyxl import Workbook
from openpyxl.chart import BarChart, LineChart, Reference
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from validate_data import clean, load_raw, pandas_checks

ROOT = Path(__file__).resolve().parents[1]
SQL_DIR = ROOT / "sql"
OUT_DIR = ROOT / "output"
OUT_FILE = OUT_DIR / "Tandrel_Retention_Health_Report.xlsx"

# Report settings (all months are 'YYYY-MM')
PARAMS = {
    "report_start": "2025-11",   # first month shown in the report
    "base_month": "2025-10",     # start of the 12-month retention window
    "latest_month": "2026-10",   # last month in the data
    "latest_idx": 2026 * 12 + 10,
    "recent_start": "2026-08",   # health score: last 3 months
    "prior_start": "2026-05",    # health score: the 3 months before
    "prior_end": "2026-07",
}

CHECK_TREATMENT = {
    "Duplicate rows in mrr_monthly": "Keep the first row for each customer and month",
    "Duplicate rows in support_monthly": "Keep the first row for each customer and month",
    "Missing MRR values": "Treated as missing. Use the previous month's MRR",
    "Negative MRR values": "Treated as missing. Use the previous month's MRR",
    "Missing months in MRR history": "Add the month. Use the previous month's MRR",
    "CSAT score outside 1 to 5": "Set the score to empty",
    "Unknown customer_id in mrr_monthly": "Remove the rows",
    "Unknown customer_id in usage_monthly": "Remove the rows",
    "Unknown customer_id in support_monthly": "Remove the rows",
    "Chat volume differs between usage and support files": "No action needed if 0",
}


# ----------------------------------------------------------------------------
# SQL helpers
# ----------------------------------------------------------------------------
def run_sql_file(conn, path: Path, params: dict, names=None) -> dict:
    """Run a .sql file made of blocks that start with '-- name: <name>'.
    CREATE blocks are executed. SELECT blocks come back as DataFrames.
    If `names` is given, only those blocks are run."""
    text = path.read_text()
    pieces = re.split(r"^-- name: (\w+)\s*$", text, flags=re.M)
    results = {}
    for name, body in zip(pieces[1::2], pieces[2::2]):
        if names is not None and name not in names:
            continue
        sql = "\n".join(l for l in body.splitlines() if not l.strip().startswith("--")).strip().rstrip(";")
        if sql.upper().startswith("CREATE"):
            conn.execute(sql)
        else:
            results[name] = pd.read_sql_query(sql, conn, params=params)
    return results


# ----------------------------------------------------------------------------
# Reconciliation checks
# ----------------------------------------------------------------------------
def reconcile(conn, raw, clean_tables, log, bridge_all, annual, health, band_summary):
    """Return a list of (description, passed) and stop if anything fails."""
    checks = []

    # 1. The MRR bridge adds up in every month.
    calc = (bridge_all["opening_mrr"] + bridge_all["new_mrr"] + bridge_all["expansion_mrr"]
            - bridge_all["contraction_mrr"] - bridge_all["churned_mrr"])
    checks.append(("MRR bridge adds up: opening + new + expansion - contraction - churn = closing (every month)",
                   bool((calc == bridge_all["closing_mrr"]).all())))

    # 2. Opening MRR of a month equals closing MRR of the month before.
    b = bridge_all.sort_values("month").reset_index(drop=True)
    checks.append(("Opening MRR of each month equals closing MRR of the previous month",
                   bool((b["opening_mrr"].iloc[1:].values == b["closing_mrr"].iloc[:-1].values).all())))

    # 3. 12-month GRR and NRR: SQL result equals an independent pandas calculation.
    m = clean_tables["mrr"]
    start = m[(m["month"] == PARAMS["base_month"]) & (m["mrr"] > 0)].set_index("customer_id")["mrr"]
    end = m[m["month"] == PARAMS["latest_month"]].set_index("customer_id")["mrr"].reindex(start.index).fillna(0)
    nrr_pd = round(end.sum() / start.sum(), 4)
    grr_pd = round(pd.concat([start, end], axis=1).min(axis=1).sum() / start.sum(), 4)
    checks.append(("12-month NRR and GRR from SQL match a separate pandas calculation",
                   bool(annual["nrr_12m"].iloc[0] == nrr_pd and annual["grr_12m"].iloc[0] == grr_pd)))

    # 4. Clean MRR row count reconciles with the cleaning log.
    expected = (log["mrr_rows_raw"] - log["mrr_duplicate_rows_removed"]
                - log["mrr_unknown_customer_rows_removed"] + log["mrr_missing_months_added"])
    checks.append(("Clean MRR rows = raw rows - duplicates - unknown customers + months added",
                   expected == log["mrr_rows_clean"]))

    # 5. No duplicate keys left in the clean tables.
    dup_left = sum(int(clean_tables[t].duplicated(["customer_id", "month"]).sum()) for t in ("mrr", "usage", "support"))
    checks.append(("No duplicate customer and month rows left in the clean tables", dup_left == 0))

    # 6. Health scores cover every paying customer in the latest month, and scores stay in 0-100.
    latest_customers = int(bridge_all.loc[bridge_all["month"] == PARAMS["latest_month"], "closing_customers"].iloc[0])
    checks.append(("Health score has one row for every paying customer in the latest month",
                   len(health) == latest_customers and int(band_summary["customers"].sum()) == latest_customers))
    checks.append(("All health scores are between 0 and 100",
                   bool(health["health_score"].between(0, 100).all())))

    # 7. Latest closing MRR equals the sum of customer MRR in the health table.
    latest_mrr = int(bridge_all.loc[bridge_all["month"] == PARAMS["latest_month"], "closing_mrr"].iloc[0])
    checks.append(("Latest closing MRR equals the MRR total in the health table", latest_mrr == int(health["mrr"].sum())))

    failed = [d for d, ok in checks if not ok]
    if failed:
        raise SystemExit("Reconciliation FAILED:\n- " + "\n- ".join(failed))
    return checks


# ----------------------------------------------------------------------------
# Excel output
# ----------------------------------------------------------------------------
TEAL = "1F4E5F"
LIGHT = "EEF4F6"
BAND_FILL = {"Healthy": "D9EAD3", "Watch": "FFF2CC", "At Risk": "F4CCCC"}
thin = Side(style="thin", color="D0D7DB")
BORDER = Border(left=thin, right=thin, top=thin, bottom=thin)


def write_table(ws, df, row, col, formats=None, band_col=None):
    """Write a DataFrame with a styled header. Returns the last row used."""
    formats = formats or {}
    for j, name in enumerate(df.columns):
        cell = ws.cell(row=row, column=col + j, value=name)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor=TEAL)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = BORDER
    for i, rec in enumerate(df.itertuples(index=False), start=1):
        for j, val in enumerate(rec):
            if pd.isna(val):
                val = None
            elif hasattr(val, "item"):
                val = val.item()
            cell = ws.cell(row=row + i, column=col + j, value=val)
            cell.border = BORDER
            fmt = formats.get(df.columns[j])
            if fmt:
                cell.number_format = fmt
            if i % 2 == 0:
                cell.fill = PatternFill("solid", fgColor=LIGHT)
            if band_col and df.columns[j] == band_col and val in BAND_FILL:
                cell.fill = PatternFill("solid", fgColor=BAND_FILL[val])
    ws.row_dimensions[row].height = 45
    return row + len(df)


def set_widths(ws, widths):
    for letter, w in widths.items():
        ws.column_dimensions[letter].width = w


def title(ws, text, sub=None):
    ws["A1"] = text
    ws["A1"].font = Font(bold=True, size=15, color=TEAL)
    if sub:
        ws["A2"] = sub
        ws["A2"].font = Font(italic=True, color="666666")


def build_excel(data_checks, recon, bridge, retention, annual, cohorts, health, band_summary, log):
    wb = Workbook()
    money, pct, pct0 = '"$"#,##0', "0.0%", "0%"
    fict = "Fictional company and fictional data. Created for demonstration only."

    # ---------------- Summary ----------------
    ws = wb.active
    ws.title = "Summary"
    title(ws, "Tandrel: Retention & Customer Health Report", fict)
    ws["A3"] = "Report period: Nov 2025 to Oct 2026"
    ws["A3"].font = Font(color="666666")

    last = bridge.iloc[-1]
    first_open = bridge.iloc[0]["opening_mrr"]
    at_risk = band_summary[band_summary["health_band"] == "At Risk"]
    at_risk_n = int(at_risk["customers"].sum()) if len(at_risk) else 0
    at_risk_pct = float(at_risk["pct_of_mrr"].sum()) / 100 if len(at_risk) else 0.0
    ann = annual.iloc[0]
    kpis = pd.DataFrame(
        [
            ("Closing MRR (Oct 2026)", int(last["closing_mrr"]), "Monthly recurring revenue from paying customers."),
            ("MRR change over 12 months", last["closing_mrr"] / first_open - 1, "Closing MRR vs. the MRR at the start of Nov 2025."),
            ("Paying customers (Oct 2026)", int(last["closing_customers"]), "Customers with MRR above zero."),
            ("12-month GRR", float(ann["grr_12m"]), "Share of starting revenue kept, with no credit for expansion."),
            ("12-month NRR", float(ann["nrr_12m"]), "Share of starting revenue kept, including expansion. Above 100% means growth."),
            ("Logo churn, Oct 2026", float(last["logo_churn_rate"]), "Customers lost in the month / customers at the start of the month."),
            ("Customers At Risk", at_risk_n, "Health score below 50."),
            ("MRR held by At Risk customers", at_risk_pct, "Share of closing MRR that sits with At Risk customers."),
        ],
        columns=["Metric", "Value", "How to read it"],
    )
    end = write_table(ws, kpis, 5, 1)
    fmts = [money, pct, "#,##0", pct, pct, pct, "#,##0", pct]
    for i, f in enumerate(fmts, start=6):
        ws.cell(row=i, column=2).number_format = f
        ws.cell(row=i, column=2).alignment = Alignment(horizontal="right")

    r = end + 3
    ws.cell(row=r - 1, column=1, value="Customer health bands (Oct 2026)").font = Font(bold=True, size=12, color=TEAL)
    bs = band_summary.rename(columns={"health_band": "Band", "customers": "Customers", "mrr": "MRR", "pct_of_mrr": "% of MRR"}).copy()
    bs["% of MRR"] = bs["% of MRR"] / 100
    band_end = write_table(ws, bs, r, 1, {"MRR": money, "% of MRR": pct}, band_col="Band")

    chart = BarChart()
    chart.type = "col"
    chart.title = "Customers by health band"
    chart.add_data(Reference(ws, min_col=2, min_row=r, max_row=band_end), titles_from_data=True)
    chart.set_categories(Reference(ws, min_col=1, min_row=r + 1, max_row=band_end))
    chart.legend = None
    chart.height, chart.width = 7, 11
    ws.add_chart(chart, f"A{band_end + 2}")

    notes_row = band_end + 19
    ws.cell(row=notes_row, column=1, value="What this dummy data shows").font = Font(bold=True, size=12, color=TEAL)
    peak = bridge.loc[bridge["churned_mrr"].idxmax()]
    growth_text = ("Existing customers grew revenue even after churn and contraction."
                   if ann["nrr_12m"] >= 1 else "Expansion did not cover churn and contraction.")
    notes = [
        f"12-month NRR is {ann['nrr_12m']:.1%} and GRR is {ann['grr_12m']:.1%}. {growth_text}",
        f"{at_risk_n} {'customer is' if at_risk_n == 1 else 'customers are'} At Risk and "
        f"{int(band_summary.loc[band_summary['health_band'] == 'Watch', 'customers'].sum())} are on Watch. "
        f"At Risk customers hold {at_risk_pct:.1%} of closing MRR.",
        f"Churned MRR was highest in {peak['month']} (${int(peak['churned_mrr']):,}).",
        "The data is made up. These findings only show how the report reads. They are not real business results.",
    ]
    for i, t in enumerate(notes, start=1):
        ws.cell(row=notes_row + i, column=1, value="- " + t).alignment = Alignment(wrap_text=False)
    set_widths(ws, {"A": 34, "B": 16, "C": 72, "D": 14})

    # ---------------- MRR Bridge ----------------
    ws = wb.create_sheet("MRR Bridge")
    title(ws, "Monthly MRR bridge", fict)
    br = bridge.rename(columns={
        "month": "Month", "opening_mrr": "Opening MRR", "new_mrr": "New", "expansion_mrr": "Expansion",
        "contraction_mrr": "Contraction", "churned_mrr": "Churned", "closing_mrr": "Closing MRR",
        "opening_customers": "Opening customers", "new_customers": "New customers",
        "churned_customers": "Churned customers", "closing_customers": "Closing customers",
        "logo_churn_rate": "Logo churn %"})
    last_row = write_table(ws, br, 4, 1, {c: money for c in ["Opening MRR", "New", "Expansion", "Contraction", "Churned", "Closing MRR"]} | {"Logo churn %": pct})
    ws.freeze_panes = "B5"
    set_widths(ws, {get_column_letter(i): 14 for i in range(1, 13)})
    line = LineChart()
    line.title = "Closing MRR by month"
    line.add_data(Reference(ws, min_col=7, min_row=4, max_row=last_row), titles_from_data=True)
    line.set_categories(Reference(ws, min_col=1, min_row=5, max_row=last_row))
    line.legend = None
    line.height, line.width = 8, 18
    ws.add_chart(line, f"A{last_row + 3}")

    # ---------------- Retention ----------------
    ws = wb.create_sheet("Retention")
    title(ws, "GRR and NRR", fict)
    ret = retention.rename(columns={"month": "Month", "opening_mrr": "Opening MRR", "grr": "GRR", "nrr": "NRR"})
    end = write_table(ws, ret, 4, 1, {"Opening MRR": money, "GRR": pct, "NRR": pct})
    ws.cell(row=end + 3, column=1, value="12-month view (same customers, Oct 2025 to Oct 2026)").font = Font(bold=True, size=12, color=TEAL)
    a = annual.rename(columns={
        "starting_customers": "Starting customers", "retained_customers": "Retained customers",
        "starting_mrr": "Starting MRR", "ending_mrr_same_customers": "Ending MRR (same customers)",
        "retained_mrr_capped": "Retained MRR (capped)", "nrr_12m": "NRR", "grr_12m": "GRR"})
    write_table(ws, a, end + 4, 1, {"Starting MRR": money, "Ending MRR (same customers)": money,
                                   "Retained MRR (capped)": money, "NRR": pct, "GRR": pct})
    ws.cell(row=end + 7, column=1, value="Month view: GRR = (opening - churned - contraction) / opening. NRR adds expansion. "
                                         "12-month view: GRR caps each customer at its starting MRR.").font = Font(italic=True, color="666666")
    set_widths(ws, {get_column_letter(i): 18 for i in range(1, 8)})
    ws.row_dimensions[end + 4].height = 45

    # ---------------- Cohorts ----------------
    ws = wb.create_sheet("Cohorts")
    title(ws, "Cohort retention by signup quarter", fict)
    sizes = cohorts.groupby("cohort")["customers"].first()
    row = 4
    for label, col in [("Customer (logo) retention", "logo_retention_pct"), ("MRR retention", "mrr_retention_pct")]:
        ws.cell(row=row, column=1, value=label).font = Font(bold=True, size=12, color=TEAL)
        pv = cohorts.pivot(index="cohort", columns="months_since_signup", values=col) / 100
        pv = pv.reindex(columns=[0, 3, 6, 9, 12]).round(3)
        pv.columns = [f"Month {c}" for c in pv.columns]
        pv.insert(0, "Customers", sizes.reindex(pv.index).values)
        pv = pv.reset_index().rename(columns={"cohort": "Signup quarter"})
        row = write_table(ws, pv, row + 1, 1, {c: pct0 for c in pv.columns[2:]}) + 3
    ws.cell(row=row - 1, column=1, value="Blank cell = some customers in that cohort are not old enough yet. MRR retention can pass 100% when expansion is high.").font = Font(italic=True, color="666666")
    set_widths(ws, {get_column_letter(i): 15 for i in range(1, 8)})

    # ---------------- Health Scores ----------------
    ws = wb.create_sheet("Health Scores")
    title(ws, "Customer health scores (Oct 2026), lowest first", fict)
    hs = health.rename(columns={
        "customer_id": "Customer ID", "company_name": "Company (fictional)", "segment": "Segment", "mrr": "MRR",
        "usage_ratio": "Usage ratio (recent/prior)", "bot_resolved_rate": "Bot-resolved %", "handoff_rate": "Handoff %",
        "csat": "CSAT", "usage_score": "Usage score", "bot_score": "Bot score", "handoff_score": "Handoff score",
        "csat_score": "CSAT score", "health_score": "Health score", "health_band": "Band"})
    write_table(ws, hs, 4, 1, {"MRR": money, "Bot-resolved %": pct, "Handoff %": pct, "Usage ratio (recent/prior)": "0.00",
                              "CSAT": "0.00", "Usage score": "0.0", "Bot score": "0.0", "Handoff score": "0.0",
                              "CSAT score": "0.0", "Health score": "0.0"}, band_col="Band")
    ws.freeze_panes = "C5"
    ws.auto_filter.ref = f"A4:{get_column_letter(len(hs.columns))}{4 + len(hs)}"
    set_widths(ws, {"A": 12, "B": 24, "C": 13, **{get_column_letter(i): 13 for i in range(4, 15)}})
    ws.cell(row=2, column=1, value="Weights: usage trend 35%, bot-resolved 20%, handoff 15%, CSAT 30%. "
                                   "Weights and limits are assumptions for this demo. " + fict).font = Font(italic=True, color="666666")

    # ---------------- Data Checks ----------------
    ws = wb.create_sheet("Data Checks")
    title(ws, "Data checks on the raw files, and how each problem was handled", fict)
    dc = data_checks.rename(columns={"check_name": "Check", "records_flagged": "Records flagged"}).copy()
    dc["How it was handled"] = dc["Check"].map(CHECK_TREATMENT)
    end = write_table(ws, dc, 4, 1)
    ws.cell(row=end + 3, column=1, value="Reconciliation checks on the final numbers").font = Font(bold=True, size=12, color=TEAL)
    rc = pd.DataFrame([(d, "PASS" if ok else "FAIL") for d, ok in recon], columns=["Check", "Result"])
    write_table(ws, rc, end + 4, 1)
    for i in range(len(rc)):
        c = ws.cell(row=end + 5 + i, column=2)
        c.fill = PatternFill("solid", fgColor="D9EAD3" if c.value == "PASS" else "F4CCCC")
        c.alignment = Alignment(horizontal="center")
    set_widths(ws, {"A": 88, "B": 16, "C": 52})

    # Print setup: landscape, fit to one page wide
    for sheet in wb.worksheets:
        sheet.page_setup.orientation = "landscape"
        sheet.page_setup.fitToWidth = 1
        sheet.page_setup.fitToHeight = 0
        sheet.sheet_properties.pageSetUpPr.fitToPage = True

    OUT_DIR.mkdir(exist_ok=True)
    wb.save(OUT_FILE)


# ----------------------------------------------------------------------------
def main():
    raw = load_raw()
    conn = sqlite3.connect(":memory:")

    # Raw tables
    for name, df in raw.items():
        df.to_sql("customers" if name == "customers" else f"raw_{name}", conn, index=False)

    # Data checks: SQL vs pandas
    data_checks = run_sql_file(conn, SQL_DIR / "01_data_checks.sql", PARAMS)["data_checks"]
    pd_checks = pandas_checks(raw)
    sql_checks = dict(zip(data_checks["check_name"], data_checks["records_flagged"]))
    assert sql_checks == pd_checks, f"SQL and pandas checks differ:\n{sql_checks}\n{pd_checks}"
    print("Data checks: SQL and pandas agree.")

    # Clean data
    clean_tables, log = clean(raw)
    for name in ("mrr", "usage", "support"):
        clean_tables[name].to_sql(f"clean_{name}", conn, index=False)

    # KPI queries
    r2 = run_sql_file(conn, SQL_DIR / "02_mrr_bridge.sql", PARAMS)
    r3 = run_sql_file(conn, SQL_DIR / "03_grr_nrr.sql", PARAMS)
    r4 = run_sql_file(conn, SQL_DIR / "04_cohort_retention.sql", PARAMS)
    bridge_all = pd.read_sql_query("SELECT * FROM mrr_bridge_all ORDER BY month", conn)
    r5 = run_sql_file(conn, SQL_DIR / "05_health_score.sql", PARAMS, names=["health_scores"])
    # The band summary query reads a health_scores table, so save the scores first.
    r5["health_scores"].to_sql("health_scores", conn, index=False, if_exists="replace")
    band_summary = run_sql_file(conn, SQL_DIR / "05_health_score.sql", PARAMS,
                                names=["health_band_summary"])["health_band_summary"]

    recon = reconcile(conn, raw, clean_tables, log, bridge_all, r3["annual_retention"], r5["health_scores"], band_summary)
    print(f"Reconciliation: {len(recon)} checks passed.")

    build_excel(data_checks, recon, r2["mrr_bridge"], r3["monthly_retention"], r3["annual_retention"],
                r4["cohort_retention"], r5["health_scores"], band_summary, log)
    print(f"Report written: {OUT_FILE.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
