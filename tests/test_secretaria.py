from datetime import date, datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.secretaria import config, ia, recorrencia, rotinas, repositorio as repo


# ---------- recorrência (não usa banco) ----------

@pytest.mark.sem_banco
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


@pytest.mark.sem_banco
def test_recorrencia_invalida():
    with pytest.raises(ValueError):
        recorrencia.validar("toda hora")
    assert recorrencia.validar("Semanal:Sexta,seg") == "semanal:seg,sex"


# ---------- tarefas ----------

def test_ids_sao_sequenciais(relogio):
    assert [repo.criar_tarefa({"titulo": f"T{i}", "area": "pessoal"}) for i in range(3)] == [1, 2, 3]


def test_concluir_tarefa_repetitiva_cria_proxima(relogio):
    tid = repo.criar_tarefa({"titulo": "Pagar condomínio", "area": "pessoal",
                             "recorrencia": "mensal:10", "prazo": "2026-10-10", "agendada_para": "2026-10-08"})
    assert "próxima repetição" in repo.concluir_tarefa(tid)
    abertas = repo.tarefas_abertas()
    assert len(abertas) == 1
    # feita adiantada em 01/10: a próxima é a de novembro, com a mesma antecedência de 2 dias
    assert (abertas[0].prazo, abertas[0].agendada_para) == (date(2026, 11, 10), date(2026, 11, 8))
    assert [r.tarefa_id for r in repo.registros_periodo(date(2026, 10, 1), date(2026, 10, 1))] == [tid]


def test_habito_repetitivo_feito_tarde_nao_acumula(relogio):
    tid = repo.criar_tarefa({"titulo": "Academia", "area": "pessoal",
                             "recorrencia": "semanal:seg,qua,sex", "agendada_para": "2026-09-28"})
    repo.concluir_tarefa(tid)  # concluída na quinta 01/10
    assert repo.tarefas_abertas()[0].agendada_para == date(2026, 10, 2)


def test_rolar_atrasadas_conta_adiamento_so_de_tarefa_normal(relogio):
    normal = repo.criar_tarefa({"titulo": "Mandar proposta", "area": "profissional", "agendada_para": "2026-09-29"})
    academia = repo.criar_tarefa({"titulo": "Academia", "area": "pessoal",
                                  "recorrencia": "semanal:seg,qua,sex", "agendada_para": "2026-09-30"})
    conta = repo.criar_tarefa({"titulo": "Pagar luz", "area": "pessoal", "recorrencia": "mensal:30",
                               "prazo": "2026-09-30", "agendada_para": "2026-09-30"})
    assert repo.rolar_atrasadas(date(2026, 10, 1)) == 3
    t_normal, t_academia, t_conta = (repo.obter_tarefa(i) for i in (normal, academia, conta))
    assert (t_normal.agendada_para, t_normal.adiamentos) == (date(2026, 10, 1), 1)
    assert (t_academia.agendada_para, t_academia.adiamentos) == (date(2026, 10, 2), 0)
    assert (t_conta.agendada_para, t_conta.adiamentos) == (date(2026, 10, 1), 1)  # conta não some


def test_reagendar_para_frente_conta_adiamento(relogio):
    tid = repo.criar_tarefa({"titulo": "Relatório", "area": "profissional", "agendada_para": "2026-10-01"})
    repo.atualizar_tarefa(tid, {"agendada_para": "2026-10-05"})
    assert repo.obter_tarefa(tid).adiamentos == 1
    repo.atualizar_tarefa(tid, {"agendada_para": "2026-10-07"})  # mover algo já futuro não é adiar
    assert repo.obter_tarefa(tid).adiamentos == 1


def test_contexto_mostra_o_essencial(relogio):
    a = repo.criar_tarefa({"titulo": "Pedir orçamento", "area": "pessoal", "contexto": "telefone"})
    repo.criar_tarefa({"titulo": "Reformar banheiro", "area": "pessoal", "prazo": "2026-11-20",
                       "depende_de": [a], "esforco_min": 240})
    repo.salvar_memoria("Trabalha das 8h às 17h")
    texto = repo.montar_contexto()
    assert "quinta-feira, 01/10/2026 09:00" in texto
    assert "sex 02/10" in texto
    assert "depende de #" in texto and "ctx: telefone" in texto
    assert "Trabalha das 8h às 17h" in texto
    assert "FINANÇAS: nada cadastrado ainda" in texto


def test_historico_e_mensagens_pendentes(relogio):
    repo.salvar_mensagem("assistant", "Bom dia!")
    primeira = repo.salvar_mensagem("user", "oi", processada=False)
    segunda = repo.salvar_mensagem("user", "tudo bem?", processada=False)
    assert [m.texto for m in repo.mensagens_pendentes()] == ["oi", "tudo bem?"]
    assert [m.texto for m in repo.historico_recente(10, antes_de_id=primeira)] == ["Bom dia!"]
    repo.marcar_processadas([primeira, segunda])
    assert repo.mensagens_pendentes() == []
    assert [m.texto for m in repo.historico_recente(10)] == ["Bom dia!", "oi", "tudo bem?"]


