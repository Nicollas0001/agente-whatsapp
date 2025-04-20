from fastapi import APIRouter, Request
from sqlalchemy import select
from app.models.produto import produtos
from app.models.database import SessionLocal
import requests

router = APIRouter()

ZAPI_INSTANCE = "SEU_ID"
ZAPI_TOKEN = "SEU_TOKEN"
ZAPI_URL = f"https://v2.z-api.io/instances/{ZAPI_INSTANCE}/token/{ZAPI_TOKEN}/send-message"

def enviar_whatsapp(numero: str, mensagem: str):
    print("📤 Enviando mensagem para", numero)
    try:
        resposta = requests.post(
            ZAPI_URL,
            json={"phone": numero, "message": mensagem}
        )
        print("📬 Resposta da Z-API:", resposta.status_code, resposta.text)
    except Exception as e:
        print("❌ Erro ao enviar mensagem:", str(e))

@router.post("/webhook")
async def receber_msg(request: Request):
    dados = await request.json()

    numero = dados.get("phone", "")
    msg = dados.get("text", {}).get("message", "").lower()
    print("📥 Mensagem recebida:", msg)

    db = SessionLocal()
    resposta = "Olá! Digite o nome de um produto para consultar ou 'pix' para pagar."

    if "pix" in msg:
        resposta = "Aqui está o link de pagamento: https://pix.seusite.com/link123"
    else:
        produtos_lista = db.execute(select(produtos)).fetchall()
        for p in produtos_lista:
            p_mapping = dict(p._mapping)
            if p_mapping["nome"].lower() in msg:
                resposta = f"{p_mapping['nome']} custa R$ {p_mapping['preco']:.2f}"
                break

    print("✅ Vai enviar resposta automática...")
    enviar_whatsapp(numero, resposta)
    return {"status": "ok"}
