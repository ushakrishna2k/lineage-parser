let
    // Inputs
    Summary = customer_segment,
    Orders = customer_orders,

    // Join summary with orders to bring customer details
    Joined = Table.NestedJoin(
        Summary,
        {"customer_id"},
        Orders,
        {"customer_id"},
        "Orders",
        JoinKind.Inner
    ),

    // Expand required columns from Orders
    Expanded = Table.ExpandTableColumn(
        Joined,
        "Orders",
        {"full_name", "email"}
    ),

    // Remove duplicates (since orders may create multiple rows per customer)
    DistinctRows = Table.Distinct(Expanded, {"customer_id"}),

    // Select final dashboard fields
    FinalOutput = Table.SelectColumns(
        DistinctRows,
        {
            "customer_id",
            "full_name",
            "email",
            "total_orders",
            "total_spent",
            "segment"
        }
    )

in
    FinalOutput
