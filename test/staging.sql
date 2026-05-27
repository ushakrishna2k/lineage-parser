CREATE OR REPLACE TABLE staging.customers_clean AS
SELECT
    id AS customer_id,
    UPPER(first_name) AS first_name,
    UPPER(last_name) AS last_name,
    LOWER(email) AS email,
    created_at
FROM raw.customers
WHERE email IS NOT NULL;
CREATE OR REPLACE TABLE staging.orders_clean AS
SELECT
    order_id,
    customer_id,
    amount,
    status,
    created_at
FROM raw.orders
WHERE status IN ('completed', 'shipped');