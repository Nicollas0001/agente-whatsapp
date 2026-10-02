"""O que a secretária faz sozinha (plano, fechamento, lembretes) e o
tratamento das mensagens que chegam."""
import html
import logging
import threading
import time
from datetime import datetime, timedelta

from app.secretaria import canal, config, diario, financas, ia, telegram, repositorio as repo

log = logging.getLogger("secretaria")

# Uma coisa de cada vez: mensagens e rotinas nunca rodam em paralelo, então a
# ordem da conversa e os dados ficam consistentes.
_vez = threading.RLock()


def vez() -> threading.RLock:
    return _vez

AJUDA = f"""<b>Como falar comigo</b>
Escreve do seu jeito, como falaria com uma pessoa:
• "Amanhã preciso pagar o IPVA e mandar a proposta pro cliente até sexta"
• "Fiz a academia e liguei pro contador"
• "Gastei 45 no iFood no Nubank"
• "Caiu o salário, 3.200 no Inter"
• "Comprei um fone de 600 em 6x no Inter Black"
• "Paguei o aluguel" / "Investi 500 no CDB"
• "Quanto gastei com mercado esse mês?" / "Posso comprar um tênis de 400?"
• "Me lembra às 15h de buscar o exame"

<b>Atalhos</b>
/plano: monta (ou refaz) o plano de hoje
/adiantar: o que dá pra fazer agora que adianta o futuro
/relatorio: o que você fez e gastou (/relatorio semana, /relatorio mes)
/tarefas: lista rápida de tudo que está aberto
/financas: saldos, faturas e contas do mês
/custo: quanto a secretária custou de IA no mês
/fechamento: fecha o dia agora
/desfazer: desfaz a última coisa que eu alterei

Eu mando o plano às {config.HORA_PLANO:%H:%M} e faço o fechamento às {config.HORA_FECHAMENTO:%H:%M}."""

AUTOMATICO = "PEDIDO AUTOMÁTICO DO SISTEMA (não é mensagem da pessoa)"

PEDIDO_PLANO = """{origem}: monte o plano de hoje. De manhã, mande como mensagem de bom dia; se o dia já começou, considere o que já foi feito e o tempo que resta.
- Decida o que entra hoje e marque com atualizar_tarefa (agendada_para = hoje) o que escolher. O que não couber, mova para outro dia.
- Estrutura: uma abertura curta e humana (o dia da semana, um compromisso marcante ou algo que vence); <b>Prioridades</b> (no máximo 3, com o motivo quando não for óbvio); <b>Com hora marcada</b>, se houver; <b>Se der tempo</b> (coisas rápidas, agrupadas por contexto); <b>💰 Dinheiro</b>, só se houver algo que peça ação hoje ou nos próximos dias (conta fixa vencendo ou atrasada, fatura vencendo, conta que vai ficar negativa, meta de gasto estourando, aporte do mês atrasado); e <b>💡 Adiantar</b>, a melhor oportunidade de adiantar algo futuro, se existir.
- Separe pessoal e profissional quando ajudar a ler.
- Se alguma tarefa já foi adiada 3 vezes ou mais, fale de uma delas com tato e proponha uma saída.
- Fim de semana ou dia vazio: mensagem leve e curta.
- Sem nenhuma tarefa: pergunte o que está na cabeça da pessoa para hoje."""

PEDIDO_FECHAMENTO = """{origem}: faça o fechamento do dia.
- Compare o que estava agendado para hoje com o que foi registrado como feito.
- Mensagem curta: reconheça o que foi feito (sem exagero), liste o que ficou pendente de hoje e pergunte o que foi feito e não foi anotado e o que fazer com o resto (amanhã, outro dia ou cancelar).
- Dinheiro em uma linha: quanto foi gasto hoje (lançamentos de hoje no retrato) e pergunte se teve algum gasto que ela não anotou. Se alguma conta fixa venceu hoje e não foi lançada, pergunte se já pagou.
- Em uma ou duas linhas, diga o principal de amanhã.
- Não mova nada ainda; espere a resposta. Termine com uma pergunta objetiva."""

