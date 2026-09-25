#!/usr/bin/env python3
"""Monitor diário de prazos de entrega — Mercado Livre e Shopee.

Comandos:
  python prazos.py configurar-ml            conecta sua conta do Mercado Livre (uma vez)
  python prazos.py validar-ceps             confere os 54 CEPs no ViaCEP e completa as cidades
  python prazos.py entrar-shopee            abre o navegador da ferramenta para você logar na Shopee (uma vez)
  python prazos.py calibrar-shopee <link>   ensina a ferramenta onde a Shopee mostra o prazo (uma vez)
  python prazos.py coletar [--so ml|shopee] [--limite N]   faz a coleta do dia e atualiza o painel
  python prazos.py painel                   só regera o painel.html
  python prazos.py exportar                 gera historico.csv com todas as consultas
  python prazos.py agendar [--hora 07:00]   agenda a coleta diária neste computador
  python prazos.py alertas [--teste]        avisa por e-mail se algum prazo de hoje saiu do normal
  python prazos.py email-semanal [--teste]  envia o resumo da semana anterior (segunda-feira)

Com --teste, os e-mails são salvos como arquivo .html em vez de enviados.
No GitHub, os caminhos e segredos vêm de variáveis de ambiente (ver .github/workflows).
"""
import argparse
import csv
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import traceback
import webbrowser
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

import db  # noqa: E402
import painel  # noqa: E402

DADOS = Path(os.environ.get("PRAZOS_DADOS", BASE))      # no GitHub: pasta dados/
DADOS.mkdir(parents=True, exist_ok=True)
CONFIG = BASE / "config.json"
CEPS = BASE / "ceps.csv"
BANCO = DADOS / "historico.sqlite"
PAINEL = Path(os.environ.get("PRAZOS_PAINEL", BASE / "painel.html"))
TOKENS_ML = DADOS / "tokens_ml.json"
LOG = DADOS / "coleta.log"


def carregar_config():
    if not CONFIG.exists():
        shutil.copy(BASE / "config.example.json", CONFIG)
        print("Criei o config.json a partir do exemplo — preencha os dados e rode de novo.")
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    # segredos vindos do ambiente (GitHub) têm prioridade sobre o arquivo
    for chave, env in (("client_id", "ML_CLIENT_ID"), ("client_secret", "ML_CLIENT_SECRET")):
        if os.environ.get(env):
            cfg["ml"][chave] = os.environ[env].strip()
    return cfg


def carregar_ceps():
    rows = list(csv.DictReader(CEPS.open(encoding="utf-8")))
    for r in rows:
        r["cep"] = re.sub(r"\D", "", r["cep"])
    return rows


def log(msg):
    linha = f"[{dt.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(linha, flush=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(linha + "\n")


def _detalhe(e):
    """Traceback só para erros inesperados (não para avisos de configuração)."""
    return "" if isinstance(e, SystemExit) else "\n" + traceback.format_exc()


# ------------------------------------------------------------ comandos
def cmd_configurar_ml(_):
    import ml
    ml.autorizar(carregar_config()["ml"], TOKENS_ML)


def cmd_validar_ceps(_):
    import requests
    rows, problemas = carregar_ceps(), []
    for r in rows:
        try:
            j = requests.get(f"https://viacep.com.br/ws/{r['cep']}/json/", timeout=15).json()
        except Exception as e:  # noqa: BLE001
            problemas.append(f"{r['uf']} {r['regiao']} {r['cep']}: falha na consulta ({e})")
            continue
        if j.get("erro"):
            problemas.append(f"{r['uf']} {r['regiao']} {r['cep']}: CEP não existe — troque no ceps.csv")
        elif j["uf"] != r["uf"]:
            problemas.append(f"{r['uf']} {r['regiao']} {r['cep']}: CEP é de {j['localidade']}/{j['uf']}")
        else:
            r["cidade"] = j["localidade"]
        print(f"  {r['uf']} {r['regiao']:<8} {r['cep']}  {j.get('localidade', '?')}")
    with CEPS.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["uf", "regiao", "cidade", "cep"])
        w.writeheader()
        w.writerows({**r, "cep": f"{r['cep'][:5]}-{r['cep'][5:]}"} for r in rows)
    print("\nTudo certo com os 54 CEPs." if not problemas else "\nAjuste estes CEPs:\n  " + "\n  ".join(problemas))


