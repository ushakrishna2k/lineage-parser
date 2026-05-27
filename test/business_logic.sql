CREATE OR REPLACE TABLE curated.customer_segment AS
SELECT
    customer_id,
    total_orders,
    total_spent,
    CASE
        WHEN total_spent > 1000 THEN 'High Value'
        WHEN total_spent BETWEEN 500 AND 1000 THEN 'Medium Value'
        ELSE 'Low Value'
    END AS segment
FROM curated.customer_summary;