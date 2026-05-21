"""SQL lineage parser — extracts column lineage, table lineage, transformations,
joins, and filter conditions from SQL statements.

Requires: pip install sqlglot

Usage:
  python lineage_parser.py path/to/script.sql
  cat script.sql | python lineage_parser.py
"""

import sys
import json
from typing import Any, Dict, List, Optional, Tuple

try:
    import sqlglot
    import sqlglot.expressions as exp
    _HAS_SQLGLOT = True
except ImportError:
    _HAS_SQLGLOT = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_table(alias: str, alias_map: Dict[str, str]) -> Optional[str]:
    if not alias:
        return None
    return alias_map.get(alias, alias)


def _col_ref(col: Any, alias_map: Dict[str, str]) -> Dict[str, Optional[str]]:
    return {
        "table": _resolve_table(col.table, alias_map),
        "column": col.name,
    }


def _collect_col_refs(node: Any, alias_map: Dict[str, str]) -> List[Dict]:
    return [_col_ref(c, alias_map) for c in node.find_all(exp.Column)]


def _classify_expr(node: Any) -> str:
    if node.find(exp.AggFunc):
        return "aggregation"
    if isinstance(node, exp.Column):
        return "direct"
    for arith in (exp.Mul, exp.Div, exp.Add, exp.Sub):
        if isinstance(node, arith) or node.find(arith):
            return "arithmetic"
    if node.find(exp.Func):
        return "function"
    return "expression"


def _join_type_str(join_node: Any) -> str:
    side = str(join_node.args.get("side") or "")
    kind = str(join_node.args.get("kind") or "")
    parts = [p for p in [side, kind] if p]
    return " ".join(parts + ["JOIN"]) if parts else "INNER JOIN"


# ---------------------------------------------------------------------------
# Extraction steps
# ---------------------------------------------------------------------------

def _build_alias_map(stmt: Any) -> Tuple[Dict[str, str], List[str]]:
    """Map table aliases to real names and collect source tables in order."""
    alias_map: Dict[str, str] = {}
    sources: List[str] = []

    from_clause = stmt.find(exp.From)
    if from_clause:
        tbl = from_clause.find(exp.Table)
        if tbl:
            name = tbl.name
            key = tbl.alias or name
            alias_map[key] = name
            if name not in sources:
                sources.append(name)

    for join in stmt.find_all(exp.Join):
        if isinstance(join.this, exp.Table):
            name = join.this.name
            key = join.this.alias or name
            alias_map[key] = name
            if name not in sources:
                sources.append(name)

    return alias_map, sources


def _extract_targets(stmt: Any) -> List[str]:
    targets: List[str] = []
    if isinstance(stmt, exp.Insert):
        tbl = stmt.find(exp.Table)
        if tbl:
            targets.append(tbl.name)
    elif isinstance(stmt, exp.Create):
        if isinstance(stmt.this, exp.Table):
            targets.append(stmt.this.name)
    return targets


def _extract_column_lineage(
    stmt: Any, alias_map: Dict[str, str]
) -> Tuple[List[Dict], List[Dict]]:
    column_lineage: List[Dict] = []
    transformations: List[Dict] = []

    select = stmt.find(exp.Select)
    if not select:
        return column_lineage, transformations

    for sel in select.expressions:
        is_aliased = isinstance(sel, exp.Alias)
        alias_name: Optional[str] = sel.alias if is_aliased else None
        inner = sel.this if is_aliased else sel

        sources = _collect_col_refs(inner, alias_map)
        expr_str = inner.sql()

        if alias_name:
            output_col = alias_name
        elif isinstance(inner, exp.Column):
            output_col = inner.name
        else:
            output_col = expr_str

        ttype = _classify_expr(inner)
        if ttype == "direct" and alias_name:
            ttype = "alias"

        column_lineage.append({
            "output_column": output_col,
            "alias": alias_name,
            "sources": sources,
            "expression": expr_str,
            "transformation_type": ttype,
        })

        if ttype not in ("direct", "alias"):
            agg = next(inner.find_all(exp.AggFunc), None)
            func_name = type(agg).__name__.upper() if agg else None
            transformations.append({
                "output_column": output_col,
                "expression": expr_str,
                "function": func_name,
                "type": ttype,
                "inputs": sources,
            })

    return column_lineage, transformations