def cmd_entrar_shopee(_):
    import shopee
    shopee.abrir_para_login(carregar_config())


def cmd_calibrar_shopee(args):
    import shopee
    shopee.calibrar(carregar_config(), args.link)


def cmd_coletar(args):
    cfg, ceps, hoje = carregar_config(), carregar_ceps(), dt.date.today()
    conn = db.conectar(BANCO)
    salvar = lambda linhas: db.inserir(conn, linhas)  # noqa: E731
    log(f"Início da coleta ({len(ceps)} CEPs)")
    falhou = False
    if args.so in (None, "ml") and cfg.get("ml", {}).get("ativo", True):
        try:
            import ml
            ml.coletar(cfg, TOKENS_ML, ceps, salvar, log, hoje, args.limite)
        except BaseException as e:  # noqa: BLE001
            falhou = True
            log(f"ML: ERRO — {e}" + _detalhe(e))
        if cfg["ml"].get("entregas_reais", True):
            try:
                import ml
                ml.coletar_entregas(cfg, TOKENS_ML, ceps, lambda l: db.gravar_entregas(conn, l),
                                    db.envios_ja_gravados(conn), log, hoje)
            except BaseException as e:  # noqa: BLE001
                falhou = True
                log(f"ML entregas: ERRO — {e}" + _detalhe(e))
    if args.so in (None, "shopee") and cfg.get("shopee", {}).get("ativo", True):
        try:
            import shopee
            shopee.coletar(cfg, ceps, salvar, log, hoje, args.limite)
        except BaseException as e:  # noqa: BLE001
            falhou = True
            log(f"Shopee: ERRO — {e}" + _detalhe(e))
    painel.gerar(conn, PAINEL, cfg.get("painel_dias", 180), publico=cfg.get("painel_publico", False))
    log(f"Painel atualizado: {PAINEL}")
    if args.abrir:
        webbrowser.open(PAINEL.as_uri())
    sys.exit(1 if falhou else 0)


def cmd_painel(_):
    cfg = carregar_config()
    painel.gerar(db.conectar(BANCO), PAINEL, cfg.get("painel_dias", 180), publico=cfg.get("painel_publico", False))
    webbrowser.open(PAINEL.as_uri())
    print(f"Painel: {PAINEL}")


def cmd_exportar(_):
    conn = db.conectar(BANCO)
    destino = BASE / "historico.csv"
    cols = [c for c in db.COLUNAS if c != "bruto"]
    with destino.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(cols)
        w.writerows(conn.execute(f"SELECT {','.join(cols)} FROM consultas ORDER BY data, marketplace"))
    destino2 = BASE / "entregas.csv"
    with destino2.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(db.COLUNAS_ENTREGAS)
        w.writerows(conn.execute(f"SELECT {','.join(db.COLUNAS_ENTREGAS)} FROM entregas ORDER BY data_entregue"))
    print(f"Exportado: {destino} e {destino2}")


def cmd_alertas(args):
    import relatorio
    cfg = carregar_config()
    relatorio.alertas(db.conectar(BANCO), cfg, dt.date.today(), log, teste=args.teste, destino=DADOS)


