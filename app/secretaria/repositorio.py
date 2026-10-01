"""Acesso ao banco e o "retrato" das tarefas que a IA recebe a cada conversa."""
from datetime import date, datetime, timedelta
from typing import Optional

from sqlalchemy import select, insert, update, delete, and_, or_, func
from sqlalchemy.orm import Session

from app.secretaria import config, recorrencia
from app.secretaria.modelos import tarefas, registros, lembretes, memorias, mensagens, estado

DIAS_SEMANA = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira",
               "sexta-feira", "sábado", "domingo"]
DIAS_CURTOS = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"]
STATUS_ABERTOS = ("pendente", "aguardando")
PRIORIDADES = {1: "alta", 2: "média", 3: "baixa"}


def agora() -> datetime:
    return datetime.now(config.FUSO).replace(tzinfo=None, microsecond=0)


def hoje() -> date:
    return agora().date()


# ---------- conversão de entradas ----------

def ler_data(valor) -> Optional[date]:
    if valor in (None, ""):
        return None
    if isinstance(valor, date):
        return valor
    return date.fromisoformat(str(valor)[:10])


def ler_hora(valor) -> Optional[str]:
    if valor in (None, ""):
        return None
    h, m = str(valor).strip().split(":")[:2]
    h, m = int(h), int(m)
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError(f"hora inválida: {valor}")
    return f"{h:02d}:{m:02d}"


def ler_momento(valor) -> datetime:
    texto = str(valor).strip().replace("T", " ")
    momento = datetime.fromisoformat(texto)
    if momento.tzinfo is not None:
        momento = momento.astimezone(config.FUSO).replace(tzinfo=None)
    return momento.replace(microsecond=0)


def ler_ids(valor) -> Optional[str]:
    if valor in (None, "", []):
        return None
    if isinstance(valor, (list, tuple)):
        ids = valor
    else:
        ids = str(valor).replace("#", "").split(",")
    return ",".join(str(int(str(i).strip().lstrip("#"))) for i in ids if str(i).strip())


# ---------- formatação ----------

def rotulo_data(d: Optional[date], ref: Optional[date] = None) -> str:
    if d is None:
        return "-"
    ref = ref or hoje()
    delta = (d - ref).days
    base = f"{DIAS_CURTOS[d.weekday()]} {d:%d/%m}"
    if delta == 0:
        return f"hoje ({base})"
    if delta == 1:
        return f"amanhã ({base})"
    if delta == -1:
        return f"ontem ({base})"
    if delta < 0:
        return f"{base}, há {-delta} dias"
    return f"{base}, em {delta} dias"


def linha_tarefa(t, ref: Optional[date] = None) -> str:
    partes = [f"#{t.id} [{'prof' if t.area == 'profissional' else 'pess'}] {t.titulo}"]
    if t.status == "aguardando":
        partes.append("AGUARDANDO TERCEIROS")
    if t.prazo:
        partes.append(f"prazo: {rotulo_data(t.prazo, ref)}")
    if t.agendada_para:
        partes.append(f"agendada: {rotulo_data(t.agendada_para, ref)}" + (f" às {t.hora}" if t.hora else ""))
    elif t.hora:
        partes.append(f"às {t.hora}")
    partes.append(f"prio {PRIORIDADES.get(t.prioridade, t.prioridade)}")
    if t.esforco_min:
        partes.append(f"~{t.esforco_min}min")
    if t.contexto:
        partes.append(f"ctx: {t.contexto}")
    if t.depende_de:
        partes.append("depende de " + ", ".join(f"#{i}" for i in t.depende_de.split(",")))
    if t.recorrencia:
        partes.append(f"repete: {t.recorrencia}")
    if t.adiamentos:
        partes.append(f"já adiada {t.adiamentos}x")
    linha = " | ".join(partes)
    if t.detalhes:
        linha += f"\n    obs: {t.detalhes}"
    return linha


# ---------- estado ----------

def estado_get(db: Session, chave: str) -> Optional[str]:
    return db.execute(select(estado.c.valor).where(estado.c.chave == chave)).scalar()


def estado_set(db: Session, chave: str, valor: str) -> None:
    if estado_get(db, chave) is None:
        db.execute(insert(estado).values(chave=chave, valor=valor))
    else:
        db.execute(update(estado).where(estado.c.chave == chave).values(valor=valor))
    db.commit()


# ---------- tarefas ----------

CAMPOS_TAREFA = ("titulo", "detalhes", "area", "status", "prazo", "agendada_para", "hora",
                 "prioridade", "esforco_min", "contexto", "depende_de", "recorrencia")


