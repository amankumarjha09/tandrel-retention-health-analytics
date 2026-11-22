"""
Generate the FICTIONAL dataset for Tandrel.

Tandrel is an imaginary B2B SaaS company that sells an AI support chatbot.
Every name, number and record produced here is made up for demonstration.

The script writes four raw CSV files to ../data/. It also plants a few known
data problems (duplicates, nulls, invalid values, an unknown customer id) so the
validation step has something real to find.

Run:  python scripts/generate_dummy_data.py
"""
from pathlib import Path

import numpy as np
import pandas as pd

SEED = 42
N_CUSTOMERS = 120
# 24 months of history. The company "launched" in Nov 2024 (fictional).
MONTHS = pd.period_range("2024-11", "2026-10", freq="M")

DATA_DIR = Path(__file__).resolve().parents[1] / "data"

rng = np.random.default_rng(SEED)

SEGMENTS = {
    # segment: (share of customers, MRR range, monthly churn base, seats range)
    "SMB": (0.60, (200, 800), 0.022, (3, 12)),
    "Mid-Market": (0.28, (1000, 3000), 0.014, (10, 35)),
    "Enterprise": (0.12, (4000, 12000), 0.007, (30, 90)),
}
INDUSTRIES = ["E-commerce", "Fintech", "Healthcare", "Education", "Travel", "Logistics", "Retail"]
REGIONS = ["India", "Southeast Asia", "Middle East", "Europe", "North America"]
PLANS = {"SMB": "Starter", "Mid-Market": "Growth", "Enterprise": "Scale"}

WORD_A = ["Northfield", "Cedar Peak", "Bluewave", "Ironleaf", "Maple Crest", "Silverline", "Harbor Lane",
          "Redstone", "Oakridge", "Sunvale", "Pinegate", "Clearbrook", "Stonebridge", "Brightfield",
          "Lakeview", "Westmore", "Ashgrove", "Riverton", "Highmark", "Greenhill"]
WORD_B = ["Apparel", "Foods", "Travel", "Learning", "Health", "Logistics", "Finance", "Home", "Retail",
          "Mobility", "Studio", "Labs", "Market", "Care", "Supply"]


def make_customers() -> pd.DataFrame:
    seg_names = list(SEGMENTS)
    seg_probs = [SEGMENTS[s][0] for s in seg_names]
    names = set()
    rows = []
    for i in range(1, N_CUSTOMERS + 1):
        seg = rng.choice(seg_names, p=seg_probs)
        while True:
            name = f"{rng.choice(WORD_A)} {rng.choice(WORD_B)}"
            if name not in names:
                names.add(name)
                break
        # Signup month index 0..21 (last signup Sep 2026, so every customer has >= 2 months)
        signup_idx = int(rng.integers(0, 22))
        rows.append(
            {
                "customer_id": f"C{i:03d}",
                "company_name": name,
                "segment": seg,
                "industry": rng.choice(INDUSTRIES),
                "region": rng.choice(REGIONS),
                "signup_date": f"{MONTHS[signup_idx]}-01",
                "plan": PLANS[seg],
                "_signup_idx": signup_idx,
            }
        )
    return pd.DataFrame(rows)


def simulate(customers: pd.DataFrame):
    mrr_rows, usage_rows, support_rows = [], [], []
    for _, c in customers.iterrows():
        seg = c["segment"]
        _, (lo, hi), churn_base, (seat_lo, seat_hi) = SEGMENTS[seg]
        # Hidden "health" trait: higher is healthier. Drives usage, bot quality, CSAT and churn.
        health = float(rng.beta(5, 2))
        mrr = float(np.round(rng.uniform(lo, hi) / 10) * 10)
        seats = int(rng.integers(seat_lo, seat_hi + 1))
        conv_per_mrr = rng.uniform(0.6, 1.0)
        usage_level = 1.0
        # About 1 in 8 older customers start to decline in the last 6 months (usage, bot quality and
        # CSAT all fall). This is built in on purpose, so the health score has something to find.
        declining = c["_signup_idx"] <= 16 and rng.random() < 0.13
        decline_start = int(rng.integers(18, 21))
        for idx in range(int(c["_signup_idx"]), len(MONTHS)):
            month = str(MONTHS[idx])
            decline_steps = max(0, idx - decline_start + 1) if declining else 0
            if idx > c["_signup_idx"]:
                # Churn, expansion and contraction (after the first month)
                p_churn = churn_base * (1.9 - 1.5 * health) * (1.5 if decline_steps else 1.0)
                if rng.random() < p_churn:
                    mrr_rows.append((c["customer_id"], month, 0, "churned"))
                    churned = True
                    break
                if rng.random() < 0.05 * (0.5 + health):
                    mrr = np.round(mrr * (1 + rng.uniform(0.10, 0.35)) / 10) * 10
                elif rng.random() < 0.03 * (1.6 - health):
                    mrr = max(100, np.round(mrr * (1 - rng.uniform(0.10, 0.25)) / 10) * 10)
                usage_level *= 1 + (health - 0.62) * 0.05 + rng.normal(0, 0.03)
                if decline_steps:
                    usage_level *= 0.90
                usage_level = float(np.clip(usage_level, 0.25, 2.5))
            mrr_rows.append((c["customer_id"], month, int(mrr), "active"))

            conv = int(max(20, mrr * conv_per_mrr * usage_level * rng.uniform(0.9, 1.1)))
            active_users = int(max(1, round(seats * min(1.0, 0.45 + 0.5 * health + rng.normal(0, 0.05)))))
            usage_rows.append((c["customer_id"], month, active_users, conv))

            bot_rate = float(np.clip(0.38 + 0.38 * health - 0.035 * decline_steps + rng.normal(0, 0.04), 0.15, 0.92))
            bot_resolved = int(round(conv * bot_rate))
            handed = int(round((conv - bot_resolved) * rng.uniform(0.55, 0.85)))
            responses = int(round(conv * rng.uniform(0.04, 0.09)))
            csat = float(np.clip(2.9 + 1.9 * health - 0.18 * decline_steps + rng.normal(0, 0.15), 1.0, 5.0))
            support_rows.append(
                (c["customer_id"], month, conv, bot_resolved, handed, responses,
                 round(csat, 2) if responses > 0 else np.nan)
            )
    mrr_df = pd.DataFrame(mrr_rows, columns=["customer_id", "month", "mrr", "status"])
    usage_df = pd.DataFrame(usage_rows, columns=["customer_id", "month", "active_users", "conversations_handled"])
    support_df = pd.DataFrame(
        support_rows,
        columns=["customer_id", "month", "chats", "bot_resolved", "handed_to_agent", "csat_responses", "csat_avg"],
    )
    return mrr_df, usage_df, support_df


