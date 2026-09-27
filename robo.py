"""
ACOMPANHAMENTO CPL - CACO AMIGURUMI (versao GitHub)
Roda na nuvem do GitHub de hora em hora. Para cada CPL marcado como ATIVO
na planilha do Google, le as views/likes/comentarios publicos do YouTube
(pela chave de API), calcula o % de views sobre os leads e compara com os
lancamentos anteriores no MESMO ponto do tempo (mesma hora desde a publicacao).
Guarda o historico em dados/snapshots.csv e avisa no Telegram.

Nada de login do Google -> nenhuma trava de seguranca.
O Renato so mexe na planilha (aba CONFIG). O resto e automatico.
"""

import csv
import io
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from zoneinfo import ZoneInfo


def _get(url, params=None, timeout=30):
    if params:
        url += "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read().decode("utf-8")


def _post(url, dados, timeout=30):
    corpo = urllib.parse.urlencode(dados).encode()
    with urllib.request.urlopen(url, corpo, timeout=timeout) as r:
        return r.read().decode("utf-8")

# ===================== CONFIGURACAO =====================

# ID da planilha do Google (aba CONFIG). Nao e segredo.
SHEET_ID = "1ad449AEPFVZ4XJVNp1EugIUFI6E1TngC_BETVRZ2DpI"
ABA_CONFIG = "CONFIG"

# Segredos vem das variaveis de ambiente (os "secrets" do GitHub).
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY", "")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

TOLERANCIA = 0.02  # 2% pra cima/baixo conta como "igual"
FUSO = ZoneInfo("America/Sao_Paulo")
ARQUIVO_SNAPSHOTS = os.path.join("dados", "snapshots.csv")
ARQUIVO_REGUA = os.path.join("dados", "regua.csv")

CABECALHO_SNAPSHOTS = [
    "TIMESTAMP", "LANCAMENTO", "TAG", "CPL", "VIDEO_ID",
    "HORA_DESDE_PUBLICACAO", "VIEWS", "LIKES", "COMENTARIOS", "PCT_VIEWS",
]


# ===================== LEITURA DA CONFIG (Google Sheets) =====================

def ler_config():
    """Le a aba CONFIG da planilha publicada como CSV. Retorna so os ATIVOS."""
    url = (
        f"https://docs.google.com/spreadsheets/d/{SHEET_ID}"
        f"/gviz/tq?tqx=out:csv&sheet={ABA_CONFIG}"
    )
    linhas = list(csv.DictReader(io.StringIO(_get(url))))

    ativos = []
    for r in linhas:
        # normaliza chaves (tira espacos) e valores
        r = {(k or "").strip().upper(): (v or "").strip() for k, v in r.items()}
        if r.get("ATIVO", "").lower() != "sim":
            continue
        video_id = extrair_video_id(r.get("VIDEO_ID", ""))
        if not video_id:
            continue
        try:
            leads = int(float(r.get("LEADS", "0") or 0))
        except ValueError:
            leads = 0
        ativos.append({
            "lancamento": r.get("LANCAMENTO", ""),
            "tag": r.get("TAG", ""),
            "personagem": r.get("PERSONAGEM", ""),
            "cpl": r.get("CPL", ""),
            "video_id": video_id,
            "leads": leads,
        })
    return ativos


def extrair_video_id(entrada):
    """Aceita ID puro, link normal, youtu.be, /live/ ou /embed/."""
    s = (entrada or "").strip()
    if not s:
        return ""
    if not re.search(r"[/.]", s):
        return s  # ja e o ID puro
    for padrao in (
        r"[?&]v=([A-Za-z0-9_-]{11})",
        r"youtu\.be/([A-Za-z0-9_-]{11})",
        r"/live/([A-Za-z0-9_-]{11})",
        r"/embed/([A-Za-z0-9_-]{11})",
        r"([A-Za-z0-9_-]{11})",
    ):
        m = re.search(padrao, s)
        if m:
            return m.group(1)
    return ""


# ===================== YOUTUBE =====================

def buscar_estatisticas(video_id):
    """Le as estatisticas publicas do video pela YouTube Data API (chave)."""
    url = "https://www.googleapis.com/youtube/v3/videos"
    params = {"part": "statistics,snippet", "id": video_id, "key": YOUTUBE_API_KEY}
    data = json.loads(_get(url, params))
    if data.get("error"):
        print(f"Erro YouTube API ({video_id}): {data['error']}", file=sys.stderr)
        return None
    itens = data.get("items", [])
    if not itens:
        return None
    item = itens[0]
    st = item.get("statistics", {})
    pub = item.get("snippet", {}).get("publishedAt")
    return {
        "views": int(st.get("viewCount", 0) or 0),
        "likes": int(st.get("likeCount", 0) or 0),
        "comments": int(st.get("commentCount", 0) or 0),
        "publishedAt": datetime.fromisoformat(pub.replace("Z", "+00:00")) if pub else None,
    }


