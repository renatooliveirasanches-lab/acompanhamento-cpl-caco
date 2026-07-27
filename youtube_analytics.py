"""
Le o YouTube Analytics (os mesmos numeros que aparecem no Studio).

Duas funcoes que o robo usa:
  - serie_diaria(video_id): views por dia desde a publicacao
  - views_primeiras_24h(video_id): o numero que a planilha do Renato usa

Credencial: token_youtube.json (no Mac) ou as variaveis de ambiente
YT_CLIENT_ID / YT_CLIENT_SECRET / YT_REFRESH_TOKEN (no GitHub).
"""

import json
import os
import urllib.parse
import urllib.request

_ARQ_TOKEN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "token_youtube.json")
_cache_access_token = {}


def _credenciais():
    if os.environ.get("YT_REFRESH_TOKEN"):
        return (os.environ["YT_CLIENT_ID"], os.environ["YT_CLIENT_SECRET"],
                os.environ["YT_REFRESH_TOKEN"])
    with open(_ARQ_TOKEN) as f:
        d = json.load(f)
    return d["client_id"], d["client_secret"], d["refresh_token"]


def _access_token():
    if "t" in _cache_access_token:
        return _cache_access_token["t"]
    client_id, client_secret, refresh = _credenciais()
    corpo = urllib.parse.urlencode({
        "client_id": client_id, "client_secret": client_secret,
        "refresh_token": refresh, "grant_type": "refresh_token",
    }).encode()
    with urllib.request.urlopen("https://oauth2.googleapis.com/token", corpo, timeout=30) as r:
        _cache_access_token["t"] = json.load(r)["access_token"]
    return _cache_access_token["t"]


def _consultar(**params):
    params.setdefault("ids", "channel==MINE")
    url = "https://youtubeanalytics.googleapis.com/v2/reports?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {_access_token()}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"YouTube Analytics recusou: {e.read().decode()[:400]}") from e


def serie_diaria(video_id, desde="2015-01-01", ate="2035-12-31"):
    """
    [(data, views), ...] a partir do primeiro dia que teve view — ou seja,
    do dia da publicacao em diante. A API devolve o intervalo inteiro
    (inclusive os zeros de antes do video existir), entao cortamos a frente.
    """
    d = _consultar(startDate=desde, endDate=ate, metrics="views",
                   dimensions="day", filters=f"video=={video_id}", sort="day")
    linhas = [(l[0], int(l[1])) for l in d.get("rows", [])]
    for i, (_, v) in enumerate(linhas):
        if v > 0:
            return linhas[i:]
    return []


def views_primeiras_24h(video_id, serie=None):
    """
    O numero da planilha: views nas ~primeiras 24h.

    ponytail: a API so entrega por DIA-calendario, nao por hora. Um video
    publicado 20h tem poucas horas no dia 1, entao somamos o dia da
    publicacao + o dia seguinte. Fica entre 24h e 48h de janela — e a melhor
    aproximacao possivel via API. Upgrade: se o YouTube expuser dimensao
    horaria, trocar por uma janela exata de 24h.
    """
    serie = serie if serie is not None else serie_diaria(video_id)
    return sum(v for _, v in serie[:2])


def data_publicacao(video_id, serie=None):
    serie = serie if serie is not None else serie_diaria(video_id)
    return serie[0][0] if serie else None