def _limpar_campos(dados: dict) -> dict:
    campos = {}
    for chave in CAMPOS_TAREFA:
        if chave not in dados:
            continue
        valor = dados[chave]
        if chave in ("prazo", "agendada_para"):
            valor = ler_data(valor)
        elif chave == "hora":
            valor = ler_hora(valor)
        elif chave == "depende_de":
            valor = ler_ids(valor)
        elif chave == "recorrencia":
            valor = recorrencia.validar(valor) if valor else None
        elif chave == "area":
            valor = "profissional" if str(valor).lower().startswith("prof") else "pessoal"
        elif chave == "status":
            if valor not in ("pendente", "aguardando", "feita", "cancelada"):
                raise ValueError(f"status inválido: {valor}")
        elif chave == "prioridade":
            valor = min(3, max(1, int(valor)))
        elif chave == "esforco_min":
            valor = int(valor) if valor not in (None, "") else None
        elif chave == "titulo":
            valor = str(valor or "").strip()
            if not valor:
                raise ValueError("título vazio")
        campos[chave] = valor
    return campos


def criar_tarefa(db: Session, dados: dict) -> int:
    campos = _limpar_campos(dados)
    if "titulo" not in campos:
        raise ValueError("tarefa sem título")
    campos.setdefault("area", "pessoal")
    campos.setdefault("status", "pendente")
    campos.setdefault("prioridade", 2)
    if campos.get("recorrencia") and not campos.get("agendada_para"):
        campos["agendada_para"] = recorrencia.proxima_a_partir_de(campos["recorrencia"], hoje())
    resultado = db.execute(insert(tarefas).values(**campos, adiamentos=0, criada_em=agora()))
    db.commit()
    return resultado.inserted_primary_key[0]


def obter_tarefa(db: Session, tarefa_id: int):
    return db.execute(select(tarefas).where(tarefas.c.id == tarefa_id)).first()


def atualizar_tarefa(db: Session, tarefa_id: int, dados: dict) -> None:
    atual = obter_tarefa(db, tarefa_id)
    if atual is None:
        raise ValueError(f"tarefa #{tarefa_id} não existe")
    campos = _limpar_campos(dados)
    if not campos:
        raise ValueError("nada para atualizar")
    # Empurrar pra frente uma tarefa que já estava marcada conta como adiamento.
    nova_data = campos.get("agendada_para")
    if (atual.status in STATUS_ABERTOS and atual.agendada_para and nova_data
            and nova_data > atual.agendada_para and atual.agendada_para <= hoje()):
        campos["adiamentos"] = (atual.adiamentos or 0) + 1
    if campos.get("status") == "cancelada":
        campos["concluida_em"] = agora()
    elif campos.get("status") in STATUS_ABERTOS:
        campos["concluida_em"] = None  # reaberta
    db.execute(update(tarefas).where(tarefas.c.id == tarefa_id).values(**campos))
    db.commit()


def concluir_tarefa(db: Session, tarefa_id: int, observacao: str = "",
                    momento: Optional[datetime] = None) -> str:
    t = obter_tarefa(db, tarefa_id)
    if t is None:
        raise ValueError(f"tarefa #{tarefa_id} não existe")
    if t.status == "feita":
        return f"#{tarefa_id} já estava concluída"
    momento = momento or agora()
    db.execute(update(tarefas).where(tarefas.c.id == tarefa_id)
               .values(status="feita", concluida_em=momento))
    texto = t.titulo + (f" — {observacao}" if observacao else "")
    db.execute(insert(registros).values(momento=momento, texto=texto, area=t.area, tarefa_id=tarefa_id))
    resposta = f"#{tarefa_id} concluída"
    if t.recorrencia:
        # A ocorrência que esta tarefa representa é o prazo (conta do dia 10) ou,
        # sem prazo, o dia agendado (academia de quarta). A próxima vem depois dela,
        # mesmo que esta tenha sido feita adiantada.
        ocorrencia = t.prazo or t.agendada_para or momento.date()
        proxima = recorrencia.proxima_data(t.recorrencia, ocorrencia)
        if proxima and not t.prazo and proxima <= momento.date():
            proxima = recorrencia.proxima_data(t.recorrencia, momento.date())  # hábito não acumula
        if proxima:
            prazo, agendada = None, proxima
            if t.prazo:
                antecedencia = (t.prazo - t.agendada_para) if t.agendada_para else timedelta(0)
                prazo, agendada = proxima, proxima - max(antecedencia, timedelta(0))
            nova = db.execute(insert(tarefas).values(
                titulo=t.titulo, detalhes=t.detalhes, area=t.area, status="pendente",
                prazo=prazo, agendada_para=agendada, hora=t.hora, prioridade=t.prioridade,
                esforco_min=t.esforco_min, contexto=t.contexto, depende_de=None,
                recorrencia=t.recorrencia, adiamentos=0, criada_em=agora(),
            ))
            resposta += f"; próxima repetição criada: #{nova.inserted_primary_key[0]} para {rotulo_data(agendada)}"
    db.commit()
    return resposta


