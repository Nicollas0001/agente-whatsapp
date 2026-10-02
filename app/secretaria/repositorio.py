"""Dados da secretária no Firestore e o "retrato" que a IA recebe a cada conversa.

Coleções (todas com prefixo sec_, só o servidor acessa):
  sec_tarefas, sec_registros, sec_lembretes, sec_memorias, sec_mensagens,
  sec_estado, sec_contadores, sec_uso
Datas são strings ISO no horário local (AAAA-MM-DD e AAAA-MM-DDTHH:MM:SS),
o que deixa a ordenação e os filtros por período simples.
"""
import time
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from typing import Optional

from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from app.secretaria import banco, config, diario, recorrencia

DIAS_SEMANA = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira",
               "sexta-feira", "sábado", "domingo"]
DIAS_CURTOS = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"]
STATUS_ABERTOS = ("pendente", "aguardando")
PRIORIDADES = {1: "alta", 2: "média", 3: "baixa"}


def _col(nome: str):
    return banco.cliente().collection(f"sec_{nome}")


def _anotar(nome: str, doc_id, novo: bool = False) -> None:
    """Guarda no diário (se a IA estiver agindo) como o documento estava antes de mudar."""
    atual = diario.ativo()
    if atual is None or (f"sec_{nome}", str(doc_id)) in atual.docs:
        return
    if novo:
        atual.anotar_doc(f"sec_{nome}", doc_id, None)
        return
    snap = _col(nome).document(str(doc_id)).get()
    atual.anotar_doc(f"sec_{nome}", doc_id, snap.to_dict() if snap.exists else None)


def agora() -> datetime:
    return datetime.now(config.FUSO).replace(tzinfo=None, microsecond=0)


def hoje() -> date:
    return agora().date()


# ---------- conversão de entradas ----------

def ler_data(valor) -> Optional[date]:
    if valor in (None, ""):
        return None
    if isinstance(valor, datetime):
        return valor.date()
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
    if isinstance(valor, datetime):
        momento = valor
    else:
        momento = datetime.fromisoformat(str(valor).strip().replace(" ", "T"))
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


def _iso(valor) -> Optional[str]:
    if valor is None:
        return None
    if isinstance(valor, datetime):
        return valor.replace(microsecond=0).isoformat()
    return valor.isoformat()


def _momento(texto) -> Optional[datetime]:
    return datetime.fromisoformat(texto) if texto else None


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


# ---------- estado e contadores ----------

def estado_get(chave: str) -> Optional[str]:
    snap = _col("estado").document(chave).get()
    return snap.to_dict().get("valor") if snap.exists else None


def estado_set(chave: str, valor: str) -> None:
    _col("estado").document(chave).set({"valor": valor})


@firestore.transactional
def _incrementar(transacao, ref) -> int:
    snap = ref.get(transaction=transacao)
    numero = ((snap.to_dict() or {}).get("valor", 0) if snap.exists else 0) + 1
    transacao.set(ref, {"valor": numero})
    return numero


def proximo_id(nome: str) -> int:
    """Números curtos e sequenciais (#12), que a IA e a pessoa conseguem citar."""
    return _incrementar(banco.cliente().transaction(), _col("contadores").document(nome))


# ---------- tarefas ----------

CAMPOS_TAREFA = ("titulo", "detalhes", "area", "status", "prazo", "agendada_para", "hora",
                 "prioridade", "esforco_min", "contexto", "depende_de", "recorrencia")


def _tarefa(snap) -> SimpleNamespace:
    d = snap.to_dict()
    return SimpleNamespace(
        id=int(snap.id), titulo=d.get("titulo"), detalhes=d.get("detalhes"), area=d.get("area", "pessoal"),
        status=d.get("status", "pendente"), prazo=ler_data(d.get("prazo")),
        agendada_para=ler_data(d.get("agendada_para")), hora=d.get("hora"),
        prioridade=d.get("prioridade", 2), esforco_min=d.get("esforco_min"), contexto=d.get("contexto"),
        depende_de=d.get("depende_de"), recorrencia=d.get("recorrencia"), adiamentos=d.get("adiamentos", 0),
        criada_em=_momento(d.get("criada_em")), concluida_em=_momento(d.get("concluida_em")),
    )


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


