"""Coletor do Mercado Livre: usa a API oficial (não precisa simular compra no site)."""
import datetime as dt
import hashlib
import json
import math
import os
import random
import threading
import time
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import parse_qs, quote, urlparse

import requests

API = "https://api.mercadolibre.com"
AUTH = "https://auth.mercadolivre.com.br/authorization"


# ---------------------------------------------------------------- autorização
def link_autorizacao(cfg_ml):
    return (f"{AUTH}?response_type=code&client_id={cfg_ml['client_id']}"
            f"&redirect_uri={quote(cfg_ml['redirect_uri'], safe='')}")


def trocar_codigo(cfg_ml, token_path, colado):
    """Troca o código TG-... (ou o endereço inteiro onde ele aparece) pelo acesso permanente."""
    colado = colado.strip()
    code = parse_qs(urlparse(colado).query).get("code", [colado])[0]
    r = requests.post(f"{API}/oauth/token", data={
        "grant_type": "authorization_code", "client_id": cfg_ml["client_id"],
        "client_secret": cfg_ml["client_secret"], "code": code, "redirect_uri": cfg_ml["redirect_uri"],
    }, headers={"accept": "application/json"}, timeout=30)
    if r.status_code != 200:
        raise SystemExit(f"Não deu certo ({r.status_code}): {r.text[:300]}\n"
                         "O código vale só por alguns minutos e uma única vez — gere outro e tente de novo.")
    cli = ClienteML(cfg_ml, token_path, tokens=r.json())
    cli._salvar(r.json())
    me = cli.get("/users/me")
    print(f"Pronto! Mercado Livre conectado: {me.get('nickname')} (id {me.get('id')}).")
    return cli


def autorizar(cfg_ml, token_path):
    """Versão para rodar no computador (no GitHub usa-se o workflow 'Conectar Mercado Livre')."""
    if not cfg_ml["client_id"]:
        cfg_ml["client_id"] = input("Client ID do app do Mercado Livre: ").strip()
    if not cfg_ml["client_secret"]:
        cfg_ml["client_secret"] = input("Client Secret do app do Mercado Livre: ").strip()
    url = link_autorizacao(cfg_ml)
    print("\nAutorize com a conta da loja. Depois você cai numa página com '?code=TG-...' no endereço.")
    print(f"\nSe o navegador não abrir, acesse:\n{url}\n")
    webbrowser.open(url)
    trocar_codigo(cfg_ml, token_path, input("Cole aqui o endereço completo dessa página: "))


def entregas_sob_demanda(cfg, token_path, ceps, d1, d2, log):
    """Busca as entregas reais do período direto na API, sem gravar no histórico
    (assim nenhum dado de venda fica guardado no repositório)."""
    linhas = []
    dias = (dt.date.today() - d1).days + 30
    coletar_entregas(cfg, token_path, ceps, linhas.extend, set(), log, dt.date.today(), dias=dias)
    return [l for l in linhas if d1.isoformat() <= l["data_entregue"] <= d2.isoformat()]


# ------------------------------------------------------------ guarda dos tokens
# Localmente: tokens_ml.json. No GitHub: arquivo criptografado (tokens_ml.enc) no
# repositório, com a chave no segredo ML_TOKEN_KEY — assim o token renovado a cada
# execução fica salvo sem aparecer para quem vê o repositório.
def _fernet():
    chave = os.environ.get("ML_TOKEN_KEY")
    if not chave:
        return None
    import base64
    from cryptography.fernet import Fernet
    k = base64.urlsafe_b64encode(hashlib.sha256(chave.encode()).digest())
    return Fernet(k)


def carregar_tokens(path):
    f = _fernet()
    enc = path.with_suffix(".enc")
    if f and enc.exists():
        return json.loads(f.decrypt(enc.read_bytes()))
    if not f and path.exists():
        return json.loads(path.read_text())
    if os.environ.get("ML_REFRESH_TOKEN"):  # primeira execução no GitHub
        return {"refresh_token": os.environ["ML_REFRESH_TOKEN"].strip(), "access_token": "", "expira_em": 0}
    return None


def salvar_tokens(path, j):
    f = _fernet()
    if f:
        path.with_suffix(".enc").write_bytes(f.encrypt(json.dumps(j).encode()))
    else:
        path.write_text(json.dumps(j, indent=2))


def _hash(valor):
    """Identificador irreversível (o histórico pode ficar num repositório público)."""
    return hashlib.sha256(f"prazos:{valor}".encode()).hexdigest()[:16]