PEDIDO_ADIANTAR = """PEDIDO DA PESSOA (/adiantar): o que ela pode fazer agora que adianta tarefas futuras. Siga <pensar_a_frente>. Leve em conta a hora atual e o que é viável agora (à noite, coisas de casa ou do celular). No máximo 3 sugestões, da melhor para a pior. Vale também o lado financeiro (separar o dinheiro de uma conta que vence, cancelar uma assinatura que ela quase não usa, fazer o aporte do mês). Se descobrir uma dependência entre tarefas que não estava registrada, registre com atualizar_tarefa (depende_de)."""

PEDIDO_BOAS_VINDAS = f"""PEDIDO DO SISTEMA: a pessoa acabou de começar a usar você (/start). Apresente-se em 2 ou 3 linhas: você guarda as tarefas pessoais e profissionais dela e cuida das finanças (gastos, entradas, contas do mês, cartões e investimentos, nos mesmos dados do app dela), manda o plano do dia às {config.HORA_PLANO:%H:%M}, faz o fechamento às {config.HORA_FECHAMENTO:%H:%M}, faz relatório quando ela pedir e diz o que dá pra adiantar (/adiantar). Depois faça no máximo 3 perguntas para conhecer a rotina: como ela quer ser chamada, horário de trabalho e compromissos fixos, e qual conta ou cartão ela mais usa no dia a dia. Não pergunte o que já está em "O QUE VOCÊ JÁ SABE"."""


def _periodo_relatorio(argumento: str, hoje) -> tuple:
    arg = (argumento or "").strip().lower()
    if arg.startswith("sem"):
        inicio, nome = hoje - timedelta(days=hoje.weekday()), "esta semana"
    elif arg.startswith("mes") or arg.startswith("mês"):
        inicio, nome = hoje.replace(day=1), "este mês"
    elif arg.startswith("ontem"):
        inicio = hoje - timedelta(days=1)
        return inicio, inicio, f"ontem ({inicio:%d/%m})"
    elif arg.isdigit():
        inicio, nome = hoje - timedelta(days=int(arg) - 1), f"os últimos {arg} dias"
    else:
        inicio, nome = hoje, "hoje"
    return inicio, hoje, f"{nome} ({inicio:%d/%m} a {hoje:%d/%m})"


def _montar_pedido(conteudo: str, ate_mensagem_id=None) -> str:
    historico = repo.historico_recente(config.HISTORICO_MENSAGENS, antes_de_id=ate_mensagem_id)
    conversa = "\n".join(
        f"[{m.momento:%d/%m %H:%M}] {'Você (secretária)' if m.papel == 'assistant' else 'Pessoa'}: {m.texto}"
        for m in historico
    ) or "(sem conversa recente)"
    return f"""<retrato>
{repo.montar_contexto()}
</retrato>

<conversa_recente>
{conversa}
</conversa_recente>

{conteudo}"""


def _gerar(conteudo: str, planejamento: bool = False, ate_mensagem_id=None) -> str:
    """Pede a resposta à IA (com retrato e conversa recente). Não entrega nem guarda."""
    return ia.responder(_montar_pedido(conteudo, ate_mensagem_id), planejamento)