def _para_banco(campos: dict) -> dict:
    return {k: (_iso(v) if isinstance(v, (date, datetime)) else v) for k, v in campos.items()}


def criar_tarefa(dados: dict) -> int:
    campos = _limpar_campos(dados)
    if "titulo" not in campos:
        raise ValueError("tarefa sem título")
    campos.setdefault("area", "pessoal")
    campos.setdefault("status", "pendente")
    campos.setdefault("prioridade", 2)
    if campos.get("recorrencia") and not campos.get("agendada_para"):
        campos["agendada_para"] = recorrencia.proxima_a_partir_de(campos["recorrencia"], hoje())
    tarefa_id = proximo_id("tarefas")
    _anotar("tarefas", tarefa_id, novo=True)
    _col("tarefas").document(str(tarefa_id)).set(_para_banco({
        **{c: None for c in CAMPOS_TAREFA}, **campos,
        "adiamentos": 0, "criada_em": agora(), "concluida_em": None,
    }))
    return tarefa_id


def obter_tarefa(tarefa_id: int):
    snap = _col("tarefas").document(str(int(tarefa_id))).get()
    return _tarefa(snap) if snap.exists else None


def atualizar_tarefa(tarefa_id: int, dados: dict) -> None:
    atual = obter_tarefa(tarefa_id)
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
    _anotar("tarefas", int(tarefa_id))
    _col("tarefas").document(str(int(tarefa_id))).update(_para_banco(campos))


def concluir_tarefa(tarefa_id: int, observacao: str = "", momento: Optional[datetime] = None) -> str:
    t = obter_tarefa(tarefa_id)
    if t is None:
        raise ValueError(f"tarefa #{tarefa_id} não existe")
    if t.status == "feita":
        return f"#{tarefa_id} já estava concluída"
    momento = momento or agora()
    _anotar("tarefas", t.id)
    _col("tarefas").document(str(t.id)).update({"status": "feita", "concluida_em": _iso(momento)})
    registrar(t.titulo + (f" — {observacao}" if observacao else ""), t.area, t.id, momento)
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
            nova_id = criar_tarefa({
                "titulo": t.titulo, "detalhes": t.detalhes, "area": t.area, "prazo": prazo,
                "agendada_para": agendada, "hora": t.hora, "prioridade": t.prioridade,
                "esforco_min": t.esforco_min, "contexto": t.contexto, "recorrencia": t.recorrencia,
            })
            resposta += f"; próxima repetição criada: #{nova_id} para {rotulo_data(agendada)}"
    return resposta


def _ordem_tarefa(t):
    return (t.agendada_para is None, t.agendada_para or date.max,
            t.prazo is None, t.prazo or date.max, t.prioridade, t.id)


def tarefas_abertas() -> list:
    consulta = _col("tarefas").where(filter=FieldFilter("status", "in", list(STATUS_ABERTOS)))
    return sorted((_tarefa(s) for s in consulta.stream()), key=_ordem_tarefa)


def buscar_tarefas(termo: str = "", status: str = "", desde=None, ate=None, limite: int = 40) -> list:
    consulta = _col("tarefas")
    if status:
        consulta = consulta.where(filter=FieldFilter("status", "==", status))
    achadas = []
    termo = (termo or "").lower()
    desde, ate = ler_data(desde), ler_data(ate)
    for t in (_tarefa(s) for s in consulta.stream()):
        if termo and termo not in f"{t.titulo} {t.detalhes or ''}".lower():
            continue
        if desde and t.criada_em.date() < desde:
            continue
        if ate and t.criada_em.date() > ate:
            continue
        achadas.append(t)
    return sorted(achadas, key=lambda t: -t.id)[:limite]