def tarefas_abertas(db: Session):
    return db.execute(
        select(tarefas).where(tarefas.c.status.in_(STATUS_ABERTOS))
        .order_by(tarefas.c.agendada_para.is_(None), tarefas.c.agendada_para,
                  tarefas.c.prazo.is_(None), tarefas.c.prazo, tarefas.c.prioridade, tarefas.c.id)
    ).fetchall()


def buscar_tarefas(db: Session, termo: str = "", status: str = "", desde=None, ate=None, limite: int = 40):
    consulta = select(tarefas)
    if termo:
        padrao = f"%{termo.lower()}%"
        consulta = consulta.where(or_(func.lower(tarefas.c.titulo).like(padrao),
                                      func.lower(tarefas.c.detalhes).like(padrao)))
    if status:
        consulta = consulta.where(tarefas.c.status == status)
    if desde:
        consulta = consulta.where(tarefas.c.criada_em >= datetime.combine(ler_data(desde), datetime.min.time()))
    if ate:
        consulta = consulta.where(tarefas.c.criada_em < datetime.combine(ler_data(ate) + timedelta(days=1), datetime.min.time()))
    return db.execute(consulta.order_by(tarefas.c.id.desc()).limit(limite)).fetchall()


def rolar_atrasadas(db: Session, ref: Optional[date] = None) -> int:
    """Tarefas agendadas para dias que já passaram vêm para hoje.

    Tarefa normal (ou repetitiva com prazo, tipo conta): vem pra hoje e conta um
    adiamento. Hábito repetitivo sem prazo: só pula para a próxima ocorrência
    (faltar à academia ontem não é "adiar").
    """
    ref = ref or hoje()
    atrasadas = db.execute(select(tarefas).where(and_(
        tarefas.c.status.in_(STATUS_ABERTOS), tarefas.c.agendada_para < ref,
    ))).fetchall()
    for t in atrasadas:
        if t.recorrencia and not t.prazo:
            nova = recorrencia.proxima_a_partir_de(t.recorrencia, ref) or ref
            db.execute(update(tarefas).where(tarefas.c.id == t.id).values(agendada_para=nova))
        else:
            db.execute(update(tarefas).where(tarefas.c.id == t.id)
                       .values(agendada_para=ref, adiamentos=(t.adiamentos or 0) + 1))
    db.commit()
    return len(atrasadas)


# ---------- registros (o que foi feito) ----------

def registrar(db: Session, texto: str, area: Optional[str] = None, tarefa_id: Optional[int] = None,
              momento: Optional[datetime] = None) -> int:
    if area:
        area = "profissional" if str(area).lower().startswith("prof") else "pessoal"
    resultado = db.execute(insert(registros).values(
        momento=momento or agora(), texto=texto.strip(), area=area, tarefa_id=tarefa_id))
    db.commit()
    return resultado.inserted_primary_key[0]


def registros_periodo(db: Session, inicio: date, fim: date):
    return db.execute(select(registros).where(and_(
        registros.c.momento >= datetime.combine(inicio, datetime.min.time()),
        registros.c.momento < datetime.combine(fim + timedelta(days=1), datetime.min.time()),
    )).order_by(registros.c.momento)).fetchall()


def formatar_registros(linhas) -> str:
    if not linhas:
        return "(nada registrado)"
    saida, dia_atual = [], None
    for r in linhas:
        if r.momento.date() != dia_atual:
            dia_atual = r.momento.date()
            saida.append(f"{DIAS_SEMANA[dia_atual.weekday()]} {dia_atual:%d/%m}:")
        area = {"profissional": "prof", "pessoal": "pess"}.get(r.area or "", "?")
        tarefa = f" (#{r.tarefa_id})" if r.tarefa_id else ""
        saida.append(f"  {r.momento:%H:%M} [{area}] {r.texto}{tarefa}")
    return "\n".join(saida)


# ---------- lembretes ----------

def criar_lembrete(db: Session, quando, texto: str) -> int:
    momento = ler_momento(quando)
    resultado = db.execute(insert(lembretes).values(
        quando=momento, texto=texto.strip(), enviado=False, criado_em=agora()))
    db.commit()
    return resultado.inserted_primary_key[0]


