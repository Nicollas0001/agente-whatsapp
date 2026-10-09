"""Anota como o navegador pede as páginas do app em /app.

No tablet, abrir o app instalado faz o Chrome oferecer "baixar download.html" (a própria
página). Os cabeçalhos dizem quem pediu e como: a navegação do app, um download do Chrome,
o gerenciador de downloads do Android, uma pré-busca... Fica em sec_diagnostico, que só o
servidor lê. SECRETARIA_DIAGNOSTICO=0 desliga."""
import logging
import threading

from app.secretaria import banco, config, repositorio as repo

log = logging.getLogger("secretaria")

CABECALHOS = ("user-agent", "accept", "sec-fetch-dest", "sec-fetch-mode", "sec-fetch-site", "sec-fetch-user",
              "sec-purpose", "purpose", "range", "x-requested-with", "referer", "service-worker",
              "cache-control", "if-none-match", "sec-ch-ua-platform")


def anotar(metodo: str, caminho: str, consulta: str, cabecalhos, status: int) -> None:
    if not (config.DIAGNOSTICO and banco.configurado()):
        return
    dados = {"quando": repo.agora().isoformat(sep=" ", timespec="seconds"), "metodo": metodo,
             "caminho": caminho + (f"?{consulta}" if consulta else ""), "status": status}
    dados.update({c: cabecalhos.get(c) for c in CABECALHOS if cabecalhos.get(c)})
    threading.Thread(target=_gravar, args=(dados,), daemon=True).start()   # não atrasa a resposta


def _gravar(dados: dict) -> None:
    try:
        banco.cliente().collection("sec_diagnostico").add(dados)
    except Exception:
        log.warning("Não consegui anotar o diagnóstico do pedido", exc_info=True)
