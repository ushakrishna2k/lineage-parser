"""Power BI Power Query (M language) lineage parser.

Parses let...in M scripts and extracts:
  - table_lineage   : external source tables and the final output step
  - steps           : every transformation step with type and inputs
  - joins           : Table.NestedJoin calls with join type and keys
  - column_operations: expand / select / rename / remove / add-column steps
  - filter_conditions: Table.SelectRows predicates

Usage:
  python pbi_lineage_parser.py path/to/script.m
  cat script.m | python pbi_lineage_parser.py
"""

import re
import sys
import json
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Low-level text helpers
# ---------------------------------------------------------------------------

def _strip_comments(text: str) -> str:
    """Remove // line comments and /* block comments */."""
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.DOTALL)
    text = re.sub(r'//[^\n]*', '', text)
    return text


def _split_top_level(text: str, delimiter: str = ',') -> List[str]:
    """Split *text* on *delimiter* only at depth-0 (not inside brackets/quotes)."""
    parts: List[str] = []
    current: List[str] = []
    depth = 0
    in_str = False
    i = 0
    dlen = len(delimiter)

    while i < len(text):
        ch = text[i]

        if in_str:
            current.append(ch)
            if ch == '"':
                in_str = False
            i += 1
            continue

        if ch == '"':
            in_str = True
            current.append(ch)
            i += 1
            continue

        if ch in '([{':
            depth += 1
            current.append(ch)
            i += 1
            continue

        if ch in ')]}':
            depth -= 1
            current.append(ch)
            i += 1
            continue

        if depth == 0 and text[i:i + dlen] == delimiter:
            parts.append(''.join(current).strip())
            current = []
            i += dlen
            continue

        current.append(ch)
        i += 1

    tail = ''.join(current).strip()
    if tail:
        parts.append(tail)

    return parts


def _extract_args(call_text: str) -> List[str]:
    """Given 'Func(a, {b,c}, d)' return ['a', '{b,c}', 'd']."""
    m = re.match(r'[^(]+\((.*)\)\s*$', call_text, re.DOTALL)
    if not m:
        return []
    return _split_top_level(m.group(1))


def _parse_list_literal(text: str) -> List[str]:
    """Parse '{\"a\", \"b\", c}' or '{a, b}' into a Python list of strings."""
    text = text.strip()
    if text.startswith('{') and text.endswith('}'):
        text = text[1:-1]
    items = _split_top_level(text)
    result = []
    for item in items:
        item = item.strip().strip('"')
        if item:
            result.append(item)
    return result


# ---------------------------------------------------------------------------
# Parse let…in block
# ---------------------------------------------------------------------------

def _parse_let_in(m_text: str) -> Tuple[List[Tuple[str, str]], str]:
    """Return ([(name, expr), ...], output_name) from a let…in block."""
    text = _strip_comments(m_text).strip()

    # Locate 'let' … 'in'
    let_match = re.match(r'\blet\b(.*)\bin\b\s*(.+)$', text, re.DOTALL | re.IGNORECASE)
    if not let_match:
        raise ValueError("No 'let…in' structure found in M script.")

    body = let_match.group(1).strip()
    output_expr = let_match.group(2).strip()

    assignments: List[Tuple[str, str]] = []
    for chunk in _split_top_level(body, ','):
        chunk = chunk.strip()
        if not chunk:
            continue
        eq_idx = chunk.index('=')
        name = chunk[:eq_idx].strip()
        expr = chunk[eq_idx + 1:].strip()
        assignments.append((name, expr))

    return assignments, output_expr


# ---------------------------------------------------------------------------
# Expression classifiers
# ---------------------------------------------------------------------------

_TABLE_FUNC_RE = re.compile(
    r'^(Table\.[A-Za-z]+)\s*\(', re.IGNORECASE
)


def _func_name(expr: str) -> Optional[str]:
    m = _TABLE_FUNC_RE.match(expr.strip())
    return m.group(1) if m else None


def _join_kind(text: str) -> str:
    m = re.search(r'JoinKind\.(\w+)', text)
    return m.group(1) if m else "Inner"


# ---------------------------------------------------------------------------
# Per-step parsers
# ---------------------------------------------------------------------------