def _lista_tarefas() -> str:
    ref = repo.hoje()
    abertas = repo.tarefas_abertas()
    if not abertas:
        return "Nada em aberto. Me conta o que está na sua cabeça que eu organizo."
    grupos = {"Hoje": [], "Próximos dias": [], "Sem data": [], "Aguardando outras pessoas": []}
    for t in abertas:
        if t.status == "aguardando":
            grupos["Aguardando outras pessoas"].append(t)
        elif t.agendada_para and t.agendada_para <= ref:
            grupos["Hoje"].append(t)
        elif t.agendada_para:
            grupos["Próximos dias"].append(t)
        else:
            grupos["Sem data"].append(t)
    saida = []
    for nome, lista in grupos.items():
        if not lista:
            continue
        saida.append(f"<b>{nome}</b>")
        for t in lista:
            extra = []
            if t.agendada_para and nome == "Próximos dias":
                extra.append(f"{repo.DIAS_CURTOS[t.agendada_para.weekday()]} {t.agendada_para:%d/%m}")
            if t.hora:
                extra.append(t.hora)
            if t.prazo:
                extra.append(f"prazo {t.prazo:%d/%m}")
            icone = "💼" if t.area == "profissional" else "🏠"
            sufixo = f" <i>({', '.join(extra)})</i>" if extra else ""
            saida.append(f"{icone} {html.escape(t.titulo)}{sufixo}")
        saida.append("")
    return "\n".join(saida).strip()


def _texto_custo() -> str:
    uso = repo.uso_do_mes()
    if not uso:
        return "Ainda não gastei nada de IA este mês."
    linhas, total = [], 0.0
    for modelo, dados in sorted(uso.items()):
        valor = ia.custo_em_dolar(modelo, dados)
        total += valor
        linhas.append(f"• {html.escape(modelo)}: {int(dados.get('chamadas', 0))} chamadas, US$ {valor:.2f}")
    dia = repo.hoje().day
    projecao = total / dia * 30 if dia else total
    return (f"<b>Custo de IA em {financas.MESES[repo.hoje().month - 1]}</b>\n" + "\n".join(linhas)
            + f"\n\nTotal: <b>US$ {total:.2f}</b> (≈ R$ {total * config.COTACAO_DOLAR:.2f})"
            + f"\nNesse ritmo, o mês fecha em ~US$ {projecao:.2f} (≈ R$ {projecao * config.COTACAO_DOLAR:.2f})."
            + "\n<i>Estimativa pelos tokens usados; o valor oficial está no console da Anthropic.</i>")


def responder(texto: str, mensagem_id: int) -> str:
    """Resposta para uma mensagem da pessoa (comando ou conversa). Não entrega nem guarda."""
    if not texto.startswith("/"):
        return _gerar(f"Mensagem da pessoa:\n{texto}", ate_mensagem_id=mensagem_id)
    comando, _, argumento = texto.strip().partition(" ")
    comando = comando.lower().split("@")[0]
    if comando in ("/ajuda", "/help"):
        return AJUDA
    if comando == "/tarefas":
        return _lista_tarefas()
    if comando in ("/financas", "/finanças", "/saldo"):
        return financas.resumo_telegram()
    if comando == "/custo":
        return _texto_custo()
    if comando == "/desfazer":
        return diario.desfazer_ultima()
    if comando == "/start":
        return _gerar(PEDIDO_BOAS_VINDAS, ate_mensagem_id=mensagem_id)
    if comando == "/plano":
        repo.rolar_atrasadas()
        resposta = _gerar(PEDIDO_PLANO.format(origem="PEDIDO DA PESSOA (/plano)"), True, mensagem_id)
        repo.estado_set(f"plano:{repo.hoje()}", "pedido")
        return resposta
    if comando == "/fechamento":
        resposta = _gerar(PEDIDO_FECHAMENTO.format(origem="PEDIDO DA PESSOA (/fechamento)"), True, mensagem_id)
        repo.estado_set(f"fechamento:{repo.hoje()}", "pedido")
        return resposta
    if comando == "/adiantar":
        extra = f"\nO que ela disse junto: {argumento}" if argumento else ""
        return _gerar(PEDIDO_ADIANTAR + extra, True, mensagem_id)
    if comando in ("/relatorio", "/relatório"):
        inicio, fim, periodo = _periodo_relatorio(argumento, repo.hoje())
        registros = repo.formatar_registros(repo.registros_periodo(inicio, fim))
        movimento = financas.consultar(inicio.isoformat(), fim.isoformat(), limite=80)
        return _gerar(f"""PEDIDO DA PESSOA (/relatorio): relatório do que ela fez e gastou em {periodo}.
Comece com um resumo de 1 ou 2 linhas (volume e foco principal). Depois agrupe as atividades por área (profissional e pessoal) e por tema, não por hora, destacando entregas importantes. Depois o dinheiro: quanto entrou, quanto saiu, as 3 maiores categorias de gasto, compras que chamam atenção e como estão as metas e o aporte do mês. No fim, o que ficou pendente ou atrasado no período (veja o retrato) e uma observação honesta sobre o padrão, se houver algo útil (muito apagando incêndio, nada pessoal, algo sendo empurrado, gasto subindo). Seja fiel aos dados, sem inventar.

<atividades>
{registros}
</atividades>

<lancamentos_financeiros>
{movimento}
</lancamentos_financeiros>""", True, mensagem_id)
    return _gerar(f"Mensagem da pessoa:\n{texto}", ate_mensagem_id=mensagem_id)