# ===================== HISTORICO / COMPARACAO =====================

def ler_historico():
    if not os.path.exists(ARQUIVO_SNAPSHOTS):
        return []
    with open(ARQUIVO_SNAPSHOTS, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def gravar_snapshots(novas_linhas):
    existe = os.path.exists(ARQUIVO_SNAPSHOTS)
    os.makedirs(os.path.dirname(ARQUIVO_SNAPSHOTS), exist_ok=True)
    with open(ARQUIVO_SNAPSHOTS, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if not existe:
            w.writerow(CABECALHO_SNAPSHOTS)
        w.writerows(novas_linhas)


def ler_regua():
    """
    A regua: como cada CPL de cada lancamento anterior se saiu no DIA DA
    ESTREIA (% de views sobre os leads daquele lancamento). Gerada pelo
    levantar_historico.py a partir do YouTube Analytics.
    """
    if not os.path.exists(ARQUIVO_REGUA):
        return {}
    por_cpl = {}
    with open(ARQUIVO_REGUA, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                por_cpl.setdefault(str(r["CPL"]), []).append({
                    "lancamento": r["LANCAMENTO"], "personagem": r["PERSONAGEM"],
                    "leads": int(r["LEADS"]), "pct": float(r["PCT"]) / 100,
                })
            except (ValueError, KeyError):
                continue
    return por_cpl


def ja_coletado(historico, lancamento, cpl, horas):
    return any(
        h.get("LANCAMENTO") == lancamento and str(h.get("CPL")) == str(cpl)
        and int(float(h.get("HORA_DESDE_PUBLICACAO") or -1)) == horas
        for h in historico
    )


def pct_no_fechamento(historico, lancamento, cpl, limite=24):
    """
    O % que o video tinha ao completar ~24h — o unico numero comparavel com
    a regua. Sem isso o robo compararia 77h contra 24h e diria "acima" de
    um jeito que nao quer dizer nada.
    """
    candidatos = [
        h for h in historico
        if h.get("LANCAMENTO") == lancamento and str(h.get("CPL")) == str(cpl)
        and h.get("PCT_VIEWS") not in ("", None)
        and int(float(h.get("HORA_DESDE_PUBLICACAO", 0))) <= limite
    ]
    if not candidatos:
        return None
    ultimo = max(candidatos, key=lambda h: int(float(h["HORA_DESDE_PUBLICACAO"])))
    return float(ultimo["PCT_VIEWS"]), int(float(ultimo["HORA_DESDE_PUBLICACAO"]))


def comparar_com_regua(regua, cpl, pct_atual, leads_atual, horas, pct_fechamento=None):
    """
    Compara o CPL de agora com o MESMO CPL dos outros lancamentos
    (CPL1 x CPL1, CPL2 x CPL2 — nunca CPL1 x CPL2).

    A regua e o fechamento do dia da estreia (~24h). Antes disso a
    comparacao e PARCIAL; depois, o veredito usa o % CONGELADO nas 24h
    (pct_fechamento), nunca o acumulado de agora.
    """
    linhas = regua.get(str(cpl), [])
    if not linhas or pct_atual is None:
        return None
    pct_veredito = pct_fechamento if pct_fechamento is not None else pct_atual

    pcts = [l["pct"] for l in linhas]
    media = sum(pcts) / len(pcts)
    melhor = max(linhas, key=lambda l: l["pct"])
    pior = min(linhas, key=lambda l: l["pct"])
    # o comparavel mais justo: lancamento com base de leads mais parecida
    parecido = min(linhas, key=lambda l: abs(l["leads"] - leads_atual)) if leads_atual else None

    fechou = horas >= 24 and pct_fechamento is not None
    if not fechou:
        status = "PARCIAL"
    elif pct_veredito > media * (1 + TOLERANCIA):
        status = "ACIMA"
    elif pct_veredito < media * (1 - TOLERANCIA):
        status = "ABAIXO"
    else:
        status = "IGUAL"

    return {"status": status, "media": media, "melhor": melhor, "pior": pior,
            "parecido": parecido, "qtd": len(linhas), "fechou": fechou,
            "pct_veredito": pct_veredito}


# ===================== TELEGRAM =====================

def enviar_telegram(texto):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram nao configurado.", file=sys.stderr)
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    _post(url, {"chat_id": TELEGRAM_CHAT_ID, "text": texto, "parse_mode": "Markdown"})


def nfmt(n):
    return f"{int(n):,}".replace(",", ".")


def pctfmt(x):
    return f"{x * 100:.1f}".replace(".", ",") + "%"


def montar_mensagem(itens, agora):
    data = agora.strftime("%d/%m %H:%M")
    msg = f"📊 *ACOMPANHAMENTO CPL* — {data}\n"
    for it in itens:
        pct_txt = "—" if it["pct"] is None else pctfmt(it["pct"])
        msg += (f"\n*{it['lancamento']}* · CPL{it['cpl']} ({it['personagem']})"
                f"\nhora {it['horas']}: {nfmt(it['views'])} views · *{pct_txt}* dos leads\n")

        comp = it["comp"]
        if not comp:
            msg += "⚪️ sem régua pra esse CPL ainda\n"
            continue

        if comp["fechou"]:
            seta = {"ACIMA": "🟢", "ABAIXO": "🔴", "IGUAL": "🟡"}[comp["status"]]
            dif = comp["pct_veredito"] - comp["media"]
            dif_txt = ("+" if dif >= 0 else "") + pctfmt(dif)
            msg += (f"no fim do dia 1: *{pctfmt(comp['pct_veredito'])}*\n"
                    f"{seta} *{comp['status']}* da média ({dif_txt})\n")
        else:
            faltam = 24 - it["horas"]
            msg += f"🕐 parcial — faltam {faltam}h pra fechar o dia 1\n"

        msg += f"\n_Régua do CPL{it['cpl']} (dia da estreia, {comp['qtd']} oficinas):_\n"
        p = comp["parecido"]
        if p:
            msg += f"  base parecida · {p['personagem']}: {pctfmt(p['pct'])}\n"
        msg += (f"  média: {pctfmt(comp['media'])}\n"
                f"  melhor · {comp['melhor']['personagem']}: {pctfmt(comp['melhor']['pct'])}\n"
                f"  pior · {comp['pior']['personagem']}: {pctfmt(comp['pior']['pct'])}\n")
    return msg


# ===================== PRINCIPAL =====================

def main():
    if not YOUTUBE_API_KEY:
        print("Falta a YOUTUBE_API_KEY.", file=sys.stderr)
        sys.exit(1)

    ativos = ler_config()
    if not ativos:
        print("Nenhum CPL ativo na planilha. Nada a fazer.")
        return

    regua = ler_regua()
    historico = ler_historico()
    agora = datetime.now(timezone.utc)
    agora_br = agora.astimezone(FUSO)

    novas_linhas = []
    itens_resumo = []

    for cfg in ativos:
        stats = buscar_estatisticas(cfg["video_id"])
        if not stats or not stats["publishedAt"]:
            continue
        horas = int((agora - stats["publishedAt"]).total_seconds() // 3600)
        if ja_coletado(historico, cfg["lancamento"], cfg["cpl"], horas):
            continue  # o workflow roda 2x/hora; a 2a vez nao repete coleta nem Telegram
        pct = (stats["views"] / cfg["leads"]) if cfg["leads"] > 0 else None

        linha = [
            agora_br.strftime("%Y-%m-%d %H:%M:%S"), cfg["lancamento"], cfg["tag"],
            cfg["cpl"], cfg["video_id"], horas, stats["views"], stats["likes"],
            stats["comments"], "" if pct is None else f"{pct:.6f}",
        ]
        novas_linhas.append(linha)
        # a coleta de agora conta pro fechamento (ex.: esta e a da hora 24)
        historico.append(dict(zip(CABECALHO_SNAPSHOTS, map(str, linha))))
        fech = pct_no_fechamento(historico, cfg["lancamento"], cfg["cpl"])
        comp = comparar_com_regua(regua, cfg["cpl"], pct, cfg["leads"], horas,
                                  pct_fechamento=fech[0] if fech else None)
        itens_resumo.append({
            "lancamento": cfg["lancamento"], "personagem": cfg["personagem"],
            "cpl": cfg["cpl"], "horas": horas, "views": stats["views"],
            "pct": pct, "comp": comp,
        })

    if novas_linhas:
        gravar_snapshots(novas_linhas)
    if itens_resumo:
        enviar_telegram(montar_mensagem(itens_resumo, agora_br))
        print(f"OK: {len(itens_resumo)} CPL(s) coletados e enviados ao Telegram.")


if __name__ == "__main__":
    main()