def _extract_joins(stmt: Any, alias_map: Dict[str, str]) -> List[Dict]:
    joins: List[Dict] = []

    from_clause = stmt.find(exp.From)
    from_tbl = from_clause.find(exp.Table) if from_clause else None
    prev_key: Optional[str] = (from_tbl.alias or from_tbl.name) if from_tbl else None

    for join in stmt.find_all(exp.Join):
        if not isinstance(join.this, exp.Table):
            continue

        right_key = join.this.alias or join.this.name
        left_table = _resolve_table(prev_key, alias_map)
        right_table = _resolve_table(right_key, alias_map)

        on_clause = join.args.get("on")
        condition_str = on_clause.sql() if on_clause else None
        left_col = right_col = None

        if on_clause and isinstance(on_clause, exp.EQ):
            lhs, rhs = on_clause.left, on_clause.right
            if isinstance(lhs, exp.Column) and isinstance(rhs, exp.Column):
                left_col = _col_ref(lhs, alias_map)
                right_col = _col_ref(rhs, alias_map)

        joins.append({
            "join_type": _join_type_str(join),
            "left_table": left_table,
            "right_table": right_table,
            "condition": condition_str,
            "left_column": left_col,
            "right_column": right_col,
        })
        prev_key = right_key

    return joins


_OP_CLASSES: Optional[List[Tuple[Any, str]]] = None


def _op_map() -> List[Tuple[Any, str]]:
    global _OP_CLASSES
    if _OP_CLASSES is None:
        _OP_CLASSES = [
            (cls, sym)
            for attr, sym in [
                ("EQ", "="), ("NEQ", "!="), ("GT", ">"), ("GTE", ">="),
                ("LT", "<"), ("LTE", "<="), ("Like", "LIKE"), ("ILike", "ILIKE"),
                ("In", "IN"), ("Between", "BETWEEN"), ("Is", "IS"),
            ]
            for cls in [getattr(exp, attr, None)]
            if cls is not None
        ]
    return _OP_CLASSES


def _extract_conditions(node: Any, alias_map: Dict[str, str], out: List[Dict]) -> None:
    if isinstance(node, (exp.And, exp.Or)):
        _extract_conditions(node.left, alias_map, out)
        _extract_conditions(node.right, alias_map, out)
        return

    op_str = next((sym for cls, sym in _op_map() if isinstance(node, cls)), None)

    if op_str:
        left = getattr(node, "left", None)
        right = getattr(node, "right", None)
        col_ref = value = None

        if left and isinstance(left, exp.Column):
            col_ref = _col_ref(left, alias_map)
            value = right.sql() if right else None
        elif right and isinstance(right, exp.Column):
            col_ref = _col_ref(right, alias_map)
            value = left.sql() if left else None

        cond: Dict[str, Any] = {
            "expression": node.sql(),
            "operator": op_str,
            "type": "comparison",
        }
        if col_ref:
            cond["table"] = col_ref["table"]
            cond["column"] = col_ref["column"]
        if value is not None:
            cond["value"] = value
        out.append(cond)
    else:
        out.append({"expression": node.sql(), "operator": None, "type": "predicate"})


def _extract_filter_conditions(stmt: Any, alias_map: Dict[str, str]) -> List[Dict]:
    conditions: List[Dict] = []
    where = stmt.find(exp.Where)
    if where:
        _extract_conditions(where.this, alias_map, conditions)
    return conditions


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_lineage(sql: str) -> Dict[str, Any]:
    """Parse SQL and return full lineage.

    Returns a dict with keys:
      table_lineage, column_lineage, transformations, joins, filter_conditions
    """
    if not _HAS_SQLGLOT:
        raise ImportError("sqlglot is required: pip install sqlglot")

    stmt = sqlglot.parse_one(sql)
    alias_map, sources = _build_alias_map(stmt)
    targets = _extract_targets(stmt)
    column_lineage, transformations = _extract_column_lineage(stmt, alias_map)
    joins = _extract_joins(stmt, alias_map)
    filter_conditions = _extract_filter_conditions(stmt, alias_map)

    return {
        "table_lineage": {"sources": sources, "targets": targets},
        "column_lineage": column_lineage,
        "transformations": transformations,
        "joins": joins,
        "filter_conditions": filter_conditions,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: List[str]) -> None:
    if len(argv) >= 2:
        with open(argv[1], "r", encoding="utf-8") as fh:
            sql = fh.read()
    else:
        sql = sys.stdin.read()
    print(json.dumps(parse_lineage(sql), indent=2))


if __name__ == "__main__":
    main(sys.argv)
