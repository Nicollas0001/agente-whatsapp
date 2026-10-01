"""O que a secretária faz sozinha (plano, fechamento, lembretes) e o
tratamento das mensagens que chegam."""
import html
import logging
import threading
import time
from datetime import datetime, timedelta

from app.models.database import SessionLocal
from app.secretaria import config, ia, telegram, repositorio as repo

log = logging.getLogger("secretaria")

# Uma coisa de cada vez: mensagens e rotinas nunca rodam em paralelo, então a
# ordem da conversa e os dados ficam consistentes.
_vez = threading.RLock()

AJUDA = f"""<b>Como falar comigo</b>
Escreve do seu jeito, como falaria com uma pessoa:
• "Amanhã preciso pagar o IPVA e mandar a proposta pro cliente até sexta"
• "Fiz a academia e liguei pro contador"
• "Me lembra às 15h de buscar o exame"
• "Empurra o relatório pra segunda"
• "O que eu faço agora? Tenho 30 min"

<b>Atalhos</b>
/plano: monta (ou refaz) o plano de hoje
/adiantar: o que dá pra fazer agora que adianta o futuro
/relatorio: o que você fez (/relatorio semana, /relatorio mes)
/tarefas: lista rápida de tudo que está aberto
/fechamento: fecha o dia agora

Eu mando o plano às {config.HORA_PLANO:%H:%M} e faço o fechamento às {config.HORA_FECHAMENTO:%H:%M}."""

AUTOMATICO = "PEDIDO AUTOMÁTICO DO SISTEMA (não é mensagem da pessoa)"

PEDIDO_PLANO = """{origem}: monte o plano de hoje. De manhã, mande como mensagem de bom dia; se o dia já começou, considere o que já foi feito e o tempo que resta.
- Decida o que entra hoje e marque com atualizar_tarefa (agendada_para = hoje) o que escolher. O que não couber, mova para outro dia.
- Estrutura: uma abertura curta e humana (o dia da semana, um compromisso marcante ou algo que vence); <b>Prioridades</b> (no máximo 3, com o motivo quando não for óbvio); <b>Com hora marcada</b>, se houver; <b>Se der tempo</b> (coisas rápidas, agrupadas por contexto); e <b>💡 Adiantar</b>, a melhor oportunidade de adiantar algo futuro, se existir.
- Separe pessoal e profissional quando ajudar a ler.
- Se alguma tarefa já foi adiada 3 vezes ou mais, fale de uma delas com tato e proponha uma saída.
- Fim de semana ou dia vazio: mensagem leve e curta.
- Sem nenhuma tarefa: pergunte o que está na cabeça da pessoa para hoje."""

PEDIDO_FECHAMENTO = """{origem}: faça o fechamento do dia.
- Compare o que estava agendado para hoje com o que foi registrado como feito.
- Mensagem curta: reconheça o que foi feito (sem exagero), liste o que ficou pendente de hoje e pergunte o que foi feito e não foi anotado e o que fazer com o resto (amanhã, outro dia ou cancelar).
- Em uma ou duas linhas, diga o principal de amanhã.
- Não mova nada ainda; espere a resposta. Termine com uma pergunta objetiva."""

PEDIDO_ADIANTAR = """PEDIDO DA PESSOA (/adiantar): o que ela pode fazer agora que adianta tarefas futuras. Siga <pensar_a_frente>. Leve em conta a hora atual e o que é viável agora (à noite, coisas de casa ou do celular). No máximo 3 sugestões, da melhor para a pior. Se descobrir uma dependência entre tarefas que não estava registrada, registre com atualizar_tarefa (depende_de)."""

PEDIDO_BOAS_VINDAS = f"""PEDIDO DO SISTEMA: a pessoa acabou de mandar /start. Apresente-se em 2 ou 3 linhas: você guarda as tarefas pessoais e profissionais dela, manda o plano do dia às {config.HORA_PLANO:%H:%M}, faz o fechamento às {config.HORA_FECHAMENTO:%H:%M}, anota o que ela for contando, faz relatório quando ela pedir e diz o que dá pra adiantar (/adiantar). Depois faça no máximo 3 perguntas para conhecer a rotina: como ela quer ser chamada, horário de trabalho e compromissos fixos, e o que está pendente na cabeça agora. Não pergunte o que já está em "O QUE VOCÊ JÁ SABE"."""