class ClienteML:
    def __init__(self, cfg_ml, token_path, tokens=None):
        self.cfg = cfg_ml
        self.path = token_path
        if tokens is None:
            tokens = carregar_tokens(token_path)
            if tokens is None:
                raise SystemExit("Mercado Livre não conectado. Rode: python prazos.py configurar-ml "
                                 "(ou, no GitHub, rode o workflow 'Conectar Mercado Livre')")
        self.tok = tokens
        self.lock = threading.Lock()
        self.s = requests.Session()

    def _salvar(self, j):
        j = dict(j)
        j["expira_em"] = time.time() + int(j.get("expires_in", 21600)) - 300
        self.tok = j
        salvar_tokens(self.path, j)

    def _renovar(self):
        # O refresh_token do ML só vale uma vez: sempre salvar o novo.
        r = requests.post(f"{API}/oauth/token", data={
            "grant_type": "refresh_token", "client_id": self.cfg["client_id"],
            "client_secret": self.cfg["client_secret"], "refresh_token": self.tok["refresh_token"],
        }, headers={"accept": "application/json"}, timeout=30)
        if r.status_code != 200:
            raise RuntimeError(f"Não consegui renovar o acesso ao ML ({r.status_code}). "
                               f"Rode de novo: python prazos.py configurar-ml. Detalhe: {r.text[:200]}")
        self._salvar(r.json())

    def get(self, caminho, params=None, headers=None):
        with self.lock:
            if time.time() > self.tok.get("expira_em", 0):
                self._renovar()
        ultimo = None
        for tentativa in range(5):
            usado = self.tok["access_token"]
            r = self.s.get(API + caminho, params=params, timeout=30,
                           headers={"Authorization": f"Bearer {usado}", **(headers or {})})
            ultimo = r
            if r.status_code == 401:
                with self.lock:
                    if self.tok["access_token"] == usado:
                        self._renovar()
                continue
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(2 ** tentativa + random.random())
                continue
            r.raise_for_status()
            return r.json()
        ultimo.raise_for_status()
        return ultimo.json()

    # ------------------------------------------------------------ anúncios
    def anuncios_ativos(self):
        uid = self.get("/users/me")["id"]
        ids, scroll = [], None
        while True:
            p = {"status": "active", "search_type": "scan", "limit": 100}
            if scroll:
                p["scroll_id"] = scroll
            j = self.get(f"/users/{uid}/items/search", p)
            res = j.get("results") or []
            if not res:
                break
            ids += res
            scroll = j.get("scroll_id")
            if not scroll:
                break
        return self.detalhes(ids)

    def detalhes(self, ids):
        saida = []
        for i in range(0, len(ids), 20):
            lote = ids[i:i + 20]
            j = self.get("/items", {"ids": ",".join(lote),
                                    "attributes": "id,title,status,shipping,sold_quantity"})
            for e in j:
                if e.get("code") == 200:
                    saida.append(e["body"])
        return saida


# ------------------------------------------------------------ modalidades
MODALIDADES = {
    "fulfillment": "Full", "cross_docking": "Coleta", "xd_drop_off": "Agência",
    "drop_off": "Correios", "self_service": "Flex", "custom": "Frete próprio",
    "not_specified": "A combinar",
}


def modalidade(anuncio):
    if anuncio.get("modalidade"):          # informada no config.json (modo sem login)
        return anuncio["modalidade"]
    lt = ((anuncio.get("shipping") or {}).get("logistic_type")) or "?"
    return MODALIDADES.get(lt, lt)


class ClientePublico:
    """Consulta sem login: o cálculo de frete por CEP do ML é público.
    Nesse modo os anúncios vêm da lista do config.json (sem busca automática)."""
    def __init__(self):
        self.s = requests.Session()
        self.s.headers["User-Agent"] = "monitor-prazos/1.0"

    def get(self, caminho, params=None, headers=None):
        ultimo = None
        for tentativa in range(5):
            r = self.s.get(API + caminho, params=params, headers=headers, timeout=30)
            ultimo = r
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(2 ** tentativa + random.random())
                continue
            r.raise_for_status()
            return r.json()
        ultimo.raise_for_status()


def representativos(anuncios, por_modalidade=1):
    """Um (ou N) anúncio(s) por modalidade de envio — os mais vendidos de cada uma.

    O prazo depende da origem e da modalidade, não do produto: anúncios da mesma
    modalidade têm o mesmo prazo por CEP, então consultar todos só repete o número.
    """
    grupos = {}
    for a in anuncios:
        grupos.setdefault(modalidade(a), []).append(a)
    saida = []
    for mod, lst in grupos.items():
        lst.sort(key=lambda a: -(a.get("sold_quantity") or 0))
        saida += lst[:por_modalidade]
    return saida


# ------------------------------------------------------------ interpretação
def _data(s):
    if not s:
        return None
    try:
        return dt.date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


