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
# usuario-robo do Google (robo-leads-cpl@acompanhamento-cpl.iam.gserviceaccount.com), so leitura
GOOGLE_SA_JSON = os.environ.get("GOOGLE_SA_JSON", "")

# Inscritos FINAIS de lancamento com a captacao fechada: valem mais que a contagem da planilha da agencia
# (que segue mexendo depois de fechar) e que a coluna LEADS. Nuvem e robo do Mac usam a mesma tabela.
# ponytail: numero fixo por lancamento; no proximo, ou poe aqui ou preenche LEADS na CONFIG.
LEADS_FECHADOS = {"LC0526": 63271}

TOLERANCIA = 0.02  # 2% pra cima/baixo conta como "igual"
FUSO = ZoneInfo("America/Sao_Paulo")
ARQUIVO_SNAPSHOTS = os.path.join("dados", "snapshots.csv")
ARQUIVO_REGUA = os.path.join("dados", "regua.csv")

CABECALHO_SNAPSHOTS = [
    "TIMESTAMP", "LANCAMENTO", "TAG", "CPL", "VIDEO_ID",
    "HORA_DESDE_PUBLICACAO", "VIEWS", "LIKES", "COMENTARIOS", "PCT_VIEWS",
    "ESPECTADORES_UNICOS", "PCT_UNICOS",  # unicos vem do Studio pelo robo do Mac (atraso de ~1 dia)
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
        # varios links na mesma celula (virgula/espaco) = mesmo conteudo publicado 2x: soma as views
        ids = [extrair_video_id(p) for p in re.split(r"[,;\s]+", r.get("VIDEO_ID", ""))]
        video_id = "+".join(i for i in ids if i)
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
            "cpl": normalizar_cpl(r.get("CPL", "")),
            "video_id": video_id,
            "leads": leads,
            "unicos": int(float(r.get("ESPECTADORES_UNICOS", "0") or 0)),
            "planilha_leads": r.get("PLANILHA_LEADS", ""),
        })
    return ativos


def normalizar_cpl(valor):
    """'1' -> '1' (CPL1) · 'AQ2', 'A2', 'Aquecimento 2' -> 'AQ2' (aquecimento)."""
    v = (valor or "").strip().upper()
    if v.startswith("A"):
        return "AQ" + re.sub(r"\D", "", v)
    return v


def rotulo(cpl):
    """Nome pra gente ler: 'AQ2' -> 'Aquecimento 2' · '1' -> 'CPL1'."""
    return f"Aquecimento {cpl[2:]}" if cpl.startswith("AQ") else f"CPL{cpl}"


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


def base_de_leads(cfg, contagem):
    """Captacao fechada -> numero fixo · senao a contagem da planilha da agencia · senao a coluna LEADS."""
    return LEADS_FECHADOS.get(cfg["lancamento"]) or contagem or cfg["leads"]


