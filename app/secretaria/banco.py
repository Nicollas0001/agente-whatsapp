"""Conexão com o Firestore: o mesmo banco do app de finanças guarda tudo."""
import base64
import json
import os

from google.cloud import firestore

_cliente = None


def _credenciais() -> dict:
    bruto = os.getenv("FIREBASE_CREDENCIAIS", "").strip()
    if not bruto:
        raise RuntimeError("FIREBASE_CREDENCIAIS não configurado (JSON da conta de serviço do Firebase)")
    if not bruto.startswith("{"):
        bruto = base64.b64decode(bruto).decode()  # aceita o JSON em base64 também
    return json.loads(bruto)


def configurado() -> bool:
    return bool(os.getenv("FIREBASE_CREDENCIAIS") or os.getenv("FIRESTORE_EMULATOR_HOST"))


def cliente() -> firestore.Client:
    global _cliente
    if _cliente is None:
        if os.getenv("FIRESTORE_EMULATOR_HOST"):
            _cliente = firestore.Client(project=os.getenv("FIREBASE_PROJECT_ID", "demo-secretaria"))
        else:
            _cliente = firestore.Client.from_service_account_info(_credenciais())
    return _cliente
