CREATE SCHEMA raw;
CREATE SCHEMA staging;
CREATE SCHEMA curated;
CREATE SCHEMA mart;

CREATE TABLE raw.customers (
    id INT,
    first_name STRING,
    last_name STRING,
    email STRING,
    created_at TIMESTAMP
);

CREATE TABLE raw.orders (
    order_id INT,
    customer_id INT,
    amount DECIMAL(10,2),
    status STRING,
    created_at TIMESTAMP
);