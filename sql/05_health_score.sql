-- 05_health_score.sql
-- Customer health score (0 to 100) for every customer who is paying in :latest_month.
-- Parameters: :recent_start, :latest_month (last 3 months), :prior_start, :prior_end (the 3 months before)
--
-- Score parts and weights. These are ASSUMPTIONS for this demo, not tested on real data:
--   Usage trend   35%  recent avg chats / prior avg chats. 0.70 gives 0 points, 1.10 or more gives 100.
--   Bot-resolved  20%  bot-resolved share of chats. 60% or more gives 100 points.
--   Handoff       15%  share of chats handed to an agent. 0% gives 100 points, 40% or more gives 0.
--   CSAT          30%  weighted average CSAT. 1 gives 0 points, 5 gives 100.
-- If a part cannot be measured (new customer, no CSAT answers) it gets a neutral 50.
-- Bands: 70 and above = Healthy, 50 to 69.9 = Watch, below 50 = At Risk.

-- name: health_scores
WITH recent_usage AS (
    SELECT customer_id, AVG(conversations_handled) AS avg_chats
    FROM clean_usage
    WHERE month BETWEEN :recent_start AND :latest_month
    GROUP BY customer_id
),
prior_usage AS (
    SELECT customer_id, AVG(conversations_handled) AS avg_chats
    FROM clean_usage
    WHERE month BETWEEN :prior_start AND :prior_end
    GROUP BY customer_id
),
recent_support AS (
    SELECT customer_id,
           SUM(chats)           AS chats,
           SUM(bot_resolved)    AS bot_resolved,
           SUM(handed_to_agent) AS handed,
           1.0 * SUM(csat_avg * csat_responses)
               / NULLIF(SUM(CASE WHEN csat_avg IS NOT NULL THEN csat_responses END), 0) AS csat
    FROM clean_support
    WHERE month BETWEEN :recent_start AND :latest_month
    GROUP BY customer_id
),
parts AS (
    SELECT c.customer_id,
           c.company_name,
           c.segment,
           m.mrr,
           ROUND(1.0 * ru.avg_chats / pu.avg_chats, 3)   AS usage_ratio,
           ROUND(1.0 * rs.bot_resolved / rs.chats, 3)    AS bot_resolved_rate,
           ROUND(1.0 * rs.handed / rs.chats, 3)          AS handoff_rate,
           ROUND(rs.csat, 2)                             AS csat,
           CASE WHEN pu.avg_chats IS NULL THEN 50.0
                ELSE MAX(0.0, MIN(100.0, ((1.0 * ru.avg_chats / pu.avg_chats) - 0.70) / 0.40 * 100)) END AS usage_score,
           MIN(100.0, (1.0 * rs.bot_resolved / rs.chats) / 0.60 * 100)                                  AS bot_score,
           MAX(0.0, 100.0 - (1.0 * rs.handed / rs.chats) / 0.40 * 100)                                  AS handoff_score,
           CASE WHEN rs.csat IS NULL THEN 50.0 ELSE (rs.csat - 1) / 4.0 * 100 END                       AS csat_score
    FROM customers c
    JOIN clean_mrr m            ON m.customer_id = c.customer_id AND m.month = :latest_month AND m.mrr > 0
    LEFT JOIN recent_usage ru   ON ru.customer_id = c.customer_id
    LEFT JOIN prior_usage pu    ON pu.customer_id = c.customer_id
    LEFT JOIN recent_support rs ON rs.customer_id = c.customer_id
)
SELECT customer_id,
       company_name,
       segment,
       mrr,
       usage_ratio,
       bot_resolved_rate,
       handoff_rate,
       csat,
       ROUND(usage_score, 1)   AS usage_score,
       ROUND(bot_score, 1)     AS bot_score,
       ROUND(handoff_score, 1) AS handoff_score,
       ROUND(csat_score, 1)    AS csat_score,
       ROUND(0.35 * usage_score + 0.20 * bot_score + 0.15 * handoff_score + 0.30 * csat_score, 1) AS health_score,
       CASE WHEN 0.35 * usage_score + 0.20 * bot_score + 0.15 * handoff_score + 0.30 * csat_score >= 70 THEN 'Healthy'
            WHEN 0.35 * usage_score + 0.20 * bot_score + 0.15 * handoff_score + 0.30 * csat_score >= 50 THEN 'Watch'
            ELSE 'At Risk' END AS health_band
FROM parts
ORDER BY health_score ASC;

-- name: health_band_summary
-- Runs on the health_scores table that the Python script saves from the query above.
SELECT health_band,
       COUNT(*)                                                         AS customers,
       SUM(mrr)                                                         AS mrr,
       ROUND(100.0 * SUM(mrr) / (SELECT SUM(mrr) FROM health_scores), 1) AS pct_of_mrr
FROM health_scores
GROUP BY health_band
ORDER BY CASE health_band WHEN 'Healthy' THEN 1 WHEN 'Watch' THEN 2 ELSE 3 END;