def cmd_email_semanal(args):
    import relatorio
    import metricas
    cfg = carregar_config()
    conn = db.conectar(BANCO)
    hoje = dt.date.today()
    if cfg["ml"].get("entregas_no_email", False) and not cfg["ml"].get("entregas_reais", True):
        # entregas reais só para o e-mail: buscadas na hora, numa cópia em memória do histórico
        import sqlite3
        import ml
        mem = sqlite3.connect(":memory:")
        conn.backup(mem)
        fech = metricas.meses_fechados(conn, hoje)
        d1 = min([a for a, _ in fech] + [metricas.semana_passada(hoje)[0] - dt.timedelta(days=7)])
        try:
            db.gravar_entregas(mem, ml.entregas_sob_demanda(cfg, TOKENS_ML, carregar_ceps(), d1, hoje, log))
        except BaseException as e:  # noqa: BLE001
            log(f"Entregas reais indisponíveis para o e-mail: {e}" + _detalhe(e))
        conn = mem
    if args.aguardar_ate:
        relatorio.aguardar_ate(args.aguardar_ate)
    relatorio.semanal(conn, cfg, hoje, log, teste=args.teste, destino=DADOS)


def cmd_conectar_ml(args):
    import ml
    cfg = carregar_config()
    if not args.codigo:
        print("Abra este link, autorize com a conta da loja e copie o código TG-... do endereço:\n")
        print(ml.link_autorizacao(cfg["ml"]))
        return
    ml.trocar_codigo(cfg["ml"], TOKENS_ML, args.codigo)


def cmd_agendar(args):
    h, m = (int(x) for x in args.hora.split(":"))
    py, script = sys.executable, str(BASE / "prazos.py")
    if os.name == "nt":
        subprocess.run(["schtasks", "/Create", "/F", "/SC", "DAILY", "/TN", "PrazosEntrega",
                        "/ST", f"{h:02d}:{m:02d}", "/TR", f'"{py}" "{script}" coletar'], check=True)
        print(f"Agendado no Agendador de Tarefas do Windows: todo dia às {h:02d}:{m:02d}.")
    else:
        linha = f'{m} {h} * * * cd "{BASE}" && "{py}" "{script}" coletar >> "{BASE / "cron.log"}" 2>&1'
        atual = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
        novas = [l for l in atual.splitlines() if "prazos.py" not in l] + [linha]
        subprocess.run(["crontab", "-"], input="\n".join(novas) + "\n", text=True, check=True)
        print(f"Agendado no cron: todo dia às {h:02d}:{m:02d}.")
    print("O computador precisa estar ligado (e com sua sessão aberta) nesse horário.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("configurar-ml").set_defaults(fn=cmd_configurar_ml)
    sub.add_parser("validar-ceps").set_defaults(fn=cmd_validar_ceps)
    sub.add_parser("entrar-shopee").set_defaults(fn=cmd_entrar_shopee)
    p = sub.add_parser("calibrar-shopee")
    p.add_argument("link", help="link de um anúncio seu na Shopee")
    p.set_defaults(fn=cmd_calibrar_shopee)
    p = sub.add_parser("coletar")
    p.add_argument("--so", choices=["ml", "shopee"])
    p.add_argument("--limite", type=int, help="só os N primeiros anúncios (bom para testar)")
    p.add_argument("--abrir", action="store_true", help="abre o painel no fim")
    p.set_defaults(fn=cmd_coletar)
    sub.add_parser("painel").set_defaults(fn=cmd_painel)
    sub.add_parser("exportar").set_defaults(fn=cmd_exportar)
    p = sub.add_parser("conectar-ml")
    p.add_argument("--codigo", help="código TG-... (ou o endereço da página onde ele aparece)")
    p.set_defaults(fn=cmd_conectar_ml)
    p = sub.add_parser("alertas")
    p.add_argument("--teste", action="store_true")
    p.set_defaults(fn=cmd_alertas)
    p = sub.add_parser("email-semanal")
    p.add_argument("--teste", action="store_true")
    p.add_argument("--aguardar-ate", help="HH:MM (horário de Brasília): espera até esse horário antes de enviar")
    p.set_defaults(fn=cmd_email_semanal)
    p = sub.add_parser("agendar")
    p.add_argument("--hora", default="07:00")
    p.set_defaults(fn=cmd_agendar)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
