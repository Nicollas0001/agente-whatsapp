"""Regras de repetição de tarefas.

Formatos aceitos:
  diaria                 todo dia
  dias_uteis             segunda a sexta
  semanal:seg,qua,sex    dias da semana (seg ter qua qui sex sab dom)
  mensal:10              todo dia 10 (dia 31 vira o último dia do mês)
  a_cada:3               a cada 3 dias
"""
import calendar
from datetime import date, timedelta
from typing import Optional

DIAS = ["seg", "ter", "qua", "qui", "sex", "sab", "dom"]


def _dias_semana(texto: str) -> list[int]:
    dias = []
    for parte in texto.split(","):
        parte = parte.strip().lower()[:3].replace("á", "a")
        if parte not in DIAS:
            raise ValueError(f"dia da semana inválido: {parte!r}")
        dias.append(DIAS.index(parte))
    return sorted(set(dias))


def validar(regra: str) -> str:
    """Normaliza a regra ou levanta ValueError explicando o formato."""
    regra = (regra or "").strip().lower()
    tipo, _, arg = regra.partition(":")
    if tipo in ("diaria", "dias_uteis") and not arg:
        return tipo
    if tipo == "semanal" and arg:
        return "semanal:" + ",".join(DIAS[d] for d in _dias_semana(arg))
    if tipo == "mensal" and arg.isdigit() and 1 <= int(arg) <= 31:
        return f"mensal:{int(arg)}"
    if tipo == "a_cada" and arg.isdigit() and int(arg) >= 1:
        return f"a_cada:{int(arg)}"
    raise ValueError(
        "recorrência inválida; use diaria, dias_uteis, semanal:seg,qua, mensal:10 ou a_cada:3"
    )


def _dia_do_mes(ano: int, mes: int, dia: int) -> date:
    return date(ano, mes, min(dia, calendar.monthrange(ano, mes)[1]))


def proxima_data(regra: str, depois_de: date) -> Optional[date]:
    """Primeira data estritamente depois de `depois_de` que obedece a regra."""
    try:
        regra = validar(regra)
    except ValueError:
        return None
    tipo, _, arg = regra.partition(":")
    if tipo == "diaria":
        return depois_de + timedelta(days=1)
    if tipo == "a_cada":
        return depois_de + timedelta(days=int(arg))
    if tipo in ("dias_uteis", "semanal"):
        dias = [0, 1, 2, 3, 4] if tipo == "dias_uteis" else _dias_semana(arg)
        d = depois_de + timedelta(days=1)
        while d.weekday() not in dias:
            d += timedelta(days=1)
        return d
    if tipo == "mensal":
        dia = int(arg)
        candidata = _dia_do_mes(depois_de.year, depois_de.month, dia)
        if candidata > depois_de:
            return candidata
        ano, mes = (depois_de.year + 1, 1) if depois_de.month == 12 else (depois_de.year, depois_de.month + 1)
        return _dia_do_mes(ano, mes, dia)
    return None


def proxima_a_partir_de(regra: str, hoje: date) -> Optional[date]:
    """Próxima ocorrência em `hoje` ou depois."""
    return proxima_data(regra, hoje - timedelta(days=1))
