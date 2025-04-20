from sqlalchemy import Table, Column, Integer, String
from app.models.database import metadata

clientes = Table(
    "clientes", metadata,
    Column("id", Integer, primary_key=True),
    Column("nome", String(255)),
    Column("cpf_cnpj", String(20)),
    Column("telefone", String(20)),
)
