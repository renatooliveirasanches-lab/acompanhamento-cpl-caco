"""
AUTORIZAR O ROBO A LER O YOUTUBE ANALYTICS (roda UMA vez, aqui no Mac)

Abre o navegador, o Renato aprova o acesso de LEITURA ao canal, e o script
imprime um "refresh token" — a chave permanente que o robo vai usar la no
GitHub pra puxar os numeros do Studio sem ninguem precisar logar de novo.

Uso:
    python3 autorizar_youtube.py
"""

import http.server
import json
import os
import secrets
import socketserver
import threading
import urllib.parse
import urllib.request
import webbrowser

CLIENT_SECRET = os.path.expanduser("~/.gws/personal/client_secret.json")
ESCOPO = "https://www.googleapis.com/auth/yt-analytics.readonly"
PORTA = 8765

codigo_recebido = {}
respondeu = threading.Event()


class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        codigo_recebido.update({k: v[0] for k, v in q.items()})
        respondeu.set()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        ok = "code" in codigo_recebido
        self.wfile.write(
            ("<h2>Pronto! Pode fechar esta aba e voltar pro Claude.</h2>"
             if ok else "<h2>Falhou. Volte pro Claude.</h2>").encode()
        )

    def log_message(self, *args):
        pass  # silencia o log do servidor


def main():
    with open(CLIENT_SECRET) as f:
        cfg = json.load(f)["installed"]
    client_id, client_secret = cfg["client_id"], cfg["client_secret"]
    redirect = f"http://localhost:{PORTA}"
    estado = secrets.token_urlsafe(16)

    servidor = socketserver.TCPServer(("", PORTA), Handler)
    threading.Thread(target=servidor.serve_forever, daemon=True).start()

    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": redirect,
        "response_type": "code",
        "scope": ESCOPO,
        "access_type": "offline",
        "prompt": "consent",
        "state": estado,
    })
    print("Abrindo o navegador para aprovar o acesso...")
    print(url)
    webbrowser.open(url)

    if not respondeu.wait(timeout=300):
        raise SystemExit("Ninguem aprovou em 5 minutos. Rode de novo.")
    servidor.shutdown()

    if "error" in codigo_recebido:
        raise SystemExit(f"Recusado: {codigo_recebido['error']}")
    if codigo_recebido.get("state") != estado:
        raise SystemExit("Estado nao confere — tente de novo.")

    corpo = urllib.parse.urlencode({
        "code": codigo_recebido["code"],
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect,
        "grant_type": "authorization_code",
    }).encode()
    with urllib.request.urlopen("https://oauth2.googleapis.com/token", corpo, timeout=30) as resp:
        r = json.load(resp)

    if "refresh_token" not in r:
        raise SystemExit(f"Nao veio refresh_token: {r}")

    destino = os.path.join(os.path.dirname(os.path.abspath(__file__)), "token_youtube.json")
    with open(destino, "w") as f:
        json.dump({
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": r["refresh_token"],
        }, f, indent=2)
    os.chmod(destino, 0o600)
    print(f"\nOK! Credencial salva em {destino}")
    print("Esse arquivo NAO vai pro GitHub (esta no .gitignore).")


if __name__ == "__main__":
    main()