def _parse_step(name: str, expr: str, defined_names: set) -> Dict[str, Any]:
    func = _func_name(expr)

    # ── Simple identifier (source reference or alias) ──────────────────────
    if func is None:
        ref = expr.strip()
        step_type = "source" if ref not in defined_names else "alias"
        return {"name": name, "type": step_type, "reference": ref}

    args = _extract_args(expr)
    func_lower = func.lower()

    # ── Table.NestedJoin ────────────────────────────────────────────────────
    if func_lower == "table.nestedjoin":
        # Table.NestedJoin(left, leftKeys, right, rightKeys, newCol, JoinKind)
        left   = args[0].strip() if len(args) > 0 else None
        lkeys  = _parse_list_literal(args[1]) if len(args) > 1 else []
        right  = args[2].strip() if len(args) > 2 else None
        rkeys  = _parse_list_literal(args[3]) if len(args) > 3 else []
        new_col = args[4].strip().strip('"') if len(args) > 4 else None
        jkind  = _join_kind(args[5]) if len(args) > 5 else "Inner"
        return {
            "name": name,
            "type": "join",
            "left_table": left,
            "right_table": right,
            "join_type": jkind,
            "left_keys": lkeys,
            "right_keys": rkeys,
            "new_column": new_col,
        }

    # ── Table.ExpandTableColumn ─────────────────────────────────────────────
    if func_lower == "table.expandtablecolumn":
        # Table.ExpandTableColumn(table, col, {columns})
        source   = args[0].strip() if len(args) > 0 else None
        src_col  = args[1].strip().strip('"') if len(args) > 1 else None
        columns  = _parse_list_literal(args[2]) if len(args) > 2 else []
        return {
            "name": name,
            "type": "expand",
            "source": source,
            "expanded_column": src_col,
            "output_columns": columns,
        }

    # ── Table.SelectColumns ─────────────────────────────────────────────────
    if func_lower == "table.selectcolumns":
        source  = args[0].strip() if len(args) > 0 else None
        columns = _parse_list_literal(args[1]) if len(args) > 1 else []
        return {
            "name": name,
            "type": "select_columns",
            "source": source,
            "columns": columns,
        }

    # ── Table.RemoveColumns ─────────────────────────────────────────────────
    if func_lower == "table.removecolumns":
        source  = args[0].strip() if len(args) > 0 else None
        columns = _parse_list_literal(args[1]) if len(args) > 1 else []
        return {
            "name": name,
            "type": "remove_columns",
            "source": source,
            "columns": columns,
        }

    # ── Table.RenameColumns ─────────────────────────────────────────────────
    if func_lower == "table.renamecolumns":
        source = args[0].strip() if len(args) > 0 else None
        pairs_raw = _split_top_level(args[1][1:-1]) if len(args) > 1 else []
        pairs = []
        for pair in pairs_raw:
            p = _parse_list_literal(pair)
            if len(p) == 2:
                pairs.append({"from": p[0], "to": p[1]})
        return {
            "name": name,
            "type": "rename_columns",
            "source": source,
            "renames": pairs,
        }

    # ── Table.AddColumn ─────────────────────────────────────────────────────
    if func_lower == "table.addcolumn":
        source   = args[0].strip() if len(args) > 0 else None
        col_name = args[1].strip().strip('"') if len(args) > 1 else None
        formula  = args[2].strip() if len(args) > 2 else None
        return {
            "name": name,
            "type": "add_column",
            "source": source,
            "new_column": col_name,
            "formula": formula,
        }

    # ── Table.Distinct ──────────────────────────────────────────────────────
    if func_lower == "table.distinct":
        source = args[0].strip() if len(args) > 0 else None
        keys   = _parse_list_literal(args[1]) if len(args) > 1 else []
        return {
            "name": name,
            "type": "distinct",
            "source": source,
            "keys": keys,
        }

    # ── Table.SelectRows (filter) ────────────────────────────────────────────
    if func_lower == "table.selectrows":
        source    = args[0].strip() if len(args) > 0 else None
        predicate = args[1].strip() if len(args) > 1 else None
        return {
            "name": name,
            "type": "filter",
            "source": source,
            "predicate": predicate,
        }

    # ── Table.Group ─────────────────────────────────────────────────────────
    if func_lower == "table.group":
        source = args[0].strip() if len(args) > 0 else None
        keys   = _parse_list_literal(args[1]) if len(args) > 1 else []
        return {
            "name": name,
            "type": "group",
            "source": source,
            "group_keys": keys,
        }

    # ── Table.Sort ──────────────────────────────────────────────────────────
    if func_lower == "table.sort":
        source = args[0].strip() if len(args) > 0 else None
        return {"name": name, "type": "sort", "source": source}

    # ── Table.Join (direct join variant) ────────────────────────────────────
    if func_lower == "table.join":
        left  = args[0].strip() if len(args) > 0 else None
        lkeys = _parse_list_literal(args[1]) if len(args) > 1 else []
        right = args[2].strip() if len(args) > 2 else None
        rkeys = _parse_list_literal(args[3]) if len(args) > 3 else []
        jkind = _join_kind(args[4]) if len(args) > 4 else "Inner"
        return {
            "name": name,
            "type": "join",
            "left_table": left,
            "right_table": right,
            "join_type": jkind,
            "left_keys": lkeys,
            "right_keys": rkeys,
            "new_column": None,
        }

    # ── Generic Table.* fallback ────────────────────────────────────────────
    source = args[0].strip() if args else None
    return {
        "name": name,
        "type": func,
        "source": source,
        "raw_args": args,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_pbi_lineage(m_text: str) -> Dict[str, Any]:
    """Parse a Power Query M script and return full lineage.

    Returns a dict with keys:
      table_lineage, steps, joins, column_operations, filter_conditions
    """
    assignments, output_expr = _parse_let_in(m_text)

    defined_names = {name for name, _ in assignments}

    steps: List[Dict] = []
    for name, expr in assignments:
        step = _parse_step(name, expr, defined_names - {name})
        steps.append(step)

    # Source tables: steps that reference something outside the let block
    sources: List[str] = []
    for step in steps:
        if step["type"] == "source":
            ref = step["reference"]
            if ref not in sources:
                sources.append(ref)

    joins        = [s for s in steps if s["type"] == "join"]
    col_ops      = [s for s in steps if s["type"] in
                    ("expand", "select_columns", "remove_columns",
                     "rename_columns", "add_column")]
    filter_conds = [s for s in steps if s["type"] == "filter"]

    return {
        "table_lineage": {
            "sources": sources,
            "target": output_expr,
        },
        "steps": steps,
        "joins": joins,
        "column_operations": col_ops,
        "filter_conditions": filter_conds,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: List[str]) -> None:
    if len(argv) >= 2:
        with open(argv[1], "r", encoding="utf-8") as fh:
            m_text = fh.read()
    else:
        m_text = sys.stdin.read()
    print(json.dumps(parse_pbi_lineage(m_text), indent=2))


if __name__ == "__main__":
    main(sys.argv)
