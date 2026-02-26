import pytest
from sqlalchemy import Column, Integer, String, select
from sqlalchemy.orm import declarative_base
from khorsyio.db.query import apply_filters, apply_order, apply_cursor

Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    name = Column(String)
    age = Column(Integer)

def test_apply_filters():
    stmt = select(User)
    
    # Simple eq
    s = apply_filters(stmt, User, {"name": "Bob"})
    assert "WHERE users.name = :name_1" in str(s)
    
    # Multiple ops
    s = apply_filters(stmt, User, {"age__gte": 18, "name__icontains": "al"})
    s_str = str(s)
    assert "users.age >= :age_1" in s_str
    assert "lower(users.name) LIKE " in s_str
    
    # In op - Relaxed check for SQLAlchemy compilation
    s = apply_filters(stmt, User, {"id__in": [1, 2, 3]})
    assert "users.id IN (" in str(s)

def test_apply_order():
    stmt = select(User)
    
    # ASC
    s = apply_order(stmt, User, "name")
    assert "ORDER BY users.name ASC" in str(s)
    
    # DESC
    s = apply_order(stmt, User, "-age")
    assert "ORDER BY users.age DESC" in str(s)
    
    # Multiple
    s = apply_order(stmt, User, ["-age", "id"])
    assert "ORDER BY users.age DESC, users.id ASC" in str(s)

def test_apply_cursor():
    stmt = select(User)
    
    # ASC cursor
    s = apply_cursor(stmt, User, cursor_value=10, cursor_field="id", order="asc")
    assert "WHERE users.id > :id_1" in str(s)
    
    # DESC cursor
    s = apply_cursor(stmt, User, cursor_value=100, cursor_field="id", order="desc")
    assert "WHERE users.id < :id_1" in str(s)