def contar_leads_unicos(link):
    """
    Leads unicos = linhas preenchidas da aba LEADS UNIQUE da planilha de leads do lancamento
    (1 e-mail por linha; e o mesmo numero do dashboard da agencia). Precisa do usuario-robo
    com acesso de leitura a planilha. Devolve None se nao der (ai vale a coluna LEADS).
    """
    m = re.search(r"/d/([A-Za-z0-9_-]+)", link or "")
    if not (m and GOOGLE_SA_JSON):
        return None
    from google.auth.transport.requests import Request
    from google.oauth2 import service_account
    cred = service_account.Credentials.from_service_account_info(
        json.loads(GOOGLE_SA_JSON), scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"])
    cred.refresh(Request())
    url = (f"https://sheets.googleapis.com/v4/spreadsheets/{m.group(1)}/values/"
           + urllib.parse.quote("'LEADS UNIQUE'!A2:A"))
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {cred.token}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            linhas = json.load(r).get("values", [])
    except urllib.error.HTTPError as e:
        print(f"Leads da planilha recusados ({e.code}): compartilhou com o usuario-robo?", file=sys.stderr)
        return None
    return sum(1 for l in linhas if l and l[0].strip())


# ===================== YOUTUBE =====================

def buscar_estatisticas(video_id):
    """
    Le as estatisticas publicas pela YouTube Data API (chave).
    'id1+id2' = mesmo conteudo em 2 videos: soma views/likes/comentarios e conta
    as horas a partir do primeiro publicado.
    """
    url = "https://www.googleapis.com/youtube/v3/videos"
    ids = video_id.split("+")
    params = {"part": "statistics,snippet", "id": ",".join(ids), "key": YOUTUBE_API_KEY}
    data = json.loads(_get(url, params))
    if data.get("error"):
        print(f"Erro YouTube API ({video_id}): {data['error']}", file=sys.stderr)
        return None
    itens = data.get("items", [])
    if not itens:
        return None
    if len(itens) < len(ids):
        print(f"Aviso: so {len(itens)} de {len(ids)} videos encontrados em {video_id}", file=sys.stderr)
    soma = lambda campo: sum(int(i.get("statistics", {}).get(campo, 0) or 0) for i in itens)
    pubs = [datetime.fromisoformat(p.replace("Z", "+00:00"))
            for p in (i.get("snippet", {}).get("publishedAt") for i in itens) if p]
    return {
        "views": soma("viewCount"),
        "likes": soma("likeCount"),
        "comments": soma("commentCount"),
        "publishedAt": min(pubs) if pubs else None,
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
    if existe:  # cabecalho ganhou colunas: reescreve so a 1a linha (linhas antigas ficam mais curtas)
        with open(ARQUIVO_SNAPSHOTS, encoding="utf-8") as f:
            linhas = f.read().split("\n", 1)
        if linhas[0].strip() != ",".join(CABECALHO_SNAPSHOTS):
            with open(ARQUIVO_SNAPSHOTS, "w", encoding="utf-8") as f:
                f.write(",".join(CABECALHO_SNAPSHOTS) + "\n" + (linhas[1] if len(linhas) > 1 else ""))
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


# ===================== CURVA HORA A HORA (Studio) =====================
# dados/curvas_por_hora.csv: uma linha por video por hora, com as views do YouTube Studio
# (os dois lados da comparacao vem do Studio; o contador publico atrasa ate ~29% no dia 1).
# Os antigos foram puxados 1 vez; o robo do Mac atualiza so o lancamento atual de hora em hora.

ARQUIVO_CURVAS = os.path.join("dados", "curvas_por_hora.csv")
CABECALHO_CURVAS = ["LANCAMENTO", "PERSONAGEM", "CPL", "VIDEO_ID", "HORA", "VIEWS_ACUMULADAS", "LEADS", "PCT"]
HORAS_CURVA = 72


def linhas_da_curva(lancamento, personagem, cpl, video_id, views_por_hora, leads, ate=HORAS_CURVA):
    """
    views_por_hora = o que o Studio devolve (views DENTRO de cada hora, a partir da hora 0).
    HORA no CSV = horas completas desde a publicacao: HORA 24 = soma das primeiras 24 horas.
    PCT = acumulado / leads do PROPRIO lancamento (fracao, igual ao PCT_VIEWS dos snapshots).
    Zeros no fim = horas que o Studio ainda nao fechou: saem (nunca gravar zero de mentira).
    """
    v = list(views_por_hora[:ate])
    while v and v[-1] == 0:
        v.pop()
    linhas, total = [], 0
    for i, x in enumerate(v):
        total += x
        linhas.append([lancamento, personagem, cpl, video_id, i + 1, total, leads,
                       f"{total / leads:.6f}" if leads else ""])
    return linhas


def juntar_curvas(antigas, novas):
    """
    Troca no CSV so os videos (LANCAMENTO + CPL) que vieram com dado agora; o resto fica como estava.
    Video que falhou nao entra em 'novas' -> a curva dele no CSV continua intacta.
    """
    trocar = {(str(l[0]), str(l[2])) for l in novas}
    ficam = [l for l in antigas if (str(l[0]), str(l[2])) not in trocar]
    return sorted(ficam + novas, key=lambda l: (str(l[0]), str(l[2]), int(l[4])))


def ler_curvas(arquivo=ARQUIVO_CURVAS):
    if not os.path.exists(arquivo):
        return []
    with open(arquivo, newline="", encoding="utf-8") as f:
        return [[r[c] for c in CABECALHO_CURVAS] for r in csv.DictReader(f)]


def gravar_curvas(linhas, arquivo=ARQUIVO_CURVAS):
    with open(arquivo, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(CABECALHO_CURVAS)
        w.writerows(linhas)


def comparar_por_hora(curvas, lancamento, cpl):
    """
    CPLn do lancamento atual x CPLn de cada oficina antiga NA MESMA HORA (a ultima com dado do atual).
    Devolve None se o atual ainda nao tem curva ou nao ha antigo com dado nessa hora.
    """
    cpl = str(cpl)
    meu = [l for l in curvas if l[0] == lancamento and str(l[2]) == cpl and l[7] != ""]
    if not meu:
        return None
    ult = max(meu, key=lambda l: int(l[4]))
    hora, pct_atual, leads_atual = int(ult[4]), float(ult[7]), int(ult[6] or 0)
    outros = [{"lancamento": l[0], "personagem": l[1], "leads": int(l[6]), "pct": float(l[7])}
              for l in curvas
              if l[0] != lancamento and str(l[2]) == cpl and int(l[4]) == hora and l[7] != ""]
    if not outros:
        return None
    media = sum(o["pct"] for o in outros) / len(outros)
    return {"hora": hora, "pct": pct_atual, "media": media, "qtd": len(outros),
            "melhor": max(outros, key=lambda o: o["pct"]), "pior": min(outros, key=lambda o: o["pct"]),
            "parecido": min(outros, key=lambda o: abs(o["leads"] - leads_atual))}


def linha_hora_a_hora(c):
    """Linha do Telegram. Sem bolinha verde/vermelha antes das 24h: o dia 1 ainda nao fechou."""
    txt = (f"⏱ Studio (fechado até a hora {c['hora']}): *{pctfmt(c['pct'])}* · média {pctfmt(c['media'])} "
           f"({c['qtd']} oficinas) · base parecida {c['parecido']['personagem']} {pctfmt(c['parecido']['pct'])}")
    if c["hora"] >= 24:
        if c["pct"] > c["media"] * (1 + TOLERANCIA):
            txt = "🟢 " + txt
        elif c["pct"] < c["media"] * (1 - TOLERANCIA):
            txt = "🔴 " + txt
        else:
            txt = "🟡 " + txt
    return txt + "\n"


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
        msg += (f"\n*{it['lancamento']}* · {rotulo(it['cpl'])} ({it['personagem']})"
                f"\nhora {it['horas']}: {nfmt(it['views'])} views · *{pct_txt}* dos leads\n")
        if it.get("unicos") and it.get("leads"):
            msg += (f"espectadores únicos (Studio, até ontem): {nfmt(it['unicos'])} · "
                    f"*{pctfmt(it['unicos'] / it['leads'])}* dos leads\n")
        if it.get("hora_a_hora"):
            msg += linha_hora_a_hora(it["hora_a_hora"])

        comp = it["comp"]
        if not comp:
            msg += "⚪️ sem régua pra esse vídeo ainda\n"
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

        msg += f"\n_Régua do {rotulo(it['cpl'])} (dia da estreia, {comp['qtd']} oficinas):_\n"
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

    # leads da planilha da agencia (1 leitura por planilha); sem acesso, vale a coluna LEADS da CONFIG
    contagens = {}
    for cfg in ativos:
        link = cfg["planilha_leads"]
        if cfg["lancamento"] not in LEADS_FECHADOS and link and link not in contagens:
            contagens[link] = contar_leads_unicos(link)
        cfg["leads"] = base_de_leads(cfg, contagens.get(link))
    print(f"Leads unicos da planilha: {contagens or 'coluna PLANILHA_LEADS vazia'}")

    regua = ler_regua()
    curvas = ler_curvas()
    historico = ler_historico()
    agora = datetime.now(timezone.utc)
    agora_br = agora.astimezone(FUSO)

    novas_linhas = []
    itens_resumo = []

    for cfg in ativos:
        stats = buscar_estatisticas(cfg["video_id"])
        if not stats or not stats["publishedAt"]:
            print(f"Video {cfg['video_id']} ({cfg['lancamento']} {rotulo(cfg['cpl'])}) nao encontrado: "
                  "privado ou apagado? Desmarque ATIVO na planilha.", file=sys.stderr)
            continue
        horas = int((agora - stats["publishedAt"]).total_seconds() // 3600)
        if ja_coletado(historico, cfg["lancamento"], cfg["cpl"], horas):
            continue  # o workflow roda 2x/hora; a 2a vez nao repete coleta nem Telegram
        pct = (stats["views"] / cfg["leads"]) if cfg["leads"] > 0 else None

        linha = [
            agora_br.strftime("%Y-%m-%d %H:%M:%S"), cfg["lancamento"], cfg["tag"],
            cfg["cpl"], cfg["video_id"], horas, stats["views"], stats["likes"],
            stats["comments"], "" if pct is None else f"{pct:.6f}",
            cfg["unicos"] or "", f"{cfg['unicos'] / cfg['leads']:.6f}" if cfg["unicos"] and cfg["leads"] else "",
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
            "pct": pct, "comp": comp, "unicos": cfg["unicos"], "leads": cfg["leads"],
            "hora_a_hora": comparar_por_hora(curvas, cfg["lancamento"], cfg["cpl"]),
        })

    if novas_linhas:
        gravar_snapshots(novas_linhas)
    if itens_resumo:
        enviar_telegram(montar_mensagem(itens_resumo, agora_br))
        print(f"OK: {len(itens_resumo)} video(s) coletados e enviados ao Telegram.")


if __name__ == "__main__":
    main()
