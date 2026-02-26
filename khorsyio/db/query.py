from __future__ import annotations

import math
from typing import Any, Iterable, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import InstrumentedAttribute
from sqlalchemy.sql import Select
from sqlalchemy.ext.asyncio import AsyncSession


def _get_attr(model, name: str) -> InstrumentedAttribute:
    if hasattr(model, name):
        return getattr(model, name)
    # dotted notation not supported in this minimal helper
    raise AttributeError(f"Unknown field '{name}' on {model}")


def apply_filters(stmt: Select, model, filters: dict[str, Any] | None) -> Select:
    """Apply simple filters to a SQLAlchemy Select statement.

    Supported operations:
    - exact match: {"name": "Bob"}
    - with operation: {"age__gte": 18, "name__icontains": "bo"}

    Available ops: eq, ne, lt, lte, gt, gte, in, contains, icontains,
    startswith, istartswith, endswith, iendswith, isnull, between
    """
    if not filters:
        return stmt

    conditions = []
    for key, value in filters.items():
        if "__" in key:
            field, op = key.split("__", 1)
        else:
            field, op = key, "eq"
        col = _get_attr(model, field)
        op = op.lower()

        if op == "eq":
            conditions.append(col == value)
        elif op == "ne":
            conditions.append(col != value)
        elif op == "lt":
            conditions.append(col < value)
        elif op == "lte":
            conditions.append(col <= value)
        elif op == "gt":
            conditions.append(col > value)
        elif op == "gte":
            conditions.append(col >= value)
        elif op == "in":
            conditions.append(col.in_(value if isinstance(value, (list, tuple, set)) else [value]))
        elif op == "contains":
            conditions.append(col.contains(value))
        elif op == "icontains":
            conditions.append(func.lower(col).contains(str(value).lower()))
        elif op == "startswith":
            conditions.append(col.startswith(value))
        elif op == "istartswith":
            conditions.append(func.lower(col).startswith(str(value).lower()))
        elif op == "endswith":
            conditions.append(col.endswith(value))
        elif op == "iendswith":
            conditions.append(func.lower(col).endswith(str(value).lower()))
        elif op in ("isnull", "is_null"):
            conditions.append(col.is_(None) if value else col.is_not(None))
        elif op == "between":
            assert isinstance(value, (list, tuple)) and len(value) == 2, "between expects [min, max]"
            conditions.append(col.between(value[0], value[1]))
        else:
            raise ValueError(f"Unsupported filter op: {op}")

    if conditions:
        stmt = stmt.where(*conditions)
    return stmt


def apply_order(stmt: Select, model, order: Sequence[str] | str | None) -> Select:
    if order is None:
        return stmt
    if isinstance(order, str):
        order = [order]
    clauses = []
    for item in order:
        if not item:
            continue
        direction = "asc"
        name = item
        if item[0] in ("-", "+"):
            direction = "desc" if item[0] == "-" else "asc"
            name = item[1:]
        col = _get_attr(model, name)
        clauses.append(col.desc() if direction == "desc" else col.asc())
    if clauses:
        stmt = stmt.order_by(*clauses)
    return stmt


async def paginate(session: AsyncSession, stmt: Select, page: int = 1, per_page: int = 20) -> dict[str, Any]:
    """Execute paginated query returning dict with items and meta.

    Returns: {"items": [...], "total": int, "page": int, "per_page": int, "pages": int}
    Items are returned as ORM instances when selecting a model, otherwise list of dicts.
    """
    page = max(1, int(page))
    per_page = max(1, int(per_page))

    # total count
    count_stmt = select(func.count()).select_from(stmt.order_by(None).subquery())
    total = int((await session.execute(count_stmt)).scalar_one())

    # page slice
    stmt2 = stmt.limit(per_page).offset((page - 1) * per_page)
    result = await session.execute(stmt2)
    # Heuristic: if selecting a single entity/column, use scalars(), else mappings
    try:
        if len(result.keys()) == 1:
            items = result.scalars().all()
        else:
            items = [dict(m) for m in result.mappings().all()]
    except Exception:
        # Fallback to mappings
        items = [dict(m) for m in result.mappings().all()]

    pages = max(1, math.ceil(total / per_page)) if total else 0
    return {"items": items, "total": total, "page": page, "per_page": per_page, "pages": pages}


def apply_cursor(stmt: Select, model, cursor_value: Any | None, cursor_field: str = "id", order: str = "asc") -> Select:
    """Apply simple cursor filtering by a monotonically increasing field (e.g., id or created_at)."""
    if cursor_value is None:
        return stmt
    col = _get_attr(model, cursor_field)
    if order.lower() == "asc":
        return stmt.where(col > cursor_value)
    return stmt.where(col < cursor_value)
