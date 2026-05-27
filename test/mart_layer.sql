CREATE OR REPLACE TABLE mart.customer_dashboard AS
SELECT
    cs.customer_id,
    co.full_name,
    co.email,
    cs.total_orders,
    cs.total_spent,
    cs.segment
FROM curated.customer_segment cs
JOIN curated.customer_orders co
    ON cs.customer_id = co.customer_id;