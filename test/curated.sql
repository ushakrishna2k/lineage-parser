CREATE OR REPLACE TABLE curated.customer_orders AS
SELECT
    c.customer_id,
    CONCAT(c.first_name, ' ', c.last_name) AS full_name,
    c.email,
    o.order_id,
    o.amount,
    o.created_at AS order_date
FROM staging.customers_clean c
JOIN staging.orders_clean o
    ON c.customer_id = o.customer_id;