def _pedido_relatorio(argumento: str, hoje) -> tuple[str, str]:
    arg = (argumento or "").strip().lower()
    if arg.startswith("sem"):
        inicio, nome = hoje - timedelta(days=hoje.weekday()), "esta semana"
    elif arg.startswith("mes") or arg.startswith("mês"):
        inicio, nome = hoje.replace(day=1), "este mês"
    elif arg.startswith("ontem"):
        inicio = hoje - timedelta(days=1)
        return inicio.isoformat(), f"ontem ({inicio:%d/%m})"
    elif arg.isdigit():
        inicio, nome = hoje - timedelta(days=int(arg) - 1), f"os últimos {arg} dias"
    else:
        inicio, nome = hoje, "hoje"
    return inicio.isoformat(), f"{nome} ({inicio:%d/%m} a {hoje:%d/%m})"


def _montar_pedido(db, conteudo: str, ate_mensagem_id=None) -> str:
    historico = repo.historico_recente(db, config.HISTORICO_MENSAGENS, antes_de_id=ate_mensagem_id)
    conversa = "\n".join(
        f"[{m.momento:%d/%m %H:%M}] {'Você (secretária)' if m.papel == 'assistant' else 'Pessoa'}: {m.texto}"
        for m in historico
    ) or "(sem conversa recente)"
    return f"""<retrato>
{repo.montar_contexto(db)}
</retrato>

<conversa_recente>
{conversa}
</conversa_recente>

{conteudo}"""


def _responder_e_enviar(db, conteudo: str, esforco: str = None, ate_mensagem_id=None) -> str:
    pedido = _montar_pedido(db, conteudo, ate_mensagem_id)
    with telegram.Digitando(config.TELEGRAM_OWNER_ID):
        resposta = ia.responder(db, pedido, esforco)
    telegram.enviar(config.TELEGRAM_OWNER_ID, resposta)
    repo.salvar_mensagem(db, "assistant", resposta)
    return resposta


def _lista_tarefas(db) -> str:
    ref = repo.hoje()
    abertas = repo.tarefas_abertas(db)
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


def _tratar_comando(db, texto: str, mensagem_id: int) -> None:
    comando, _, argumento = texto.strip().partition(" ")
    comando = comando.lower().split("@")[0]
    if comando in ("/ajuda", "/help"):
        telegram.enviar(config.TELEGRAM_OWNER_ID, AJUDA)
    elif comando == "/tarefas":
        telegram.enviar(config.TELEGRAM_OWNER_ID, _lista_tarefas(db))
    elif comando == "/start":
        _responder_e_enviar(db, PEDIDO_BOAS_VINDAS, ate_mensagem_id=mensagem_id)
    elif comando == "/plano":
        repo.rolar_atrasadas(db)
        _responder_e_enviar(db, PEDIDO_PLANO.format(origem="PEDIDO DA PESSOA (/plano)"),
                            config.ESFORCO_PLANEJAMENTO, mensagem_id)
        repo.estado_set(db, f"plano:{repo.hoje()}", "pedido")
    elif comando == "/fechamento":
        _responder_e_enviar(db, PEDIDO_FECHAMENTO.format(origem="PEDIDO DA PESSOA (/fechamento)"),
                            ate_mensagem_id=mensagem_id)
        repo.estado_set(db, f"fechamento:{repo.hoje()}", "pedido")
    elif comando == "/adiantar":
        extra = f"\nO que ela disse junto: {argumento}" if argumento else ""
        _responder_e_enviar(db, PEDIDO_ADIANTAR + extra, config.ESFORCO_PLANEJAMENTO, mensagem_id)
    elif comando in ("/relatorio", "/relatório"):
        inicio, periodo = _pedido_relatorio(argumento, repo.hoje())
        registros = repo.formatar_registros(repo.registros_periodo(db, repo.ler_data(inicio), repo.hoje()))
        _responder_e_enviar(db, f"""PEDIDO DA PESSOA (/relatorio): relatório do que ela fez em {periodo}.
Comece com um resumo de 1 ou 2 linhas (volume e foco principal). Depois agrupe por área (profissional e pessoal) e por tema, não por hora, destacando entregas importantes. No fim, o que ficou pendente ou atrasado no período (veja o retrato) e uma observação honesta sobre o padrão, se houver algo útil (muito apagando incêndio, nada pessoal, algo sendo empurrado). Seja fiel aos registros, sem inventar.

<registros>
{registros}
</registros>""", config.ESFORCO_PLANEJAMENTO, mensagem_id)
    else:
        _responder_e_enviar(db, f"Mensagem da pessoa:\n{texto}", ate_mensagem_id=mensagem_id)


