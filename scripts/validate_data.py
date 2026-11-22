"""
Data validation and cleaning for the Tandrel project (fictional data).

Two jobs:
1. pandas_checks()  - counts data problems in the RAW files. build_report.py compares these
                      counts with the SQL checks in sql/01_data_checks.sql. Both must agree.
2. clean()          - fixes the problems with simple, written rules and returns a log of what changed.

Cleaning rules:
- Duplicate rows (same customer_id and month): keep the first one.
- customer_id that is not in customers.csv: remove the row.
- MRR that is missing or negative: treat as missing, then use the previous month's MRR.
- A missing month inside a customer's history: add the month and use the previous month's MRR.
- CSAT outside 1 to 5: set to empty (no score).

Run on its own:  python scripts/validate_data.py
"""
from collections import OrderedDict
from pathlib import Path

import numpy as np
import pandas as pd

DATA_DIR = Path(__file__).resolve().parents[1] / "data"


def load_raw(data_dir: Path = DATA_DIR) -> dict:
    """Read the four raw CSV files exactly as they are."""
    return {
        "customers": pd.read_csv(data_dir / "customers.csv"),
        "mrr": pd.read_csv(data_dir / "mrr_monthly.csv"),
        "usage": pd.read_csv(data_dir / "usage_monthly.csv"),
        "support": pd.read_csv(data_dir / "support_monthly.csv"),
    }


def _month_index(month: pd.Series) -> pd.Series:
    """'2026-07' -> 2026*12 + 7. Makes month gaps easy to measure."""
    return month.str[:4].astype(int) * 12 + month.str[5:7].astype(int)


def pandas_checks(raw: dict) -> "OrderedDict[str, int]":
    """Same checks as sql/01_data_checks.sql, written in pandas. Names match the SQL output."""
    c, m, u, s = raw["customers"], raw["mrr"], raw["usage"], raw["support"]
    known = set(c["customer_id"])
    keys = ["customer_id", "month"]

    idx = m.assign(m_idx=_month_index(m["month"]))
    span = idx.groupby("customer_id")["m_idx"].agg(lambda x: x.max() - x.min() + 1)
    present = idx.groupby("customer_id")["m_idx"].nunique()
    missing_months = int((span - present)[(span - present) > 0].sum())

    both = (
        u[["customer_id", "month", "conversations_handled"]].drop_duplicates()
        .merge(s[["customer_id", "month", "chats"]].drop_duplicates(), on=keys)
    )

    out = OrderedDict()
    out["Duplicate rows in mrr_monthly"] = int(m.duplicated(keys).sum())
    out["Duplicate rows in support_monthly"] = int(s.duplicated(keys).sum())
    out["Missing MRR values"] = int(m["mrr"].isna().sum())
    out["Negative MRR values"] = int((m["mrr"] < 0).sum())
    out["Missing months in MRR history"] = missing_months
    out["CSAT score outside 1 to 5"] = int(((s["csat_avg"] < 1) | (s["csat_avg"] > 5)).sum())
    out["Unknown customer_id in mrr_monthly"] = int((~m["customer_id"].isin(known)).sum())
    out["Unknown customer_id in usage_monthly"] = int((~u["customer_id"].isin(known)).sum())
    out["Unknown customer_id in support_monthly"] = int((~s["customer_id"].isin(known)).sum())
    out["Chat volume differs between usage and support files"] = int(
        (both["chats"] != both["conversations_handled"]).sum()
    )
    return out


def clean(raw: dict):
    """Apply the cleaning rules. Returns (clean_tables, log)."""
    log = OrderedDict()
    customers = raw["customers"].copy()
    known = set(customers["customer_id"])
    keys = ["customer_id", "month"]

    # ---- MRR ----
    m = raw["mrr"].copy()
    log["mrr_rows_raw"] = len(m)
    dup = m.duplicated(keys, keep="first")
    log["mrr_duplicate_rows_removed"] = int(dup.sum())
    m = m[~dup]
    unknown = ~m["customer_id"].isin(known)
    log["mrr_unknown_customer_rows_removed"] = int(unknown.sum())
    m = m[~unknown]
    invalid = m["mrr"].isna() | (m["mrr"] < 0)
    log["mrr_invalid_values_filled"] = int(invalid.sum())
    m.loc[invalid, "mrr"] = np.nan

    parts, inserted = [], 0
    for cid, g in m.groupby("customer_id"):
        g = g.assign(period=pd.PeriodIndex(g["month"], freq="M")).set_index("period")
        full = pd.period_range(g.index.min(), g.index.max(), freq="M")
        g = g.reindex(full)
        inserted += int(g["customer_id"].isna().sum())
        g["customer_id"] = cid
        g["status"] = g["status"].fillna("active")
        g["month"] = g.index.astype(str)
        g["mrr"] = g["mrr"].ffill()          # previous month's MRR
        parts.append(g.reset_index(drop=True))
    m = pd.concat(parts, ignore_index=True)
    assert m["mrr"].notna().all(), "MRR still has gaps after filling"
    m["mrr"] = m["mrr"].astype(int)
    m = m[["customer_id", "month", "mrr", "status"]].sort_values(keys).reset_index(drop=True)
    log["mrr_missing_months_added"] = inserted
    log["mrr_rows_clean"] = len(m)

    # ---- Usage ----
    u = raw["usage"].copy()
    log["usage_rows_raw"] = len(u)
    u = u[~u.duplicated(keys, keep="first")]
    u = u[u["customer_id"].isin(known)].reset_index(drop=True)
    log["usage_rows_clean"] = len(u)

    # ---- Support ----
    s = raw["support"].copy()
    log["support_rows_raw"] = len(s)
    dup = s.duplicated(keys, keep="first")
    log["support_duplicate_rows_removed"] = int(dup.sum())
    s = s[~dup]
    s = s[s["customer_id"].isin(known)].copy()
    bad_csat = (s["csat_avg"] < 1) | (s["csat_avg"] > 5)
    log["support_csat_values_cleared"] = int(bad_csat.sum())
    s.loc[bad_csat, "csat_avg"] = np.nan
    s = s.reset_index(drop=True)
    log["support_rows_clean"] = len(s)

    return {"customers": customers, "mrr": m, "usage": u, "support": s}, log


if __name__ == "__main__":
    raw_data = load_raw()
    print("Checks on RAW data:")
    for name, n in pandas_checks(raw_data).items():
        print(f"  {name}: {n}")
    _, cleaning_log = clean(raw_data)
    print("\nCleaning log:")
    for k, v in cleaning_log.items():
        print(f"  {k}: {v}")