def responder_app(texto: str) -> dict:
    """Mensagem digitada no app: responde na hora (a resposta volta na própria requisição)."""
    texto = (texto or "").strip()
    if not texto:
        raise ValueError("mensagem vazia")
    with _vez:
        mensagem_id = repo.salvar_mensagem("user", texto)
        if not repo.estado_get("iniciado"):
            repo.estado_set("iniciado", repo.agora().isoformat())
        with diario.registrando(texto) as registro:
            try:
                resposta = responder(texto, mensagem_id)
            except Exception as erro:  # a pessoa precisa de uma resposta, não de um "Erro 500"
                log.exception("Falha ao responder pelo app")
                resposta = ("Tive um problema técnico e não consegui terminar isso agora "
                            f"({type(erro).__name__}). Tenta de novo daqui a pouco?")
                if not registro.vazio():
                    resposta += " O que eu já tinha alterado pode ser desfeito no botão abaixo."
        resposta_id = repo.salvar_mensagem("assistant", resposta)
    return {"resposta": resposta, "id": resposta_id, "desfazivel": not registro.vazio()}


def processar_pendentes() -> None:
    """Responde tudo o que chegou e ainda não foi respondido."""
    with _vez:
        pendentes = repo.mensagens_pendentes()
        if not pendentes:
            return
        # Comandos são tratados um a um; textos seguidos viram uma resposta só.
        grupos, atual = [], []
        for m in pendentes:
            if m.texto.startswith("/"):
                if atual:
                    grupos.append(atual)
                    atual = []
                grupos.append([m])
            else:
                atual.append(m)
        if atual:
            grupos.append(atual)

        for grupo in grupos:
            ids = [m.id for m in grupo]
            try:
                with telegram.Digitando(config.TELEGRAM_OWNER_ID), diario.registrando(grupo[-1].texto):
                    if len(grupo) == 1 and grupo[0].texto.startswith("/"):
                        resposta = responder(grupo[0].texto, grupo[0].id)
                    else:
                        textos = "\n".join(f"[{m.momento:%H:%M}] {m.texto}" for m in grupo)
                        rotulo = "Mensagem da pessoa" if len(grupo) == 1 else "Mensagens da pessoa (mandadas em sequência)"
                        resposta = _gerar(f"{rotulo}:\n{textos}", ate_mensagem_id=ids[0])
                repo.salvar_mensagem("assistant", resposta)
                telegram.enviar(config.TELEGRAM_OWNER_ID, resposta)
            except Exception as erro:  # fronteira do job: a pessoa precisa saber que falhou
                log.exception("Falha ao responder")
                aviso = ("Tive um problema técnico e não consegui processar isso agora "
                         f"({type(erro).__name__}). Me manda de novo daqui a pouco?")
                telegram.enviar(config.TELEGRAM_OWNER_ID, aviso)
                repo.salvar_mensagem("assistant", "(falha técnica: as mensagens acima NÃO foram processadas)")
            finally:
                repo.marcar_processadas(ids)


