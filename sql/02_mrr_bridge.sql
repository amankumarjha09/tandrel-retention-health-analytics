-- 02_mrr_bridge.sql
-- Runs on the CLEAN tables. Builds the monthly MRR bridge:
-- opening MRR + new + expansion - contraction - churn = closing MRR.
-- Parameter: :report_start (first month to show, e.g. '2025-11')

-- name: create_mrr_movements
-- One row per customer per month. LAG() reads last month's MRR for the same customer.
CREATE VIEW mrr_movements AS
SELECT customer_id,
       month,
       mrr,
       COALESCE(prev_mrr, 0) AS prev_mrr,
       CASE WHEN COALESCE(prev_mrr, 0) = 0 AND mrr > 0              THEN mrr             ELSE 0 END AS new_mrr,
       CASE WHEN prev_mrr > 0 AND mrr > prev_mrr                    THEN mrr - prev_mrr  ELSE 0 END AS expansion_mrr,
       CASE WHEN prev_mrr > 0 AND mrr > 0 AND mrr < prev_mrr        THEN prev_mrr - mrr  ELSE 0 END AS contraction_mrr,
       CASE WHEN prev_mrr > 0 AND mrr = 0                           THEN prev_mrr        ELSE 0 END AS churned_mrr
FROM (
    SELECT customer_id,
           month,
           mrr,
           LAG(mrr) OVER (PARTITION BY customer_id ORDER BY month) AS prev_mrr
    FROM clean_mrr
);

-- name: create_mrr_bridge_all
-- Adds the customer movements up for each month. Also counts customers (logos).
CREATE VIEW mrr_bridge_all AS
SELECT month,
       SUM(prev_mrr)        AS opening_mrr,
       SUM(new_mrr)         AS new_mrr,
       SUM(expansion_mrr)   AS expansion_mrr,
       SUM(contraction_mrr) AS contraction_mrr,
       SUM(churned_mrr)     AS churned_mrr,
       SUM(mrr)             AS closing_mrr,
       SUM(CASE WHEN prev_mrr > 0     THEN 1 ELSE 0 END) AS opening_customers,
       SUM(CASE WHEN new_mrr > 0      THEN 1 ELSE 0 END) AS new_customers,
       SUM(CASE WHEN churned_mrr > 0  THEN 1 ELSE 0 END) AS churned_customers,
       SUM(CASE WHEN mrr > 0          THEN 1 ELSE 0 END) AS closing_customers
FROM mrr_movements
GROUP BY month;

-- name: mrr_bridge
SELECT month,
       opening_mrr,
       new_mrr,
       expansion_mrr,
       contraction_mrr,
       churned_mrr,
       closing_mrr,
       opening_customers,
       new_customers,
       churned_customers,
       closing_customers,
       ROUND(1.0 * churned_customers / opening_customers, 4) AS logo_churn_rate
FROM mrr_bridge_all
WHERE month >= :report_start
ORDER BY month;