def interpretar(js, hoje):
    """Prazo da opção que o comprador vê por padrão no anúncio.

    A API devolve várias opções (entrega em casa, retirada em agência, transportadoras
    diferentes). O ML marca com display="recommended" a que aparece no anúncio; se não
    houver, usa a entrega em casa mais rápida.
    """
    todas = _todas_opcoes(js, hoje)
    if not todas:
        return []
    rec = [o for o in todas if o["_display"] == "recommended"]
    casa = [o for o in todas if o["_tipo"] in ("address", None)] or todas
    escolhida = rec[0] if rec else min(casa, key=lambda o: o["dias_min"])
    return [{k: v for k, v in escolhida.items() if not k.startswith("_")}]


def _todas_opcoes(js, hoje):
    saida = []
    for o in (js or {}).get("options") or []:
        edt = o.get("estimated_delivery_time") or {}
        d1 = _data(edt.get("date"))
        d2 = _data((edt.get("offset") or {}).get("date")) or d1
        if d1 is None:
            horas = sum((edt.get(k) or 0) for k in ("handling", "shipping"))
            if horas and (edt.get("unit") or "hour") == "hour":
                d1 = d2 = hoje + dt.timedelta(days=math.ceil(horas / 24))
        if d1 is None:
            continue
        if d2 < d1:
            d1, d2 = d2, d1
        saida.append({
            "_display": o.get("display"), "_tipo": o.get("shipping_option_type"),
            "opcao": ("frete grátis" if o.get("cost") == 0 else "frete pago")
                     + (" · retirada" if o.get("shipping_option_type") == "agency" else ""),
            "dias_min": (d1 - hoje).days, "dias_max": (d2 - hoje).days,
            "data_entrega_min": d1.isoformat(), "data_entrega_max": d2.isoformat(),
            "frete": o.get("cost"),
        })
    return saida


# ------------------------------------------------------------ coleta diária
def coletar(cfg, token_path, ceps, salvar, log, hoje, limite=None):
    cfg_ml = cfg["ml"]
    escolha = cfg_ml.get("anuncios", "auto")
    if isinstance(escolha, list) and carregar_tokens(token_path) is None:
        cli = ClientePublico()
        anuncios = [a if isinstance(a, dict) else {"id": a} for a in escolha]
        anuncios = [{"title": a.get("titulo"), "modalidade": a.get("modalidade") or "ML", **a} for a in anuncios]
    elif escolha in ("auto", "todos"):
        cli = ClienteML(cfg_ml, token_path)
        anuncios = cli.anuncios_ativos()
        if escolha == "auto":
            anuncios = representativos(anuncios, int(cfg_ml.get("anuncios_por_modalidade", 1)))
    else:
        cli = ClienteML(cfg_ml, token_path)
        anuncios = cli.detalhes([a["id"] if isinstance(a, dict) else a for a in escolha])
    if limite:
        anuncios = anuncios[:int(limite)]
    log(f"ML: {len(anuncios)} anúncios ({', '.join(sorted({modalidade(a) for a in anuncios}))}) "
        f"x {len(ceps)} CEPs = {len(anuncios) * len(ceps)} consultas")

    agora = dt.datetime.now().isoformat(timespec="seconds")

    def consultar(a, c):
        base = {"coletado_em": agora, "data": hoje.isoformat(), "marketplace": "ml",
                "modalidade": modalidade(a), "anuncio_id": a["id"], "anuncio_titulo": a.get("title"), "uf": c["uf"],
                "regiao": c["regiao"], "cidade": c["cidade"], "cep": c["cep"]}
        try:
            js = cli.get(f"/items/{a['id']}/shipping_options", {"zip_code": c["cep"]})
        except Exception as e:  # noqa: BLE001
            return [{**base, "status": "erro", "erro": str(e)[:300]}]
        opcoes = interpretar(js, hoje)
        if not opcoes:
            return [{**base, "status": "indisponivel", "bruto": json.dumps(js, ensure_ascii=False)[:3000]}]
        return [{**base, **o, "status": "ok"} for o in opcoes]

    ok = erro = 0
    with ThreadPoolExecutor(max_workers=int(cfg_ml.get("paralelo", 4))) as ex:
        futs = [ex.submit(consultar, a, c) for a in anuncios for c in ceps]
        for f in as_completed(futs):
            linhas = f.result()
            salvar(linhas)
            if linhas[0]["status"] == "ok":
                ok += 1
            else:
                erro += 1
    log(f"ML: {ok} consultas com prazo, {erro} sem prazo/erro")