def ao_receber(mensagem_id: int) -> None:
    """Espera um pouco para juntar mensagens em sequência, como uma pessoa faria."""
    time.sleep(config.ESPERA_AGRUPAR_SEG)
    pendentes = repo.mensagens_pendentes()
    if pendentes and pendentes[-1].id != mensagem_id:
        return  # chegou mensagem mais nova; quem cuidar dela responde tudo junto
    processar_pendentes()


def _rotina_do_dia(chave: str, pedido: str) -> None:
    tentativas_chave = f"{chave}:tentativas"
    tentativas = int(repo.estado_get(tentativas_chave) or 0)
    if tentativas >= 3:
        return
    repo.estado_set(tentativas_chave, str(tentativas + 1))
    try:
        with diario.registrando(f"rotina {chave}"):
            texto = _gerar(pedido, planejamento=True)
        canal.entregar(texto)
        repo.estado_set(chave, "enviado")
    except Exception:
        log.exception("Falha na rotina %s", chave)


def tick() -> dict:
    """Roda o que estiver na hora. Pode ser chamado quantas vezes quiser."""
    feito = {"lembretes": 0, "plano": False, "fechamento": False}
    if not canal.tem_destino():
        return feito
    with _vez:
        agora = repo.agora()
        hoje = agora.date()

        for lembrete in repo.lembretes_vencidos():
            atraso = agora - lembrete.quando
            nota = f"\n<i>(era pra {lembrete.quando:%H:%M})</i>" if atraso > timedelta(minutes=20) else ""
            canal.entregar(f"⏰ {html.escape(lembrete.texto)}{nota}")
            repo.marcar_lembrete_enviado(lembrete.id)
            feito["lembretes"] += 1

        if repo.estado_get("rolagem") != hoje.isoformat():
            repo.rolar_atrasadas(hoje)
            repo.estado_set("rolagem", hoje.isoformat())

        # Só puxa conversa depois que a pessoa mandou a primeira mensagem.
        if not repo.estado_get("iniciado"):
            return feito

        # Janela de envio: se o servidor estava dormindo no horário, ainda
        # manda atrasado, mas não manda "bom dia" de tarde nem fechamento de madrugada.
        inicio_plano = datetime.combine(hoje, config.HORA_PLANO)
        if (inicio_plano <= agora < inicio_plano + timedelta(hours=6)
                and not repo.estado_get(f"plano:{hoje}")):
            _rotina_do_dia(f"plano:{hoje}", PEDIDO_PLANO.format(origem=AUTOMATICO))
            feito["plano"] = True

        inicio_fechamento = datetime.combine(hoje, config.HORA_FECHAMENTO)
        if (inicio_fechamento <= agora < inicio_fechamento + timedelta(hours=3)
                and not repo.estado_get(f"fechamento:{hoje}")):
            _rotina_do_dia(f"fechamento:{hoje}", PEDIDO_FECHAMENTO.format(origem=AUTOMATICO))
            feito["fechamento"] = True

    # Mensagens que ficaram sem resposta (ex.: servidor reiniciou no meio).
    antigas = [m for m in repo.mensagens_pendentes() if repo.agora() - m.momento > timedelta(minutes=1)]
    if antigas:
        processar_pendentes()
    return feito


def iniciar_relogio() -> None:
    """Relógio interno: roda o tick a cada minuto enquanto o servidor está de pé.
    No Render grátis o servidor dorme; aí quem acorda é o cron externo (/secretaria/tick)."""
    def laco():
        while True:
            try:
                tick()
            except Exception:
                log.exception("Erro no relógio da secretária")
            time.sleep(60)

    threading.Thread(target=laco, daemon=True, name="relogio-secretaria").start()
