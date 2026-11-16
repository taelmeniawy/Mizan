from __future__ import annotations

from datetime import datetime


def next_number(conn, name: str, year: int | None = None, prefix: str = "") -> str:
    year = year or datetime.now().year
    row = conn.execute("SELECT id,next_value,prefix FROM sequences WHERE name=? AND year=?", (name, year)).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO sequences(name,year,next_value,prefix) VALUES(?,?,?,?)",
            (name, year, 2, prefix),
        )
        value = 1
    else:
        value = int(row["next_value"])
        conn.execute("UPDATE sequences SET next_value=? WHERE id=?", (value + 1, row["id"]))
        prefix = row["prefix"] or prefix
    return f"{prefix}{year}-{value:06d}"