def processar_pendentes() -> None:
    """Responde tudo o que chegou e ainda não foi respondido."""
    with _vez:
        db = SessionLocal()
        try:
            pendentes = repo.mensagens_pendentes(db)
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
                    if len(grupo) == 1 and grupo[0].texto.startswith("/"):
                        _tratar_comando(db, grupo[0].texto, grupo[0].id)
                    else:
                        textos = "\n".join(f"[{m.momento:%H:%M}] {m.texto}" for m in grupo)
                        rotulo = "Mensagem da pessoa" if len(grupo) == 1 else "Mensagens da pessoa (mandadas em sequência)"
                        _responder_e_enviar(db, f"{rotulo}:\n{textos}", ate_mensagem_id=ids[0])
                except Exception as erro:  # fronteira do job: a pessoa precisa saber que falhou
                    db.rollback()
                    log.exception("Falha ao responder")
                    aviso = ("Tive um problema técnico e não consegui processar isso agora "
                             f"({type(erro).__name__}). Me manda de novo daqui a pouco?")
                    telegram.enviar(config.TELEGRAM_OWNER_ID, aviso)
                    repo.salvar_mensagem(db, "assistant", "(falha técnica: as mensagens acima NÃO foram processadas)")
                finally:
                    repo.marcar_processadas(db, ids)
        finally:
            db.close()


def ao_receber(mensagem_id: int) -> None:
    """Espera um pouco para juntar mensagens em sequência, como uma pessoa faria."""
    time.sleep(config.ESPERA_AGRUPAR_SEG)
    db = SessionLocal()
    try:
        pendentes = repo.mensagens_pendentes(db)
    finally:
        db.close()
    if pendentes and pendentes[-1].id != mensagem_id:
        return  # chegou mensagem mais nova; quem cuidar dela responde tudo junto
    processar_pendentes()


def _rotina_do_dia(db, chave: str, pedido: str, esforco: str) -> None:
    tentativas_chave = f"{chave}:tentativas"
    tentativas = int(repo.estado_get(db, tentativas_chave) or 0)
    if tentativas >= 3:
        return
    repo.estado_set(db, tentativas_chave, str(tentativas + 1))
    try:
        _responder_e_enviar(db, pedido, esforco)
        repo.estado_set(db, chave, "enviado")
    except Exception:
        db.rollback()
        log.exception("Falha na rotina %s", chave)


def tick() -> dict:
    """Roda o que estiver na hora. Pode ser chamado quantas vezes quiser."""
    feito = {"lembretes": 0, "plano": False, "fechamento": False}
    if not config.TELEGRAM_OWNER_ID:
        return feito
    with _vez:
        db = SessionLocal()
        try:
            agora = repo.agora()
            hoje = agora.date()

            for lembrete in repo.lembretes_vencidos(db):
                atraso = agora - lembrete.quando
                nota = f"\n<i>(era pra {lembrete.quando:%H:%M})</i>" if atraso > timedelta(minutes=20) else ""
                if telegram.enviar(config.TELEGRAM_OWNER_ID, f"⏰ {html.escape(lembrete.texto)}{nota}"):
                    repo.marcar_lembrete_enviado(db, lembrete.id)
                    repo.salvar_mensagem(db, "assistant", f"⏰ {lembrete.texto}")
                    feito["lembretes"] += 1

            if repo.estado_get(db, "rolagem") != hoje.isoformat():
                repo.rolar_atrasadas(db, hoje)
                repo.estado_set(db, "rolagem", hoje.isoformat())

            # Só puxa conversa depois que a pessoa mandou a primeira mensagem.
            if not repo.estado_get(db, "iniciado"):
                return feito

            # Janela de envio: se o servidor estava dormindo no horário, ainda
            # manda atrasado, mas não manda "bom dia" de tarde nem fechamento de madrugada.
            inicio_plano = datetime.combine(hoje, config.HORA_PLANO)
            if (inicio_plano <= agora < inicio_plano + timedelta(hours=6)
                    and not repo.estado_get(db, f"plano:{hoje}")):
                _rotina_do_dia(db, f"plano:{hoje}", PEDIDO_PLANO.format(origem=AUTOMATICO), config.ESFORCO_PLANEJAMENTO)
                feito["plano"] = True

            inicio_fechamento = datetime.combine(hoje, config.HORA_FECHAMENTO)
            if (inicio_fechamento <= agora < inicio_fechamento + timedelta(hours=3)
                    and not repo.estado_get(db, f"fechamento:{hoje}")):
                _rotina_do_dia(db, f"fechamento:{hoje}", PEDIDO_FECHAMENTO.format(origem=AUTOMATICO), config.ESFORCO_CONVERSA)
                feito["fechamento"] = True
        finally:
            db.close()

    # Mensagens que ficaram sem resposta (ex.: servidor reiniciou no meio).
    db = SessionLocal()
    try:
        pendentes = repo.mensagens_pendentes(db)
        antigas = [m for m in pendentes if repo.agora() - m.momento > timedelta(minutes=1)]
    finally:
        db.close()
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
