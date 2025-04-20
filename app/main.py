from fastapi import FastAPI
from app.routes import produtos, clientes, vendas, estoque, zap
from app.models import database

app = FastAPI(title="Agente Completo com WhatsApp + Pix")

database.create_tables()

app.include_router(produtos.router, prefix="/produtos", tags=["Produtos"])
app.include_router(clientes.router, prefix="/clientes", tags=["Clientes"])
app.include_router(vendas.router, prefix="/vendas", tags=["Vendas"])
app.include_router(estoque.router, prefix="/estoque", tags=["Estoque"])
app.include_router(zap.router, prefix="/zap", tags=["WhatsApp"])

@app.get("/")
def home():
    return {"msg": "Agente completo rodando com todos os módulos"}
