from datetime import date, datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.secretaria import config, ia, recorrencia, rotinas, repositorio as repo


# ---------- recorrência ----------

@pytest.mark.parametrize("regra,depois_de,esperado", [
    ("diaria", date(2026, 10, 1), date(2026, 10, 2)),
    ("dias_uteis", date(2026, 10, 2), date(2026, 10, 5)),      # sexta -> segunda
    ("semanal:seg,qua", date(2026, 10, 1), date(2026, 10, 5)),  # quinta -> segunda
    ("semanal:qui", date(2026, 10, 1), date(2026, 10, 8)),      # estritamente depois
    ("mensal:10", date(2026, 10, 1), date(2026, 10, 10)),
    ("mensal:10", date(2026, 10, 10), date(2026, 11, 10)),
    ("mensal:31", date(2026, 10, 31), date(2026, 11, 30)),      # mês curto
    ("mensal:5", date(2026, 12, 20), date(2027, 1, 5)),
    ("a_cada:3", date(2026, 10, 1), date(2026, 10, 4)),
])
def test_proxima_data(regra, depois_de, esperado):
    assert recorrencia.proxima_data(regra, depois_de) == esperado


def test_recorrencia_invalida():
    with pytest.raises(ValueError):
        recorrencia.validar("toda hora")
    assert recorrencia.validar("Semanal:Sexta,seg") == "semanal:seg,sex"


# ---------- tarefas ----------

def test_concluir_tarefa_repetitiva_cria_proxima(db, relogio):
    tid = repo.criar_tarefa(db, {"titulo": "Pagar condomínio", "area": "pessoal",
                                 "recorrencia": "mensal:10", "prazo": "2026-10-10",
                                 "agendada_para": "2026-10-08"})
    resultado = repo.concluir_tarefa(db, tid)
    assert "próxima repetição" in resultado
    abertas = repo.tarefas_abertas(db)
    assert len(abertas) == 1
    # feita adiantada em 01/10: a próxima é a de novembro, com a mesma antecedência de 2 dias
    assert (abertas[0].prazo, abertas[0].agendada_para) == (date(2026, 11, 10), date(2026, 11, 8))
    feitos = repo.registros_periodo(db, date(2026, 10, 1), date(2026, 10, 1))
    assert [r.tarefa_id for r in feitos] == [tid]


def test_habito_repetitivo_feito_tarde_nao_acumula(db, relogio):
    tid = repo.criar_tarefa(db, {"titulo": "Academia", "area": "pessoal",
                                 "recorrencia": "semanal:seg,qua,sex", "agendada_para": "2026-09-28"})
    repo.concluir_tarefa(db, tid)  # concluída na quinta 01/10
    assert repo.tarefas_abertas(db)[0].agendada_para == date(2026, 10, 2)


def test_rolar_atrasadas_conta_adiamento_so_de_tarefa_normal(db, relogio):
    normal = repo.criar_tarefa(db, {"titulo": "Mandar proposta", "area": "profissional",
                                    "agendada_para": "2026-09-29"})
    academia = repo.criar_tarefa(db, {"titulo": "Academia", "area": "pessoal",
                                      "recorrencia": "semanal:seg,qua,sex", "agendada_para": "2026-09-30"})
    conta = repo.criar_tarefa(db, {"titulo": "Pagar luz", "area": "pessoal", "recorrencia": "mensal:30",
                                   "prazo": "2026-09-30", "agendada_para": "2026-09-30"})
    assert repo.rolar_atrasadas(db, date(2026, 10, 1)) == 3
    t_normal, t_academia, t_conta = (repo.obter_tarefa(db, i) for i in (normal, academia, conta))
    assert (t_normal.agendada_para, t_normal.adiamentos) == (date(2026, 10, 1), 1)
    assert (t_academia.agendada_para, t_academia.adiamentos) == (date(2026, 10, 2), 0)
    assert (t_conta.agendada_para, t_conta.adiamentos) == (date(2026, 10, 1), 1)  # conta não some


def test_reagendar_para_frente_conta_adiamento(db, relogio):
    tid = repo.criar_tarefa(db, {"titulo": "Relatório", "area": "profissional", "agendada_para": "2026-10-01"})
    repo.atualizar_tarefa(db, tid, {"agendada_para": "2026-10-05"})
    assert repo.obter_tarefa(db, tid).adiamentos == 1
    # planejar algo futuro para mais longe ainda não é adiar
    repo.atualizar_tarefa(db, tid, {"agendada_para": "2026-10-07"})
    assert repo.obter_tarefa(db, tid).adiamentos == 1


