SELECT
    c.customer_id,
    c.name AS customer_name,
    o.order_id,
    o.order_date,
    SUM(oi.quantity * oi.unit_price) AS total_order_value
FROM customers c
JOIN orders o 
    ON c.customer_id = o.customer_id
JOIN order_items oi 
    ON o.order_id = oi.order_id
WHERE o.order_date >= '2024-01-01'
GROUP BY 
    c.customer_id, 
    c.name, 
    o.order_id, 
    o.order_date;