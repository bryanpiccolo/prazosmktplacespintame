"""Prazo de entrega no site da loja (Shopify), pelo calculo de frete do carrinho.

Para cada CEP: coloca o produto no carrinho (sessao propria, nao mexe em nada da loja)
e pede as opcoes de frete, como o cliente ve no carrinho/checkout. Grava duas linhas:
  - "Site"            -> opcao mais barata (o que a maioria escolhe)
  - "Site · expresso" -> opcao mais rapida
O prazo e convertido para dias corridos a partir do dia da coleta, igual ao Mercado Livre.
"""
import datetime as dt
import json
import time

import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/129.0 Safari/537.36")


def _mais_uteis(d, n):
    """Data depois de n dias uteis (sem sabado/domingo) a partir de d."""
    while n > 0:
        d += dt.timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d


def interpretar(rate, hoje):
    """(dias_min, dias_max, data_min, data_max) em dias corridos, ou None."""
    datas = [x for x in (rate.get("delivery_range") or []) if x] or ([rate["delivery_date"]] if rate.get("delivery_date") else [])
    if datas:
        ds = sorted(dt.date.fromisoformat(x[:10]) for x in datas)
        a, b = ds[0], ds[-1]
    else:
        dias = [int(x) for x in (rate.get("delivery_days") or []) if x is not None]
        if not dias:
            return None
        a, b = _mais_uteis(hoje, min(dias)), _mais_uteis(hoje, max(dias))
    return (a - hoje).days, (b - hoje).days, a.isoformat(), b.isoformat()


class Loja:
    def __init__(self, base):
        self.base = base.rstrip("/")
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA, "Accept": "application/json"})

    def produto(self, handle, variante=None):
        p = self.s.get(f"{self.base}/products/{handle}.js", timeout=30)
        p.raise_for_status()
        p = p.json()
        vs = p["variants"]
        v = next((x for x in vs if str(x["id"]) == str(variante)), None) if variante else None
        v = v or next((x for x in vs if x.get("available")), vs[0])
        return p, v

    def carrinho(self, variante_id):
        self.s.post(f"{self.base}/cart/clear.js", timeout=30)
        r = self.s.post(f"{self.base}/cart/add.js", json={"items": [{"id": variante_id, "quantity": 1}]}, timeout=30)
        r.raise_for_status()

    def fretes(self, cep, uf):
        q = {"shipping_address[zip]": cep, "shipping_address[country]": "Brazil", "shipping_address[province]": uf}
        r = self.s.get(f"{self.base}/cart/shipping_rates.json", params=q, timeout=45)
        if r.status_code == 200 and (r.json().get("shipping_rates")):
            return r.json()["shipping_rates"]
        # fretes de transportadora as vezes vem em modo assincrono
        self.s.post(f"{self.base}/cart/prepare_shipping_rates.json", params=q, timeout=45)
        for _ in range(10):
            time.sleep(1.5)
            a = self.s.get(f"{self.base}/cart/async_shipping_rates.json", params=q, timeout=45)
            if a.status_code == 200 and a.text not in ("", "null"):
                return (a.json() or {}).get("shipping_rates") or []
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
        return []


def coletar(cfg, ceps, salvar, log, hoje, limite=None):
    c = cfg.get("site", {})
    loja = Loja(c.get("loja", "https://www.pintame.com.br"))
    handle = c["produto"]
    p, v = loja.produto(handle, c.get("variante"))
    loja.carrinho(v["id"])
    titulo = c.get("titulo") or p.get("title")
    log(f"Site: {titulo} (variante {v.get('title')}) em {len(ceps[:limite] if limite else ceps)} CEPs")
    agora = dt.datetime.now().isoformat(timespec="seconds")
    ok = 0
    for i, cp in enumerate(ceps[:limite] if limite else ceps):
        base = {"coletado_em": agora, "data": hoje.isoformat(), "marketplace": "site", "anuncio_id": handle,
                "anuncio_titulo": titulo, "uf": cp["uf"], "regiao": cp["regiao"], "cidade": cp["cidade"], "cep": cp["cep"]}
        linhas = []
        try:
            rates = loja.fretes(cp["cep"], cp["uf"])
            opcoes = []
            for r in rates:
                x = interpretar(r, hoje)
                if x:
                    opcoes.append((float(r.get("price") or 0), x, r.get("name")))
            if not opcoes:
                linhas = [{**base, "modalidade": m, "status": "indisponivel",
                           "bruto": json.dumps(rates, ensure_ascii=False)[:3000]} for m in ("Site", "Site · expresso")]
            else:
                barata = min(opcoes, key=lambda o: (o[0], o[1][0]))
                rapida = min(opcoes, key=lambda o: (o[1][0], o[0]))
                for mod, (preco, (dmin, dmax, d1, d2), nome) in (("Site", barata), ("Site · expresso", rapida)):
                    linhas.append({**base, "modalidade": mod, "opcao": nome, "dias_min": dmin, "dias_max": dmax,
                                   "data_entrega_min": d1, "data_entrega_max": d2, "frete": preco, "status": "ok"})
                ok += 1
        except Exception as e:  # noqa: BLE001
            linhas = [{**base, "modalidade": m, "status": "erro", "erro": str(e)[:500]} for m in ("Site", "Site · expresso")]
            if i == 0:
                loja.carrinho(v["id"])   # sessao pode ter expirado: tenta recriar uma vez
        salvar(linhas)
        time.sleep(c.get("pausa_segundos", 0.7))
    try:
        loja.s.post(f"{loja.base}/cart/clear.js", timeout=30)
    except Exception:  # noqa: BLE001
        pass
    log(f"Site: {ok} de {len(ceps[:limite] if limite else ceps)} CEPs com prazo")
    if ok == 0:
        raise RuntimeError("nenhum CEP com prazo no site")
