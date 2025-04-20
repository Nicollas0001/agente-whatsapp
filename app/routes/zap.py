from fastapi import APIRouter, Request
import requests

router = APIRouter()

ZAPI_INSTANCE = "3DFEBC76D35C60755AF8FA8592F99CB9"
ZAPI_TOKEN = "SEU_TOK108648BD703ADBBBE798F920EN"
ZAPI_URL = f"https://api.z-api.io/instances/{ZAPI_INSTANCE}"

def enviar_whatsapp(numero: str, mensagem: str):
    try:
        print(f"📤 Enviando para {numero}: {mensagem}")
        response = requests.post(
            f"{ZAPI_URL}/send-message",
            headers={"Client-Token": ZAPI_TOKEN},
            json={"phone": numero, "message": mensagem}
        )
        print("✅ Resposta Z-API:", response.text)
    except Exception as e:
        print("❌ Erro ao enviar:", e)

@router.post("/webhook")
async def receber_msg(request: Request):
    dados = await request.json()
    print("📥 Mensagem recebida:", dados)

    numero = dados.get("phone", "")
    msg = dados.get("text", {}).get("message", "").lower()

    if "pix" in msg:
        resposta = "💸 Aqui está seu link de pagamento: https://pix.seusite.com/link123"
    else:
        resposta = "🤖 Resposta automática: diga 'pix' para pagar"

    enviar_whatsapp(numero, resposta)
    return {"status": "ok"}
