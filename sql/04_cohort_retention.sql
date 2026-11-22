-- 04_cohort_retention.sql
-- Cohort = the calendar quarter a customer signed up.
-- For each cohort we check how many customers (and how much MRR) are left
-- 0, 3, 6, 9 and 12 months after signup.
-- A cell is shown only when EVERY customer in the cohort is old enough for that checkpoint.
-- Parameter: :latest_idx (latest month as year*12 + month, e.g. 2026*12 + 10)

-- name: cohort_retention
WITH mrr_idx AS (
    SELECT customer_id,
           mrr,
           CAST(substr(month, 1, 4) AS INTEGER) * 12 + CAST(substr(month, 6, 2) AS INTEGER) AS m_idx
    FROM clean_mrr
),
cust AS (
    SELECT customer_id,
           substr(signup_date, 1, 4) || '-Q' || ((CAST(substr(signup_date, 6, 2) AS INTEGER) + 2) / 3) AS cohort,
           CAST(substr(signup_date, 1, 4) AS INTEGER) * 12 + CAST(substr(signup_date, 6, 2) AS INTEGER) AS s_idx
    FROM customers
),
cohort_size AS (
    SELECT cohort, COUNT(*) AS customers, MAX(s_idx) AS last_signup_idx
    FROM cust
    GROUP BY cohort
),
aged AS (
    SELECT c.cohort, m.mrr, m.m_idx - c.s_idx AS months_since_signup
    FROM cust c
    JOIN mrr_idx m ON m.customer_id = c.customer_id
),
start_mrr AS (
    SELECT cohort, SUM(mrr) AS mrr_at_signup
    FROM aged
    WHERE months_since_signup = 0
    GROUP BY cohort
)
SELECT a.cohort,
       s.customers,
       a.months_since_signup,
       ROUND(100.0 * SUM(CASE WHEN a.mrr > 0 THEN 1 ELSE 0 END) / s.customers, 1) AS logo_retention_pct,
       ROUND(100.0 * SUM(a.mrr) / t.mrr_at_signup, 1)                              AS mrr_retention_pct
FROM aged a
JOIN cohort_size s ON s.cohort = a.cohort
JOIN start_mrr  t ON t.cohort = a.cohort
WHERE a.months_since_signup IN (0, 3, 6, 9, 12)
  AND s.last_signup_idx + a.months_since_signup <= :latest_idx
GROUP BY a.cohort, a.months_since_signup
ORDER BY a.cohort, a.months_since_signup;