# ------------------------------------------------------------ entregas reais
def _norm(txt):
    import unicodedata
    t = unicodedata.normalize("NFKD", str(txt or "")).encode("ascii", "ignore").decode()
    return t.lower().strip()


def _dt(s):
    """Data (fuso de Brasília) de um timestamp ISO do ML."""
    if not s:
        return None
    try:
        d = dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return _data(s)
    if d.tzinfo:
        d = d.astimezone(dt.timezone(dt.timedelta(hours=-3)))
    return d.date()


def _achar(obj, *caminhos):
    for cam in caminhos:
        o = obj
        try:
            for k in cam.split("."):
                o = o[k]
        except (KeyError, TypeError, IndexError):
            continue
        if o not in (None, ""):
            return o
    return None


def _data_entregue(cli, envio_id, env):
    d = _achar(env, "status_history.date_delivered")
    if d:
        return _dt(d)
    try:
        h = cli.get(f"/shipments/{envio_id}/history", headers={"x-format-new": "true"})
    except Exception:  # noqa: BLE001
        return None
    eventos = h if isinstance(h, list) else (h.get("history") or h.get("results") or [])
    datas = [e.get("date") for e in eventos if isinstance(e, dict) and e.get("status") == "delivered"]
    return _dt(min(datas)) if datas else None


def interpretar_envio(env, pedido, entregue, capitais):
    uf = str(_achar(env, "destination.shipping_address.state.id", "receiver_address.state.id") or "")
    uf = uf.replace("BR-", "")[-2:].upper()
    cidade = _achar(env, "destination.shipping_address.city.name", "receiver_address.city.name") or ""
    feito = _dt(pedido.get("date_created") or env.get("date_created"))
    prometido = _dt(_achar(env, "lead_time.estimated_delivery_time.date",
                           "shipping_option.estimated_delivery_time.date",
                           "lead_time.estimated_delivery_final.date",
                           "shipping_option.estimated_delivery_final.date"))
    if not (uf and feito and entregue):
        return None
    lt = _achar(env, "logistic.type", "logistic_type") or "?"
    return {
        "envio_id": _hash(env.get("id")), "pedido_id": None, "marketplace": "ml",
        "modalidade": MODALIDADES.get(lt, lt), "data_pedido": feito.isoformat(),
        "data_prometida": prometido.isoformat() if prometido else None,
        "data_entregue": entregue.isoformat(), "uf": uf, "cidade": None,
        "regiao": "capital" if _norm(cidade) == capitais.get(uf) else "interior",
        "dias_prometidos": (prometido - feito).days if prometido else None,
        "dias_reais": (entregue - feito).days,
        "no_prazo": None if not prometido else int(entregue <= prometido),
        "atualizado_em": dt.datetime.now().isoformat(timespec="seconds"),
    }


def coletar_entregas(cfg, token_path, ceps, gravar, ja_gravados, log, hoje, dias=45):
    """Pedidos do ML dos últimos `dias` já entregues: prazo prometido x prazo real."""
    cli = ClienteML(cfg["ml"], token_path)
    uid = cli.get("/users/me")["id"]
    capitais = {c["uf"]: _norm(c["cidade"]) for c in ceps if c["regiao"] == "capital"}
    desde = (hoje - dt.timedelta(days=dias)).isoformat() + "T00:00:00.000-03:00"
    pedidos, offset = [], 0
    while True:
        j = cli.get("/orders/search", {"seller": uid, "order.date_created.from": desde,
                                       "sort": "date_desc", "limit": 50, "offset": offset})
        res = j.get("results") or []
        pedidos += res
        offset += len(res)
        if not res or offset >= min((j.get("paging") or {}).get("total", 0), 9950):
            break
    pendentes = {}
    for p in pedidos:
        sid = (p.get("shipping") or {}).get("id")
        if sid and _hash(sid) not in ja_gravados:
            pendentes[str(sid)] = p

    def um(sid, p):
        env = cli.get(f"/shipments/{sid}", headers={"x-format-new": "true"})
        if env.get("status") != "delivered":
            return None
        return interpretar_envio(env, p, _data_entregue(cli, sid, env), capitais)

    novas, falhas = [], 0
    with ThreadPoolExecutor(max_workers=int(cfg["ml"].get("paralelo", 4))) as ex:
        futs = [ex.submit(um, s, p) for s, p in pendentes.items()]
        for f in as_completed(futs):
            try:
                r = f.result()
            except Exception:  # noqa: BLE001
                falhas += 1
                continue
            if r:
                novas.append(r)
    gravar(novas)
    log(f"ML entregas: {len(pedidos)} pedidos em {dias} dias, {len(novas)} novas entregas gravadas"
        + (f", {falhas} falhas" if falhas else ""))