def plant_problems(mrr_df, usage_df, support_df):
    """Add known problems on purpose, so the data checks have something to find."""
    mrr_df = mrr_df.copy()
    support_df = support_df.copy()

    # Pick mid-life rows only (not first row, not the churn row)
    active = mrr_df[mrr_df["status"] == "active"].copy()
    active["rank"] = active.groupby("customer_id").cumcount()
    last_rank = active.groupby("customer_id")["rank"].transform("max")
    mid = active[(active["rank"] >= 2) & (active["rank"] <= last_rank - 1)]
    picks = mid.sample(n=10, random_state=SEED).index.tolist()

    mrr_df["mrr"] = mrr_df["mrr"].astype("Int64")
    mrr_df.loc[picks[0:2], "mrr"] = pd.NA           # 2 missing MRR values
    mrr_df.loc[picks[2], "mrr"] = -abs(mrr_df.loc[picks[2], "mrr"])  # 1 sign error (negative MRR)
    gap_row = picks[3]                                # 1 missing month (row deleted)
    dup_rows = mrr_df.loc[picks[4:10]]               # 6 exact duplicate rows
    mrr_df = pd.concat([mrr_df.drop(index=gap_row), dup_rows], ignore_index=True)

    # 4 exact duplicate support rows
    support_df = pd.concat([support_df, support_df.sample(n=4, random_state=SEED + 1)], ignore_index=True)
    # 3 CSAT scores outside the 1-5 range
    has_csat = support_df[support_df["csat_avg"].notna()].index
    bad = rng.choice(has_csat, size=3, replace=False)
    support_df.loc[bad, "csat_avg"] = [0.0, 6.0, -1.0]

    # 1 customer id that is not in the customer master (C999), in all three monthly files
    orphan_mrr = pd.DataFrame({"customer_id": ["C999"] * 3, "month": ["2026-06", "2026-07", "2026-08"],
                               "mrr": [450, 450, 450], "status": ["active"] * 3})
    orphan_usage = pd.DataFrame({"customer_id": ["C999"] * 3, "month": ["2026-06", "2026-07", "2026-08"],
                                 "active_users": [6, 6, 5], "conversations_handled": [380, 400, 360]})
    orphan_support = pd.DataFrame({"customer_id": ["C999"] * 3, "month": ["2026-06", "2026-07", "2026-08"],
                                   "chats": [380, 400, 360], "bot_resolved": [190, 200, 170],
                                   "handed_to_agent": [100, 110, 100], "csat_responses": [20, 22, 18],
                                   "csat_avg": [4.1, 4.0, 3.9]})
    mrr_df = pd.concat([mrr_df, orphan_mrr], ignore_index=True)
    usage_df = pd.concat([usage_df, orphan_usage], ignore_index=True)
    support_df = pd.concat([support_df, orphan_support], ignore_index=True)
    return mrr_df, usage_df, support_df


def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    customers = make_customers()
    mrr_df, usage_df, support_df = simulate(customers)
    mrr_df, usage_df, support_df = plant_problems(mrr_df, usage_df, support_df)

    customers.drop(columns=["_signup_idx"]).to_csv(DATA_DIR / "customers.csv", index=False)
    mrr_df.sort_values(["customer_id", "month"]).to_csv(DATA_DIR / "mrr_monthly.csv", index=False)
    usage_df.sort_values(["customer_id", "month"]).to_csv(DATA_DIR / "usage_monthly.csv", index=False)
    support_df.sort_values(["customer_id", "month"]).to_csv(DATA_DIR / "support_monthly.csv", index=False)
    print(f"customers: {len(customers)} | mrr rows: {len(mrr_df)} | usage rows: {len(usage_df)} | support rows: {len(support_df)}")


if __name__ == "__main__":
    main()
