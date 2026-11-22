-- 01_data_checks.sql
-- Runs on the RAW tables (raw_mrr, raw_usage, raw_support, customers) before any cleaning.
-- Each row in the result is one check and the number of records it flags.
-- All data is fictional (Tandrel is an imaginary company).

-- name: data_checks
SELECT 'Duplicate rows in mrr_monthly' AS check_name,
       COALESCE(SUM(c - 1), 0) AS records_flagged
FROM (SELECT COUNT(*) AS c FROM raw_mrr GROUP BY customer_id, month HAVING COUNT(*) > 1)

UNION ALL
SELECT 'Duplicate rows in support_monthly',
       COALESCE(SUM(c - 1), 0)
FROM (SELECT COUNT(*) AS c FROM raw_support GROUP BY customer_id, month HAVING COUNT(*) > 1)

UNION ALL
SELECT 'Missing MRR values', COUNT(*) FROM raw_mrr WHERE mrr IS NULL

UNION ALL
SELECT 'Negative MRR values', COUNT(*) FROM raw_mrr WHERE mrr < 0

UNION ALL
-- A customer should have one row for every month between its first and last row.
SELECT 'Missing months in MRR history',
       COALESCE(SUM(span - months_present), 0)
FROM (
    SELECT customer_id,
           MAX(m_idx) - MIN(m_idx) + 1 AS span,
           COUNT(DISTINCT m_idx)       AS months_present
    FROM (
        SELECT customer_id,
               CAST(substr(month, 1, 4) AS INTEGER) * 12 + CAST(substr(month, 6, 2) AS INTEGER) AS m_idx
        FROM raw_mrr
    )
    GROUP BY customer_id
)
WHERE span > months_present

UNION ALL
SELECT 'CSAT score outside 1 to 5', COUNT(*)
FROM raw_support
WHERE csat_avg IS NOT NULL AND (csat_avg < 1 OR csat_avg > 5)

UNION ALL
SELECT 'Unknown customer_id in mrr_monthly', COUNT(*)
FROM raw_mrr WHERE customer_id NOT IN (SELECT customer_id FROM customers)

UNION ALL
SELECT 'Unknown customer_id in usage_monthly', COUNT(*)
FROM raw_usage WHERE customer_id NOT IN (SELECT customer_id FROM customers)

UNION ALL
SELECT 'Unknown customer_id in support_monthly', COUNT(*)
FROM raw_support WHERE customer_id NOT IN (SELECT customer_id FROM customers)

UNION ALL
-- The same chat volume is stored in two files. They should agree.
SELECT 'Chat volume differs between usage and support files', COUNT(*)
FROM (SELECT DISTINCT customer_id, month, conversations_handled FROM raw_usage) u
JOIN (SELECT DISTINCT customer_id, month, chats FROM raw_support) s
  ON s.customer_id = u.customer_id AND s.month = u.month
WHERE s.chats <> u.conversations_handled;
