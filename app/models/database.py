import os

from sqlalchemy import create_engine, MetaData
from sqlalchemy.orm import sessionmaker

# Em produção use um Postgres (Neon, Supabase...): o disco do Render é apagado
# a cada deploy/reinício, então SQLite lá perde tudo.
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./agente.db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=_connect_args, pool_pre_ping=True)
metadata = MetaData()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def create_tables():
    from app.models import produto, cliente, venda, estoque
    from app.secretaria import modelos
    metadata.create_all(bind=engine)
