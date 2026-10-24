def _render_table(table: dict) -> str:
    """One table as the lines the model reads.

    Structure only -- names, types, and what joins to what. That is the same
    boundary the document itself was built to enforce, applied a second time at
    the point it costs something: if a customer ever puts a comment or a sampled
    value into the document, this is where it would leak, so nothing here reads
    anything but the structural keys.
    """
    lines = [f"table {table['name']}:"]
    for column in table["columns"]:
        marks = " PRIMARY KEY" if column["primary_key"] else ""
        marks += " NOT NULL" if not column["nullable"] else ""
        lines.append(f"  {column['name']}: {column['type']}{marks}")
    for unique in table.get("unique", []):
        lines.append(f"  UNIQUE({', '.join(unique)})")
    for fk in table.get("foreign_keys", []):
        rule = f" ON DELETE {fk['on_delete']}" if fk.get("on_delete") else ""
        lines.append(
            f"  FK({', '.join(fk['columns'])}) -> {fk['references']}{rule}")
    return "\n".join(lines)


def budget_context(schema_doc: dict, *, max_tokens: int = 12_000) -> tuple[str, bool]:
    """Return (schema text, was_truncated)."""
    tables = schema_doc.get("tables", [])
    kept, used = [], 0

    for table in tables:
        rendered = _render_table(table)
        cost = len(rendered) // 3
        if used + cost > max_tokens:
            break
        kept.append(rendered)
        used += cost

    return "\n\n".join(kept), len(kept) < len(tables)
