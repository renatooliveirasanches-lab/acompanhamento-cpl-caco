"""
Le a PLANILHA DE VIEWS LANCAMENTO, e pra cada CPL de cada lancamento puxa do
YouTube Analytics as views do DIA DA ESTREIA (a regua de comparacao).

Nao escreve nada: cospe um CSV pra conferencia.

Uso:  python3 levantar_historico.py > historico.csv
"""

import csv
import re
import subprocess
import sys

import youtube_analytics as ya

PLANILHA = "1NINDFUX7r1a49lW8ZCIaPMpLTYKyaPLEeOB_sepChx8"
ABA = "CPL - ATUALIZADO"


def ler_planilha():
    saida = subprocess.run(
        ["gws", "sheets", "+read", "--spreadsheet", PLANILHA,
         "--range", f"{ABA}!A1:J50", "--format", "json"],
        capture_output=True, text=True, check=True,
        env={**__import__("os").environ,
             "GOOGLE_WORKSPACE_CLI_CONFIG_DIR": __import__("os").path.expanduser("~/.gws/personal")},
    ).stdout
    import json
    return json.loads(saida[saida.index("{"):])["values"]


def id_do_link(link):
    m = re.search(r"(?:youtu\.be/|v=|/live/|/embed/)([A-Za-z0-9_-]{11})", link or "")
    return m.group(1) if m else ""


def n(txt):
    """'21.835' -> 21835"""
    return int(re.sub(r"\D", "", txt or "0") or 0)


def estreia(serie, limiar=100):
    """
    O dia da estreia NAO e o primeiro dia com qualquer view: videos ficam
    'nao listados' antes e pegam 1-2 acessos de teste. E o primeiro dia com
    movimento de verdade.
    """
    for i, (dia, v) in enumerate(serie):
        if v >= limiar:
            return i, dia, v
    return (0, serie[0][0], serie[0][1]) if serie else (0, "", 0)


def main():
    linhas = ler_planilha()
    w = csv.writer(sys.stdout)
    w.writerow(["CPL", "LANCAMENTO", "PERSONAGEM", "VIDEO_ID", "ESTREIA",
                "LEADS", "VIEWS_DIA_ESTREIA", "PCT", "PLANILHA_HOJE", "TOTAL_HOJE"])

    cpl_atual = ""
    for linha in linhas:
        linha = list(linha) + [""] * (10 - len(linha))
        if (linha[1] or "").strip().upper().startswith("CPL "):
            cpl_atual = re.sub(r"\D", "", linha[1])
            continue
        vid = id_do_link(linha[2])
        if not vid:
            continue
        leads = n(linha[3])
        try:
            serie = ya.serie_diaria(vid)
        except Exception as e:
            print(f"# ERRO {vid}: {e}", file=sys.stderr)
            continue
        if not serie:
            w.writerow([cpl_atual, linha[0], linha[9], vid, "SEM DADOS",
                        leads, "", "", linha[4], ""])
            continue
        _, dia, views = estreia(serie)
        pct = round(views / leads * 100, 2) if leads else ""
        w.writerow([cpl_atual, linha[0], linha[8], vid, dia, leads, views, pct,
                    linha[4], sum(v for _, v in serie)])


if __name__ == "__main__":
    main()
