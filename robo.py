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
import os
import re
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests

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
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    linhas = list(csv.DictReader(io.StringIO(resp.text)))

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
    resp = requests.get(url, params=params, timeout=30)
    data = resp.json()
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


def comparar_historico(historico, cpl, horas, lancamento_atual, pct_atual):
    """Compara o % de views com OUTROS lancamentos no mesmo CPL e mesma hora."""
    if pct_atual is None:
        return None
    pares = []
    for h in historico:
        try:
            if (str(h["CPL"]) == str(cpl)
                    and int(float(h["HORA_DESDE_PUBLICACAO"])) == int(horas)
                    and h["LANCAMENTO"] != lancamento_atual
                    and h["PCT_VIEWS"] not in ("", None)
                    and float(h["PCT_VIEWS"]) > 0):
                pares.append((h["LANCAMENTO"], float(h["PCT_VIEWS"])))
        except (ValueError, KeyError):
            continue
    if not pares:
        return None
    media = sum(p for _, p in pares) / len(pares)
    melhor_lanc, melhor = max(pares, key=lambda x: x[1])
    if pct_atual > media * (1 + TOLERANCIA):
        status = "ACIMA"
    elif pct_atual < media * (1 - TOLERANCIA):
        status = "ABAIXO"
    else:
        status = "IGUAL"
    return {"status": status, "media": media, "melhor": melhor,
            "melhor_lanc": melhor_lanc, "qtd": len(pares)}


# ===================== TELEGRAM =====================

def enviar_telegram(texto):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram nao configurado.", file=sys.stderr)
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, data={
        "chat_id": TELEGRAM_CHAT_ID, "text": texto, "parse_mode": "Markdown",
    }, timeout=30)


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
                f"\nhora {it['horas']}: {nfmt(it['views'])} views ({pct_txt} dos leads)")
        comp = it["comp"]
        if comp:
            seta = {"ACIMA": "🟢", "ABAIXO": "🔴", "IGUAL": "🟡"}[comp["status"]]
            dif = it["pct"] - comp["media"]
            dif_txt = ("+" if dif >= 0 else "") + pctfmt(dif)
            msg += (f"\n{seta} {comp['status']} vs média ({dif_txt})"
                    f" · melhor: {comp['melhor_lanc']} ({pctfmt(comp['melhor'])})")
        else:
            msg += "\n⚪️ sem base de comparação ainda"
        msg += "\n"
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
        pct = (stats["views"] / cfg["leads"]) if cfg["leads"] > 0 else None

        novas_linhas.append([
            agora_br.strftime("%Y-%m-%d %H:%M:%S"), cfg["lancamento"], cfg["tag"],
            cfg["cpl"], cfg["video_id"], horas, stats["views"], stats["likes"],
            stats["comments"], "" if pct is None else f"{pct:.6f}",
        ])
        comp = comparar_historico(historico, cfg["cpl"], horas, cfg["lancamento"], pct)
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