def rolar_atrasadas(ref: Optional[date] = None) -> int:
    """Tarefas agendadas para dias que já passaram vêm para hoje.

    Tarefa normal (ou repetitiva com prazo, tipo conta): vem pra hoje e conta um
    adiamento. Hábito repetitivo sem prazo: só pula para a próxima ocorrência
    (faltar à academia ontem não é "adiar").
    """
    ref = ref or hoje()
    atrasadas = [t for t in tarefas_abertas() if t.agendada_para and t.agendada_para < ref]
    for t in atrasadas:
        _anotar("tarefas", t.id)
        doc = _col("tarefas").document(str(t.id))
        if t.recorrencia and not t.prazo:
            nova = recorrencia.proxima_a_partir_de(t.recorrencia, ref) or ref
            doc.update({"agendada_para": _iso(nova)})
        else:
            doc.update({"agendada_para": _iso(ref), "adiamentos": (t.adiamentos or 0) + 1})
    return len(atrasadas)


# ---------- registros (o que foi feito) ----------

def registrar(texto: str, area: Optional[str] = None, tarefa_id: Optional[int] = None,
              momento: Optional[datetime] = None) -> str:
    if area:
        area = "profissional" if str(area).lower().startswith("prof") else "pessoal"
    ref = _col("registros").document()
    _anotar("registros", ref.id, novo=True)
    ref.set({"momento": _iso(momento or agora()), "texto": texto.strip(), "area": area, "tarefa_id": tarefa_id})
    return ref.id


def registros_periodo(inicio: date, fim: date) -> list:
    consulta = (_col("registros")
                .where(filter=FieldFilter("momento", ">=", inicio.isoformat()))
                .where(filter=FieldFilter("momento", "<", (fim + timedelta(days=1)).isoformat()))
                .order_by("momento"))
    return [SimpleNamespace(momento=_momento(d["momento"]), texto=d["texto"], area=d.get("area"),
                            tarefa_id=d.get("tarefa_id"))
            for d in (s.to_dict() for s in consulta.stream())]


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

def _lembrete(snap) -> SimpleNamespace:
    d = snap.to_dict()
    return SimpleNamespace(id=int(snap.id), quando=_momento(d["quando"]), texto=d["texto"],
                           enviado=d.get("enviado", False))


def criar_lembrete(quando, texto: str) -> int:
    lembrete_id = proximo_id("lembretes")
    _anotar("lembretes", lembrete_id, novo=True)
    _col("lembretes").document(str(lembrete_id)).set({
        "quando": _iso(ler_momento(quando)), "texto": texto.strip(), "enviado": False, "criado_em": _iso(agora()),
    })
    return lembrete_id


def _lembretes_pendentes() -> list:
    consulta = _col("lembretes").where(filter=FieldFilter("enviado", "==", False))
    return sorted((_lembrete(s) for s in consulta.stream()), key=lambda l: l.quando)


def cancelar_lembrete(lembrete_id: int) -> bool:
    doc = _col("lembretes").document(str(int(lembrete_id)))
    snap = doc.get()
    if not snap.exists or snap.to_dict().get("enviado"):
        return False
    _anotar("lembretes", int(lembrete_id))
    doc.delete()
    return True


def lembretes_futuros() -> list:
    return _lembretes_pendentes()


def lembretes_vencidos() -> list:
    momento = agora()
    return [l for l in _lembretes_pendentes() if l.quando <= momento]


def marcar_lembrete_enviado(lembrete_id: int) -> None:
    _col("lembretes").document(str(int(lembrete_id))).update({"enviado": True})


# ---------- memória sobre a pessoa ----------

def salvar_memoria(fato: str) -> int:
    memoria_id = proximo_id("memorias")
    _anotar("memorias", memoria_id, novo=True)
    _col("memorias").document(str(memoria_id)).set({"fato": fato.strip(), "criada_em": _iso(agora())})
    return memoria_id