# ---------- ferramentas da IA ----------

def test_ferramentas_de_tarefa(relogio):
    saida = ia.executar_ferramenta("criar_tarefas", {"tarefas": [
        {"titulo": "Ligar pro contador", "area": "profissional", "agendada_para": "2026-10-01"},
        {"titulo": "Comprar presente", "area": "pessoal", "prazo": "2026-10-12"},
    ]})
    assert saida.startswith("criadas: #1")
    assert "concluída" in ia.executar_ferramenta("concluir_tarefas", {"itens": [{"id": 1}]})
    ia.executar_ferramenta("registrar_feito", {"itens": [{"texto": "Reunião com o time", "area": "profissional"}]})
    registros = ia.executar_ferramenta("buscar_registros", {"inicio": "2026-10-01", "fim": "2026-10-01"})
    assert "Ligar pro contador" in registros and "Reunião com o time" in registros
    assert "agendado" in ia.executar_ferramenta("criar_lembrete", {"quando": "2026-10-01 15:00", "texto": "Buscar exame"})
    assert ia.executar_ferramenta("cancelar_lembrete", {"id": 1}) == "cancelado"
    assert "guardado como m1" == ia.executar_ferramenta("lembrar_sobre_mim", {"fato": "Prefere tarefas pesadas cedo"})
    assert ia.executar_ferramenta("esquecer_sobre_mim", {"id": "m1"}) == "apagado"
    assert "status: feita" in ia.executar_ferramenta("buscar_tarefas", {"status": "feita"})
    assert "Comprar presente" in ia.executar_ferramenta("buscar_tarefas", {"termo": "PRESENTE"})
    with pytest.raises(ValueError):
        ia.executar_ferramenta("atualizar_tarefa", {"id": 9999, "prioridade": 1})


def _resposta(stop_reason, *blocos, modelo="claude-haiku-4-5"):
    uso = SimpleNamespace(input_tokens=1000, output_tokens=200, cache_creation_input_tokens=0,
                          cache_read_input_tokens=3000)
    return SimpleNamespace(stop_reason=stop_reason, content=list(blocos), model=modelo, usage=uso)


def test_laco_executa_ferramentas_devolve_texto_e_mede_custo(relogio, monkeypatch):
    chamadas = []
    respostas = iter([
        _resposta("tool_use",
                  SimpleNamespace(type="tool_use", id="t1", name="criar_tarefas",
                                  input={"tarefas": [{"titulo": "Pagar IPVA", "area": "pessoal"}]}),
                  SimpleNamespace(type="tool_use", id="t2", name="atualizar_tarefa",
                                  input={"id": 999, "prioridade": 1})),
        _resposta("end_turn", SimpleNamespace(type="text", text="Anotei o IPVA.")),
    ])

    def chamar(mensagens, modelo, esforco):
        chamadas.append(([dict(m) for m in mensagens], modelo))
        return next(respostas)

    monkeypatch.setattr(ia, "_chamar", chamar)
    assert ia.responder("pedido") == "Anotei o IPVA."
    assert {m for _, m in chamadas} == {config.MODELO}
    resultados = chamadas[1][0][-1]["content"]
    assert [r["is_error"] for r in resultados] == [False, True]  # erro vira tool_result, não exceção
    assert repo.tarefas_abertas()[0].titulo == "Pagar IPVA"
    uso = repo.uso_do_mes()["claude-haiku-4-5"]
    assert (uso["chamadas"], uso["entrada"], uso["cache_leitura"]) == (2, 2000, 6000)
    assert ia.custo_em_dolar("claude-haiku-4-5", uso) == pytest.approx((2000 * 1 + 400 * 5 + 6000 * 0.1) / 1e6)


def test_planejamento_usa_o_modelo_mais_capaz(relogio, monkeypatch):
    usados = []
    monkeypatch.setattr(ia, "_chamar", lambda m, modelo, esforco: usados.append((modelo, esforco)) or
                        _resposta("end_turn", SimpleNamespace(type="text", text="ok"), modelo=modelo))
    ia.responder("plano", planejamento=True)
    ia.responder("oi")
    assert usados == [(config.MODELO_PLANEJAMENTO, config.ESFORCO_PLANEJAMENTO), (config.MODELO, config.ESFORCO_CONVERSA)]
    assert config.MODELO == "claude-haiku-4-5" and config.MODELO_PLANEJAMENTO == "claude-sonnet-5-5"


# ---------- rotinas e Telegram ----------