def test_contexto_mostra_o_essencial(db, relogio):
    a = repo.criar_tarefa(db, {"titulo": "Pedir orçamento", "area": "pessoal", "contexto": "telefone"})
    repo.criar_tarefa(db, {"titulo": "Reformar banheiro", "area": "pessoal", "prazo": "2026-11-20",
                           "depende_de": [a], "esforco_min": 240})
    repo.salvar_memoria(db, "Trabalha das 8h às 17h")
    texto = repo.montar_contexto(db)
    assert "quinta-feira, 01/10/2026 09:00" in texto
    assert "sex 02/10" in texto
    assert "depende de #" in texto and "ctx: telefone" in texto
    assert "Trabalha das 8h às 17h" in texto


# ---------- ferramentas da IA ----------

def test_ferramentas_cobrem_o_fluxo(db, relogio):
    saida = ia.executar_ferramenta(db, "criar_tarefas", {"tarefas": [
        {"titulo": "Ligar pro contador", "area": "profissional", "agendada_para": "2026-10-01"},
        {"titulo": "Comprar presente", "area": "pessoal", "prazo": "2026-10-12"},
    ]})
    assert saida.startswith("criadas: #")
    ids = [t.id for t in repo.tarefas_abertas(db)]
    assert "concluída" in ia.executar_ferramenta(db, "concluir_tarefas", {"itens": [{"id": ids[0]}]})
    ia.executar_ferramenta(db, "registrar_feito", {"itens": [{"texto": "Reunião com o time", "area": "profissional"}]})
    registros = ia.executar_ferramenta(db, "buscar_registros", {"inicio": "2026-10-01", "fim": "2026-10-01"})
    assert "Ligar pro contador" in registros and "Reunião com o time" in registros
    assert "agendado" in ia.executar_ferramenta(db, "criar_lembrete", {"quando": "2026-10-01 15:00", "texto": "Buscar exame"})
    assert "guardado como m" in ia.executar_ferramenta(db, "lembrar_sobre_mim", {"fato": "Prefere tarefas pesadas de manhã"})
    assert "status: feita" in ia.executar_ferramenta(db, "buscar_tarefas", {"status": "feita"})
    with pytest.raises(ValueError):
        ia.executar_ferramenta(db, "atualizar_tarefa", {"id": 9999, "prioridade": 1})


def _resposta(stop_reason, *blocos):
    return SimpleNamespace(stop_reason=stop_reason, content=list(blocos))


def test_laco_executa_ferramentas_e_devolve_texto(db, relogio, monkeypatch):
    chamadas = []
    respostas = iter([
        _resposta("tool_use",
                  SimpleNamespace(type="thinking", thinking=""),
                  SimpleNamespace(type="tool_use", id="t1", name="criar_tarefas",
                                  input={"tarefas": [{"titulo": "Pagar IPVA", "area": "pessoal"}]}),
                  SimpleNamespace(type="tool_use", id="t2", name="atualizar_tarefa",
                                  input={"id": 999, "prioridade": 1})),
        _resposta("end_turn", SimpleNamespace(type="text", text="Anotei o IPVA.")),
    ])

    def chamar(mensagens, esforco):
        chamadas.append([dict(m) for m in mensagens])
        return next(respostas)

    monkeypatch.setattr(ia, "_chamar", chamar)
    assert ia.responder(db, "pedido") == "Anotei o IPVA."
    resultados = chamadas[1][-1]["content"]
    assert [r["is_error"] for r in resultados] == [False, True]  # erro vira tool_result, não exceção
    assert repo.tarefas_abertas(db)[0].titulo == "Pagar IPVA"


# ---------- rotinas e Telegram ----------

def test_tick_manda_plano_uma_vez_e_lembretes(db, relogio, telegram_falso, monkeypatch):
    pedidos = []
    monkeypatch.setattr(ia, "responder", lambda db, pedido, esforco=None: pedidos.append(pedido) or "Bom dia!")
    repo.criar_lembrete(db, "2026-10-01 05:50", "Tomar o remédio")
    relogio.ajustar(datetime(2026, 10, 1, 6, 1))
    # lembrete sai; plano não, porque a pessoa ainda não falou com ela
    assert rotinas.tick() == {"lembretes": 1, "plano": False, "fechamento": False}
    repo.estado_set(db, "iniciado", "sim")
    assert rotinas.tick()["plano"] is True
    assert rotinas.tick()["plano"] is False  # não repete
    assert [m["text"] for m in telegram_falso] == ["⏰ Tomar o remédio", "Bom dia!"]
    assert pedidos[0].count("PEDIDO AUTOMÁTICO DO SISTEMA") == 1 and "monte o plano de hoje" in pedidos[0]

    relogio.ajustar(datetime(2026, 10, 1, 20, 0))
    assert rotinas.tick()["fechamento"] is True