def apagar_memoria(memoria_id: int) -> bool:
    doc = _col("memorias").document(str(int(memoria_id)))
    if not doc.get().exists:
        return False
    _anotar("memorias", int(memoria_id))
    doc.delete()
    return True


def listar_memorias() -> list:
    return sorted((SimpleNamespace(id=int(s.id), fato=s.to_dict()["fato"]) for s in _col("memorias").stream()),
                  key=lambda m: m.id)


# ---------- mensagens ----------

def _mensagem(snap) -> SimpleNamespace:
    d = snap.to_dict()
    return SimpleNamespace(id=d["seq"], papel=d["papel"], texto=d["texto"],
                           momento=_momento(d["momento"]), processada=d.get("processada", True))


def salvar_mensagem(papel: str, texto: str, processada: bool = True) -> int:
    seq = time.time_ns()  # ordena as mensagens sem precisar de contador
    _col("mensagens").document(str(seq)).set({
        "seq": seq, "papel": papel, "texto": texto, "momento": _iso(agora()), "processada": processada,
    })
    return seq


def mensagens_pendentes() -> list:
    consulta = _col("mensagens").where(filter=FieldFilter("processada", "==", False))
    return sorted((_mensagem(s) for s in consulta.stream() if s.to_dict().get("papel") == "user"),
                  key=lambda m: m.id)


def marcar_processadas(ids: list) -> None:
    if not ids:
        return
    lote = banco.cliente().batch()
    for seq in ids:
        lote.update(_col("mensagens").document(str(seq)), {"processada": True})
    lote.commit()


def historico_recente(limite: int, antes_de_id: Optional[int] = None, horas: int = 36) -> list:
    consulta = _col("mensagens")
    if antes_de_id is not None:
        consulta = consulta.where(filter=FieldFilter("seq", "<", antes_de_id))
    consulta = consulta.order_by("seq", direction=firestore.Query.DESCENDING).limit(limite * 2)
    corte = agora() - timedelta(hours=horas)
    linhas = [m for m in (_mensagem(s) for s in consulta.stream()) if m.processada and m.momento >= corte]
    return list(reversed(linhas[:limite]))


# ---------- custo (uso da API) ----------

def registrar_uso(modelo: str, usage) -> None:
    valores = {
        "entrada": getattr(usage, "input_tokens", 0) or 0,
        "saida": getattr(usage, "output_tokens", 0) or 0,
        "cache_escrita": getattr(usage, "cache_creation_input_tokens", 0) or 0,
        "cache_leitura": getattr(usage, "cache_read_input_tokens", 0) or 0,
        "chamadas": 1,
    }
    _col("uso").document(agora().strftime("%Y-%m")).set(
        {"modelos": {modelo: {k: firestore.Increment(v) for k, v in valores.items()}}}, merge=True)


def uso_do_mes(mes: Optional[str] = None) -> dict:
    snap = _col("uso").document(mes or agora().strftime("%Y-%m")).get()
    return (snap.to_dict() or {}).get("modelos", {}) if snap.exists else {}


# ---------- retrato do momento para a IA ----------

def montar_contexto(incluir_financas: bool = True) -> str:
    momento = agora()
    ref = momento.date()
    proximos = " · ".join(
        f"{DIAS_CURTOS[(ref + timedelta(days=i)).weekday()]} {(ref + timedelta(days=i)):%d/%m}"
        for i in range(1, 15)
    )
    abertas = tarefas_abertas()
    pendentes = [t for t in abertas if t.status == "pendente"]
    aguardando = [t for t in abertas if t.status == "aguardando"]
    feito_hoje = registros_periodo(ref, ref)
    futuros = lembretes_futuros()
    fatos = listar_memorias()

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
    if incluir_financas:
        from app.secretaria import financas
        partes += ["", financas.retrato()]
    return "\n".join(partes)
