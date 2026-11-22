-- 03_grr_nrr.sql
-- Gross Revenue Retention (GRR) and Net Revenue Retention (NRR).
-- Parameters: :report_start, :base_month, :latest_month

-- name: monthly_retention
-- Month view, built from the MRR bridge.
-- GRR = (opening - churned - contraction) / opening
-- NRR = (opening - churned - contraction + expansion) / opening
SELECT month,
       opening_mrr,
       ROUND(1.0 * (opening_mrr - churned_mrr - contraction_mrr) / opening_mrr, 4)                 AS grr,
       ROUND(1.0 * (opening_mrr - churned_mrr - contraction_mrr + expansion_mrr) / opening_mrr, 4) AS nrr
FROM mrr_bridge_all
WHERE month >= :report_start
ORDER BY month;

-- name: annual_retention
-- 12-month view. Take the customers who were paying in :base_month.
-- Then look at what the SAME customers pay in :latest_month. New customers are left out.
-- NRR counts expansion. GRR caps each customer at the starting MRR, so expansion does not count.
WITH base AS (
    SELECT customer_id, mrr
    FROM clean_mrr
    WHERE month = :base_month AND mrr > 0
),
latest AS (
    SELECT customer_id, mrr
    FROM clean_mrr
    WHERE month = :latest_month
)
SELECT COUNT(*)                                              AS starting_customers,
       SUM(CASE WHEN COALESCE(l.mrr, 0) > 0 THEN 1 ELSE 0 END) AS retained_customers,
       SUM(b.mrr)                                            AS starting_mrr,
       SUM(COALESCE(l.mrr, 0))                               AS ending_mrr_same_customers,
       SUM(MIN(b.mrr, COALESCE(l.mrr, 0)))                   AS retained_mrr_capped,
       ROUND(1.0 * SUM(COALESCE(l.mrr, 0)) / SUM(b.mrr), 4)  AS nrr_12m,
       ROUND(1.0 * SUM(MIN(b.mrr, COALESCE(l.mrr, 0))) / SUM(b.mrr), 4) AS grr_12m
FROM base b
LEFT JOIN latest l ON l.customer_id = b.customer_id;