def cancelar_lembrete(db: Session, lembrete_id: int) -> bool:
    resultado = db.execute(delete(lembretes).where(and_(
        lembretes.c.id == lembrete_id, lembretes.c.enviado.is_(False))))
    db.commit()
    return resultado.rowcount > 0


def lembretes_futuros(db: Session):
    return db.execute(select(lembretes).where(lembretes.c.enviado.is_(False))
                      .order_by(lembretes.c.quando)).fetchall()


def lembretes_vencidos(db: Session):
    return db.execute(select(lembretes).where(and_(
        lembretes.c.enviado.is_(False), lembretes.c.quando <= agora(),
    )).order_by(lembretes.c.quando)).fetchall()


def marcar_lembrete_enviado(db: Session, lembrete_id: int) -> None:
    db.execute(update(lembretes).where(lembretes.c.id == lembrete_id).values(enviado=True))
    db.commit()


# ---------- memória sobre a pessoa ----------

def salvar_memoria(db: Session, fato: str) -> int:
    resultado = db.execute(insert(memorias).values(fato=fato.strip(), criada_em=agora()))
    db.commit()
    return resultado.inserted_primary_key[0]


def apagar_memoria(db: Session, memoria_id: int) -> bool:
    resultado = db.execute(delete(memorias).where(memorias.c.id == memoria_id))
    db.commit()
    return resultado.rowcount > 0


def listar_memorias(db: Session):
    return db.execute(select(memorias).order_by(memorias.c.id)).fetchall()


# ---------- mensagens ----------

def salvar_mensagem(db: Session, papel: str, texto: str, processada: bool = True) -> int:
    resultado = db.execute(insert(mensagens).values(
        papel=papel, texto=texto, momento=agora(), processada=processada))
    db.commit()
    return resultado.inserted_primary_key[0]


def mensagens_pendentes(db: Session):
    return db.execute(select(mensagens).where(and_(
        mensagens.c.papel == "user", mensagens.c.processada.is_(False),
    )).order_by(mensagens.c.id)).fetchall()


def marcar_processadas(db: Session, ids: list[int]) -> None:
    if ids:
        db.execute(update(mensagens).where(mensagens.c.id.in_(ids)).values(processada=True))
        db.commit()


def historico_recente(db: Session, limite: int, antes_de_id: Optional[int] = None, horas: int = 36):
    consulta = select(mensagens).where(and_(
        mensagens.c.processada.is_(True),
        mensagens.c.momento >= agora() - timedelta(hours=horas),
    ))
    if antes_de_id is not None:
        consulta = consulta.where(mensagens.c.id < antes_de_id)
    linhas = db.execute(consulta.order_by(mensagens.c.id.desc()).limit(limite)).fetchall()
    return list(reversed(linhas))


# ---------- retrato do momento para a IA ----------

def montar_contexto(db: Session) -> str:
    momento = agora()
    ref = momento.date()
    proximos = " · ".join(
        f"{DIAS_CURTOS[(ref + timedelta(days=i)).weekday()]} {(ref + timedelta(days=i)):%d/%m}"
        for i in range(1, 15)
    )
    abertas = tarefas_abertas(db)
    pendentes = [t for t in abertas if t.status == "pendente"]
    aguardando = [t for t in abertas if t.status == "aguardando"]
    feito_hoje = registros_periodo(db, ref, ref)
    futuros = lembretes_futuros(db)
    fatos = listar_memorias(db)

    partes = [
        f"AGORA: {DIAS_SEMANA[ref.weekday()]}, {momento:%d/%m/%Y %H:%M} (horário de Brasília)",
        f"PRÓXIMOS 14 DIAS: {proximos}",
        "",
        f"TAREFAS PENDENTES ({len(pendentes)}):",
        *([linha_tarefa(t, ref) for t in pendentes[:150]] or ["(nenhuma)"]),
    ]
    if len(pendentes) > 150:
        partes.append(f"... e mais {len(pendentes) - 150} (use buscar_tarefas)")
    partes += ["", f"AGUARDANDO TERCEIROS ({len(aguardando)}):",
               *([linha_tarefa(t, ref) for t in aguardando] or ["(nenhuma)"])]
    partes += ["", "FEITO HOJE:", formatar_registros(feito_hoje)]
    partes += ["", "LEMBRETES AGENDADOS:",
               *([f"#{l.id} {rotulo_data(l.quando.date(), ref)} {l.quando:%H:%M} — {l.texto}" for l in futuros[:30]]
                 or ["(nenhum)"])]
    partes += ["", "O QUE VOCÊ JÁ SABE SOBRE A PESSOA:",
               *([f"(m{f.id}) {f.fato}" for f in fatos] or ["(nada ainda — vá descobrindo e salvando)"])]
    return "\n".join(partes)
