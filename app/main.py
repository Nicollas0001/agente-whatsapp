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

# ✅ Adicione isso no final do arquivo
if __name__ == "__main__":
    import os
    import uvicorn

    port = int(os.environ.get("PORT", 10000))
    uvicorn.run("app.main:app", host="0.0.0.0", port=port, reload=False)
