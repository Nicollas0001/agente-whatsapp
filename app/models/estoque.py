from sqlalchemy import Table, Column, Integer, String
from app.models.database import metadata

estoque = Table(
    "estoque", metadata,
    Column("id", Integer, primary_key=True),
    Column("sku", String(50)),
    Column("tipo", String(10)),
    Column("quantidade", Integer),
)
