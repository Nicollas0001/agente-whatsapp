from sqlalchemy import Table, Column, Integer, String, Float
from app.models.database import metadata

produtos = Table(
    "produtos", metadata,
    Column("id", Integer, primary_key=True),
    Column("nome", String(255)),
    Column("preco", Float),
    Column("estoque", Integer),
)
