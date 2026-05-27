CREATE OR REPLACE TABLE curated.customer_summary AS
SELECT
    customer_id,
    COUNT(order_id) AS total_orders,
    SUM(amount) AS total_spent,
    AVG(amount) AS avg_order_value,
    MAX(order_date) AS last_order_date
FROM curated.customer_orders
GROUP BY customer_id;