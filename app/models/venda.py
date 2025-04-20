from sqlalchemy import Table, Column, Integer, Float, ForeignKey
from app.models.database import metadata

vendas = Table(
    "vendas", metadata,
    Column("id", Integer, primary_key=True),
    Column("cliente_id", Integer, ForeignKey("clientes.id")),
    Column("produto_id", Integer, ForeignKey("produtos.id")),
    Column("quantidade", Integer),
    Column("total", Float),
)
