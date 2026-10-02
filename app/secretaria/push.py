"""Notificações no tablet (Web Push). Grátis: o Chrome entrega pela infraestrutura do Google.

As chaves VAPID são geradas na primeira vez e guardadas no Firestore
(sec_estado/vapid), então não há nada para configurar.
"""
import base64
import hashlib
import json
import logging

from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from py_vapid import Vapid02
from pywebpush import WebPushException, webpush

from app.secretaria import banco, config, repositorio as repo

log = logging.getLogger("secretaria.push")
_vapid = None


def _chaves() -> Vapid02:
    global _vapid
    if _vapid is None:
        salvo = repo.estado_get("vapid")
        if salvo:
            _vapid = Vapid02.from_pem(salvo.encode())
        else:
            _vapid = Vapid02()
            _vapid.generate_keys()
            repo.estado_set("vapid", _vapid.private_pem().decode())
    return _vapid


def chave_publica() -> str:
    """applicationServerKey que o navegador usa para se inscrever."""
    bruto = _chaves().public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    return base64.urlsafe_b64encode(bruto).rstrip(b"=").decode()


def _col():
    return banco.cliente().collection("sec_push")


def _id(endpoint: str) -> str:
    return hashlib.sha256(endpoint.encode()).hexdigest()[:40]


def salvar_inscricao(inscricao: dict) -> None:
    endpoint = str(inscricao.get("endpoint") or "")
    chaves = inscricao.get("keys") or {}
    if not endpoint.startswith("https://") or not chaves.get("p256dh") or not chaves.get("auth"):
        raise ValueError("inscrição de notificação inválida")
    _col().document(_id(endpoint)).set({
        "endpoint": endpoint, "keys": {"p256dh": chaves["p256dh"], "auth": chaves["auth"]},
        "criada_em": repo.agora().isoformat(),
    })


def remover_inscricao(endpoint: str) -> None:
    _col().document(_id(endpoint)).delete()


def inscricoes() -> list:
    return [s.to_dict() for s in _col().stream()]


def enviar(titulo: str, corpo: str, url: str = "./#secretaria", tag: str = "secretaria") -> int:
    """Manda para todos os aparelhos inscritos. Devolve quantos aceitaram."""
    carga = json.dumps({"titulo": titulo, "corpo": corpo[:240], "url": url, "tag": tag}, ensure_ascii=False)
    contato = f"mailto:{config.DONO_EMAIL}" if config.DONO_EMAIL else "mailto:secretaria@example.com"
    entregues = 0
    for inscricao in inscricoes():
        try:
            webpush(subscription_info={"endpoint": inscricao["endpoint"], "keys": inscricao["keys"]},
                    data=carga, vapid_private_key=_chaves(), vapid_claims={"sub": contato},
                    ttl=24 * 3600, timeout=15)
            entregues += 1
        except WebPushException as erro:
            status = getattr(erro.response, "status_code", None)
            if status in (404, 410):  # o aparelho cancelou a inscrição
                remover_inscricao(inscricao["endpoint"])
            else:
                log.warning("Push falhou (%s): %s", status, erro)
    return entregues