def test_fechamento_nao_sai_de_madrugada(db, relogio, telegram_falso, monkeypatch):
    monkeypatch.setattr(ia, "responder", lambda *a, **k: "Fechamento")
    repo.estado_set(db, "iniciado", "sim")
    relogio.ajustar(datetime(2026, 10, 1, 23, 30))
    assert rotinas.tick()["fechamento"] is False


def test_plano_nao_sai_de_tarde_se_o_servidor_dormiu(db, relogio, telegram_falso, monkeypatch):
    monkeypatch.setattr(ia, "responder", lambda *a, **k: "Bom dia!")
    repo.estado_set(db, "iniciado", "sim")
    relogio.ajustar(datetime(2026, 10, 1, 13, 0))
    assert rotinas.tick()["plano"] is False


def _cliente():
    from app.main import app
    return TestClient(app)


def _update(update_id, texto, chat_id=42):
    return {"update_id": update_id, "message": {"chat": {"id": chat_id}, "text": texto}}


def test_webhook_exige_segredo_e_ignora_estranhos(db, relogio, telegram_falso, monkeypatch):
    monkeypatch.setattr(rotinas, "ao_receber", lambda mensagem_id: None)
    cliente = _cliente()
    assert cliente.post("/secretaria/telegram", json=_update(1, "oi")).status_code == 403
    cabecalho = {"X-Telegram-Bot-Api-Secret-Token": config.TELEGRAM_WEBHOOK_SECRET}
    assert cliente.post("/secretaria/telegram", json=_update(2, "oi", chat_id=7), headers=cabecalho).status_code == 200
    assert repo.mensagens_pendentes(db) == []
    cliente.post("/secretaria/telegram", json=_update(3, "oi"), headers=cabecalho)
    cliente.post("/secretaria/telegram", json=_update(3, "oi"), headers=cabecalho)  # reenvio do Telegram
    assert len(repo.mensagens_pendentes(db)) == 1
    assert repo.estado_get(db, "iniciado")


def test_mensagens_em_sequencia_viram_uma_resposta(db, relogio, telegram_falso, monkeypatch):
    pedidos = []
    monkeypatch.setattr(ia, "responder", lambda db, pedido, esforco=None: pedidos.append(pedido) or "Anotado.")
    repo.salvar_mensagem(db, "user", "fiz a academia", processada=False)
    ultima = repo.salvar_mensagem(db, "user", "e liguei pro contador", processada=False)
    rotinas.ao_receber(ultima - 1)  # a mais antiga desiste: chegou outra depois
    assert pedidos == []
    rotinas.ao_receber(ultima)
    assert len(pedidos) == 1
    assert "fiz a academia" in pedidos[0] and "e liguei pro contador" in pedidos[0]
    assert repo.mensagens_pendentes(db) == []
    assert [m["text"] for m in telegram_falso] == ["Anotado."]


def test_falha_da_ia_avisa_a_pessoa(db, relogio, telegram_falso, monkeypatch):
    def quebra(*a, **k):
        raise TypeError("sem chave")

    monkeypatch.setattr(ia, "responder", quebra)
    mid = repo.salvar_mensagem(db, "user", "oi", processada=False)
    rotinas.ao_receber(mid)
    assert "problema técnico" in telegram_falso[0]["text"]
    assert repo.mensagens_pendentes(db) == []


def test_lista_de_tarefas_sem_ia(db, relogio, telegram_falso):
    repo.criar_tarefa(db, {"titulo": "Pagar <boleto> & taxa", "area": "pessoal", "agendada_para": "2026-10-01"})
    repo.criar_tarefa(db, {"titulo": "Revisar contrato", "area": "profissional", "status": "aguardando"})
    mid = repo.salvar_mensagem(db, "user", "/tarefas", processada=False)
    rotinas.ao_receber(mid)
    texto = telegram_falso[0]["text"]
    assert "<b>Hoje</b>" in texto and "Pagar &lt;boleto&gt; &amp; taxa" in texto
    assert "<b>Aguardando outras pessoas</b>" in texto
