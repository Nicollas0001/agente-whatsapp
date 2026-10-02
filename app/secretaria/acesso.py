"""Quem pode usar a API do app: só a conta Google do dono (DONO_EMAIL).

O app manda o token do login do Firebase; aqui ele é validado com as chaves
públicas do Google. Com o emulador de login (só em testes), o token não é assinado.
"""
import os
import threading
import time

from fastapi import Header, HTTPException
from google.auth import jwt
from google.auth.transport import requests as transporte
from google.oauth2 import id_token

from app.secretaria import banco, config

_validados = {}
_trava = threading.Lock()


def projeto() -> str:
    return os.getenv("FIREBASE_PROJECT_ID") or banco.cliente().project


def verificar_token(token: str) -> dict:
    agora = time.time()
    with _trava:
        guardado = _validados.get(token)
    if guardado and guardado.get("exp", 0) > agora + 30:
        return guardado
    if os.getenv("FIREBASE_AUTH_EMULATOR_HOST"):
        dados = jwt.decode(token, verify=False)
    else:
        dados = id_token.verify_firebase_token(token, transporte.Request(), audience=projeto())
    with _trava:
        if len(_validados) > 50:
            _validados.clear()
        _validados[token] = dados
    return dados


def dono(authorization: str = Header(default="")) -> dict:
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="faça login")
    try:
        dados = verificar_token(token)
    except Exception:
        raise HTTPException(status_code=401, detail="login inválido ou expirado")
    email = str(dados.get("email") or "").lower()
    if not config.DONO_EMAIL or email != config.DONO_EMAIL or not dados.get("email_verified"):
        raise HTTPException(status_code=403, detail="essa conta não tem acesso")
    return dados