def test_tick_manda_plano_uma_vez_e_lembretes(relogio, telegram_falso, monkeypatch):
    pedidos = []
    monkeypatch.setattr(ia, "responder", lambda pedido, planejamento=False: pedidos.append((pedido, planejamento)) or "Bom dia!")
    repo.criar_lembrete("2026-10-01 05:50", "Tomar o remédio")
    relogio.ajustar(datetime(2026, 10, 1, 6, 1))
    # lembrete sai; plano não, porque a pessoa ainda não falou com ela
    assert rotinas.tick() == {"lembretes": 1, "plano": False, "fechamento": False}
    repo.estado_set("iniciado", "sim")
    assert rotinas.tick()["plano"] is True
    assert rotinas.tick()["plano"] is False  # não repete
    assert [m["text"] for m in telegram_falso] == ["⏰ Tomar o remédio", "Bom dia!"]
    pedido, planejamento = pedidos[0]
    assert planejamento and pedido.count("PEDIDO AUTOMÁTICO DO SISTEMA") == 1 and "💰 Dinheiro" in pedido

    relogio.ajustar(datetime(2026, 10, 1, 20, 0))
    assert rotinas.tick()["fechamento"] is True


def test_rotinas_respeitam_a_janela(relogio, telegram_falso, monkeypatch):
    monkeypatch.setattr(ia, "responder", lambda *a, **k: "Oi")
    repo.estado_set("iniciado", "sim")
    relogio.ajustar(datetime(2026, 10, 1, 13, 0))
    assert rotinas.tick()["plano"] is False  # não manda "bom dia" de tarde
    relogio.ajustar(datetime(2026, 10, 1, 23, 30))
    assert rotinas.tick()["fechamento"] is False  # nem fechamento de madrugada


def _cliente():
    from app.main import app
    return TestClient(app)


def _update(update_id, texto, chat_id=42):
    return {"update_id": update_id, "message": {"chat": {"id": chat_id}, "text": texto}}


def test_webhook_exige_segredo_e_ignora_estranhos(relogio, telegram_falso, monkeypatch):
    monkeypatch.setattr(rotinas, "ao_receber", lambda mensagem_id: None)
    cliente = _cliente()
    assert cliente.post("/secretaria/telegram", json=_update(1, "oi")).status_code == 403
    cabecalho = {"X-Telegram-Bot-Api-Secret-Token": config.TELEGRAM_WEBHOOK_SECRET}
    assert cliente.post("/secretaria/telegram", json=_update(2, "oi", chat_id=7), headers=cabecalho).status_code == 200
    assert repo.mensagens_pendentes() == []
    cliente.post("/secretaria/telegram", json=_update(3, "oi"), headers=cabecalho)
    cliente.post("/secretaria/telegram", json=_update(3, "oi"), headers=cabecalho)  # reenvio do Telegram
    assert len(repo.mensagens_pendentes()) == 1
    assert repo.estado_get("iniciado")


def test_mensagens_em_sequencia_viram_uma_resposta(relogio, telegram_falso, monkeypatch):
    pedidos = []
    monkeypatch.setattr(ia, "responder", lambda pedido, planejamento=False: pedidos.append(pedido) or "Anotado.")
    primeira = repo.salvar_mensagem("user", "fiz a academia", processada=False)
    ultima = repo.salvar_mensagem("user", "e gastei 30 no almoço", processada=False)
    rotinas.ao_receber(primeira)  # a mais antiga desiste: chegou outra depois
    assert pedidos == []
    rotinas.ao_receber(ultima)
    assert len(pedidos) == 1
    assert "fiz a academia" in pedidos[0] and "e gastei 30 no almoço" in pedidos[0]
    assert repo.mensagens_pendentes() == []
    assert [m["text"] for m in telegram_falso] == ["Anotado."]


def test_falha_da_ia_avisa_a_pessoa(relogio, telegram_falso, monkeypatch):
    def quebra(*a, **k):
        raise TypeError("sem chave")

    monkeypatch.setattr(ia, "responder", quebra)
    rotinas.ao_receber(repo.salvar_mensagem("user", "oi", processada=False))
    assert "problema técnico" in telegram_falso[0]["text"]
    assert repo.mensagens_pendentes() == []


def test_lista_de_tarefas_sem_ia(relogio, telegram_falso):
    repo.criar_tarefa({"titulo": "Pagar <boleto> & taxa", "area": "pessoal", "agendada_para": "2026-10-01"})
    repo.criar_tarefa({"titulo": "Revisar contrato", "area": "profissional", "status": "aguardando"})
    rotinas.ao_receber(repo.salvar_mensagem("user", "/tarefas", processada=False))
    texto = telegram_falso[0]["text"]
    assert "<b>Hoje</b>" in texto and "Pagar &lt;boleto&gt; &amp; taxa" in texto
    assert "<b>Aguardando outras pessoas</b>" in texto


def test_custo_do_mes(relogio, telegram_falso):
    repo.registrar_uso("claude-sonnet-5-5", SimpleNamespace(input_tokens=1_000_000, output_tokens=100_000,
                                                            cache_creation_input_tokens=0, cache_read_input_tokens=0))
    rotinas.ao_receber(repo.salvar_mensagem("user", "/custo", processada=False))
    texto = telegram_falso[0]["text"]
    assert "US$ 3.00" in texto  # 1M de entrada a US$2 + 100k de saída a US$10
    assert "R$ 16.50" in texto
