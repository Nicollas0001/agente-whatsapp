import os
import tempfile
from datetime import datetime

import pytest

_banco = os.path.join(tempfile.mkdtemp(), "teste.db")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_banco}")
os.environ["TELEGRAM_BOT_TOKEN"] = "token-de-teste"
os.environ["TELEGRAM_OWNER_ID"] = "42"
os.environ["SECRETARIA_ESPERA_AGRUPAR_SEG"] = "0"
os.environ.pop("CRON_SECRET", None)

from app.models import database  # noqa: E402
from app.secretaria import modelos, repositorio, telegram  # noqa: E402

database.create_tables()


@pytest.fixture(autouse=True)
def limpar_banco():
    with database.engine.begin() as conn:
        for tabela in (modelos.tarefas, modelos.registros, modelos.lembretes,
                       modelos.memorias, modelos.mensagens, modelos.estado):
            conn.execute(tabela.delete())
    yield


@pytest.fixture
def db():
    sessao = database.SessionLocal()
    yield sessao
    sessao.close()


@pytest.fixture
def relogio(monkeypatch):
    """Congela o "agora" da secretária. Use relogio.ajustar(datetime(...))."""
    class Relogio:
        atual = datetime(2026, 10, 1, 9, 0)  # quinta-feira

        def ajustar(self, momento):
            self.atual = momento

    r = Relogio()
    monkeypatch.setattr(repositorio, "agora", lambda: r.atual)
    return r


@pytest.fixture
def telegram_falso(monkeypatch):
    enviados = []

    def api(metodo, **dados):
        if metodo == "sendMessage":
            enviados.append(dados)
        return {"ok": True}

    monkeypatch.setattr(telegram, "_api", api)
    return enviados
