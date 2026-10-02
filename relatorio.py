"""E-mails: resumo semanal (segunda-feira) e alerta quando um prazo sai do normal.

Formato: somente texto simples, sem HTML, sem links, sem emoji, sem simbolos especiais
e sem acentos. O servidor de e-mail da empresa (Zoho) bloqueia mensagens fora disso.
"""
import csv
import datetime as dt
import os
import smtplib
import ssl
import statistics
import time
import unicodedata
from email.mime.text import MIMEText
from pathlib import Path

import metricas as M

BASE = Path(__file__).resolve().parent
FUSO = "America/Sao_Paulo"
DIAS_SEMANA = ["segunda", "terca", "quarta", "quinta", "sexta", "sabado", "domingo"]


# ------------------------------------------------------------ formatacao
def asc(t):
    """Texto so com caracteres ASCII simples: tira acentos, cedilha e simbolos."""
    for a, b in (("·", "-"), ("–", "-"), ("—", "-"), ("…", "..."), ("“", ""), ("”", ""),
                 ("‘", ""), ("’", ""), ("º", "o"), ("ª", "a"), (" ", " ")):
        t = t.replace(a, b)
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode("ascii")
    return "\n".join(l.rstrip() for l in t.split("\n"))


def n1(v):
    if v is None:
        return "sem dado"
    return f"{v:.1f}".replace(".", ",").replace(",0", "") if abs(v - round(v)) < 0.05 else f"{v:.1f}".replace(".", ",")


def pct(v):
    return "sem dado" if v is None else f"{round(v * 100)}%"


def var(v, unidade="dias"):
    """Variacao por extenso: 'mais 1,5 dias', 'menos 0,5 dias', 'igual'."""
    if v is None:
        return ""
    if abs(v) < 0.05:
        return "igual"
    return f"{'mais' if v > 0 else 'menos'} {n1(abs(v))} {unidade}"


def titulo(t):
    return f"\n{t.upper()}\n"


# ------------------------------------------------------------ envio
def enviar(cfg, assunto, texto, log, teste=False, destino=None, nome="email"):
    assunto, texto = asc(assunto), asc(texto)
    if teste:
        arq = Path(destino or BASE) / f"{nome}_teste.txt"
        arq.write_text(texto, encoding="ascii")
        arq.with_suffix(".assunto.txt").write_text(assunto, encoding="ascii")   # usado pelo agendamento do Claude
        log(f"E-mail de teste salvo em {arq} (assunto: {assunto})")
        return arq
    para = os.environ.get("EMAIL_PARA") or ",".join(cfg.get("email", {}).get("para", []))
    usuario, senha = os.environ.get("SMTP_USER"), os.environ.get("SMTP_PASS")
    if not (para and usuario and senha):
        log("E-mail nao enviado: faltam SMTP_USER, SMTP_PASS ou EMAIL_PARA.")
        return None
    msg = MIMEText(texto, "plain", "us-ascii")
    msg["Subject"], msg["From"], msg["To"] = assunto, os.environ.get("EMAIL_DE", usuario), para
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    porta = int(os.environ.get("SMTP_PORT", "465"))
    with smtplib.SMTP_SSL(host, porta, context=ssl.create_default_context()) as s:
        s.login(usuario, senha)
        s.sendmail(usuario, [p.strip() for p in para.split(",") if p.strip()], msg.as_string())
    log(f"E-mail enviado para {para}: {assunto}")
    return True


RODAPE = ("\n--\nPrazo prometido e o prazo exibido ao comprador no anuncio, em dias corridos, para uma capital "
          "e uma cidade do interior de cada estado. Graficos e historico completo no painel de prazos. "
          "Enviado automaticamente pelo monitor de prazos da Pinta Me Mais.\n")


def aguardar_ate(hhmm):
    """O agendador do GitHub as vezes adianta/atrasa; se chegar cedo, espera ate HH:MM."""
    from zoneinfo import ZoneInfo
    agora = dt.datetime.now(ZoneInfo(FUSO))
    h, m = (int(x) for x in hhmm.split(":"))
    alvo = agora.replace(hour=h, minute=m, second=0, microsecond=0)
    espera = (alvo - agora).total_seconds()
    if 0 < espera <= 45 * 60:
        time.sleep(espera)


def _cidades():
    return {(r["uf"], r["regiao"]): r["cidade"] for r in csv.DictReader((BASE / "ceps.csv").open(encoding="utf-8"))}


def _local(cid, u, rg):
    return f"{u} {cid.get((u, rg), rg)} ({rg})"


# ------------------------------------------------------------ alertas
def normal_mesmo_dia_semana(conn, dia, semanas=5, minimo_dias=7):
    """Prazo normal de cada canal/UF/regiao comparando com o MESMO dia da semana.

    O prazo e contado em dias corridos, entao pedidos de sexta e sabado atravessam o fim de
    semana e sempre mostram 2 a 3 dias a mais. Comparar sexta com sexta evita falso alarme.
    """
    hist = M.diario(conn, dia - dt.timedelta(days=7 * semanas), dia - dt.timedelta(days=1))
    total, mesmo = {}, {}
    for d, c, u, rg, dmin, dmax, fr in hist:
        k = (c, u, rg)
        total[k] = total.get(k, 0) + 1
        if dt.date.fromisoformat(d).weekday() == dia.weekday():
            mesmo.setdefault(k, []).append((dmin, fr or 0))
    return {k: {"dias": statistics.median(x[0] for x in v), "frete": statistics.median(x[1] for x in v), "n": len(v)}
            for k, v in mesmo.items() if total.get(k, 0) >= minimo_dias}


def _casos(conn, cfg, dia, cid):
    """Situacoes fora do normal num dia: [(tipo, chave, linha_texto)]."""
    ca = cfg.get("alertas", {})
    lim_dias, lim_pct = ca.get("dias_acima", 2), ca.get("percentual_acima", 0.3)
    normal = normal_mesmo_dia_semana(conn, dia, minimo_dias=ca.get("minimo_dias", 7))
    linhas = M.diario(conn, dia, dia)
    casos = []
    for d, c, u, rg, dmin, dmax, fr in linhas:
        n = normal.get((c, u, rg))
        if not n:
            continue
        delta = dmin - n["dias"]
        if delta >= max(lim_dias, lim_pct * n["dias"]):
            casos.append(("prazo", f"prazo|{c}|{u}|{rg}",
                          f"{_local(cid, u, rg)} - {c} - hoje {n1(dmin)} dias - normal {n1(n['dias'])} dias - {var(delta)}"))
        if (fr or 0) > 0 and n["frete"] == 0:
            casos.append(("frete", f"frete|{c}|{u}|{rg}",
                          f"{_local(cid, u, rg)} - {c} - frete hoje {n1(fr)} reais - normalmente gratis"))
    presentes = {(r[1], r[2], r[3]) for r in linhas}
    falhas = conn.execute("""SELECT marketplace, COALESCE(modalidade,''), uf, regiao FROM consultas
                             WHERE data = ? GROUP BY 1,2,3,4 HAVING MAX(status = 'ok') = 0""", (dia.isoformat(),)).fetchall()
    for mk, mod, u, rg in falhas:
        c = "Shopee" if mk == "shopee" else ("Mercado Livre" if mod in ("", "ML") else f"ML - {mod}")
        if (c, u, rg) in normal and (c, u, rg) not in presentes:
            casos.append(("sem", f"sem|{c}|{u}|{rg}",
                          f"{_local(cid, u, rg)} - {c} - sem opcao de entrega hoje - normal {n1(normal[(c, u, rg)]['dias'])} dias"))
    return casos


def alertas(conn, cfg, hoje, log, teste=False, destino=None):
    ca = cfg.get("alertas", {})
    cid = _cidades()
    casos = _casos(conn, cfg, hoje, cid)
    # nao repetir: ignora o que ja estava fora do normal em algum dos 3 dias anteriores
    recentes = {x[1] for k in (1, 2, 3) for x in _casos(conn, cfg, hoje - dt.timedelta(days=k), cid)}
    novos = [x for x in casos if x[1] not in recentes]

    ativos = [m for m in ("ml", "shopee") if cfg.get(m, {}).get("ativo", m == "ml")]
    ok_hoje = {r[0] for r in conn.execute("SELECT DISTINCT marketplace FROM consultas WHERE data = ? AND status = 'ok'",
                                          (hoje.isoformat(),))}
    coleta_falhou = [m for m in ativos if m not in ok_hoje]
    if not novos and not coleta_falhou:
        log(f"Alertas: nada novo fora do normal hoje ({len(casos)} caso(s) ja em andamento).")
        return None

    corpo, resumo = [f"Prazos de entrega fora do normal - coleta de {DIAS_SEMANA[hoje.weekday()]} {hoje:%d/%m/%Y}"], []
    if coleta_falhou:
        nomes = " e ".join({"ml": "Mercado Livre", "shopee": "Shopee"}[m] for m in coleta_falhou)
        corpo.append(f"\nATENCAO - a coleta de hoje nao trouxe dados de {nomes}. "
                     "O motivo aparece na aba Actions do repositorio no GitHub.")
        resumo.append(f"coleta sem dados de {nomes}")
    grupos = [("prazo", "Prazo bem acima do normal", "prazo acima do normal", "prazos acima do normal"),
              ("frete", "Frete deixou de ser gratis", "frete deixou de ser gratis", "fretes deixaram de ser gratis"),
              ("sem", "Sem opcao de entrega hoje", "destino sem entrega", "destinos sem entrega")]
    for tipo, tit, sing, plur in grupos:
        ls = [x[2] for x in novos if x[0] == tipo]
        if ls:
            corpo.append(titulo(f"{tit} ({len(ls)})") + "\n".join(ls))
            resumo.append(f"{len(ls)} {sing if len(ls) == 1 else plur}")
    corpo.append(f"\nComo funciona - acima do normal quer dizer pelo menos {ca.get('dias_acima', 2)} dias ou "
                 f"{round(ca.get('percentual_acima', 0.3) * 100)}% a mais que o prazo normal do mesmo canal, estado e "
                 f"cidade, comparando sempre com o mesmo dia da semana (sexta com sexta, por exemplo). "
                 f"Cada situacao e avisada uma vez - se continuar nos dias seguintes, nao repete.")
    assunto = f"Prazos de entrega - alerta {hoje:%d/%m} - " + ", ".join(resumo)
    return enviar(cfg, assunto, "\n".join(corpo) + "\n" + RODAPE, log, teste, destino, "alerta")


# ------------------------------------------------------------ resumo semanal
def semanal(conn, cfg, hoje, log, teste=False, destino=None):
    s1, s2 = M.semana_passada(hoje)
    a1, a2 = s1 - dt.timedelta(days=7), s1 - dt.timedelta(days=1)
    ontem = hoje - dt.timedelta(days=1)
    if ontem >= hoje.replace(day=1):          # caso normal: mes corrente ate ontem
        m1 = hoje.replace(day=1)
        rot_mes = f"{M.mes_label(m1)} ate {ontem:%d/%m}"
    else:                                      # segunda-feira dia 1o: mostra o mes que acabou de fechar
        m1 = ontem.replace(day=1)
        rot_mes = f"{M.mes_label(m1)} fechado"

    sem, ant, mes = M.resumo_periodo(conn, s1, s2), M.resumo_periodo(conn, a1, a2), M.resumo_periodo(conn, m1, ontem)
    fechados = [(M.mes_label(a), M.resumo_periodo(conn, a, b)) for a, b in M.meses_fechados(conn, hoje) if a != m1]
    canais = M.ordenar_canais(list(sem["prom"]) + list(mes["prom"]) + [c for _, f in fechados for c in f["prom"]])
    nf = len(fechados)
    rot_media = f"media {nf} {'mes fechado' if nf == 1 else 'meses fechados'}"

    def media(vals):
        vals = [v for v in vals if v is not None]
        return sum(vals) / len(vals) if vals else None

    corpo = [f"Resumo dos prazos de entrega - semana {s1:%d/%m} a {s2:%d/%m}",
             f"Mercado Livre{' e Shopee' if cfg.get('shopee', {}).get('ativo') else ''} - enviado em {hoje:%d/%m/%Y}"]

    # ---- destaques
    dest = []
    for c in canais:
        v, a = sem["prom"].get(c), ant["prom"].get(c)
        if v is None:
            continue
        t = f"{c} - prazo prometido medio de {n1(v)} dias na semana"
        if a is not None:
            t += f" - {var(v - a)} que a semana anterior" if abs(v - a) >= 0.05 else " - igual a semana anterior"
        if nf:
            t += f" - {rot_media} {n1(media(f['prom'].get(c) for _, f in fechados))} dias"
        dest.append(t)
    es, ea = sem["ent"], ant["ent"]
    if es["n"]:
        t = f"Entregas reais Mercado Livre - {es['n']} pedidos entregues - {pct(es['pct'])} no prazo"
        if ea["pct"] is not None and es["pct"] is not None:
            t += f" ({var((es['pct'] - ea['pct']) * 100, 'pontos')} vs semana anterior)"
        t += f" - prazo real medio {n1(es['real'])} dias (prometido {n1(es['prom'])})"
        dest.append(t)
    pioras = []
    for c in canais:
        for u in sorted({r[2] for r in sem["linhas"]}):
            v, a = M.prometido(sem["linhas"], uf=u).get(c), M.prometido(ant["linhas"], uf=u).get(c)
            if v is not None and a is not None and v - a >= 1:
                pioras.append((v - a, u, c))
    pioras.sort(reverse=True)
    if pioras:
        dest.append("Maiores altas de prazo na semana - " + ", ".join(
            f"{u} mais {n1(d)} dias" + (f" ({c})" if len(canais) > 1 else "") for d, u, c in pioras[:5]))
    for c, lst in M.frete_pago(sem["linhas"]).items():
        dest.append(f"{c} - frete pago pelo comprador em {len(lst)} dos 54 destinos - " +
                    ", ".join(f"{u} {rg}" for u, rg, _ in lst[:6]) + (" e outros" if len(lst) > 6 else ""))
    corpo.append(titulo("Destaques") + "\n\n".join(dest or ["Ainda nao ha dados suficientes para a semana."]))

    # ---- prazo prometido por periodo
    bloco = []
    for c in canais:
        for rg, nome in ((None, c), ("capital", f"{c} capitais"), ("interior", f"{c} interior")):
            f = lambda r: M.prometido(r["linhas"], rg).get(c)  # noqa: E731
            mf = media(M.prometido(x["linhas"], rg).get(c) for _, x in fechados)
            partes = [f"semana {n1(f(sem))}", f"semana anterior {n1(f(ant))}", f"{rot_mes} {n1(f(mes))}"]
            if nf:
                partes.append(f"{rot_media} {n1(mf)}")
            bloco.append(f"{nome} - " + " - ".join(partes))
    corpo.append(titulo("Prazo prometido medio em dias") + "\n".join(bloco))

    # ---- entregas reais
    if any(x["ent"]["n"] for x in (sem, ant, mes)) or any(f["ent"]["n"] for _, f in fechados):
        def lin(rot, k, fmt):
            p = [f"semana {fmt(es[k])}", f"semana anterior {fmt(ea[k])}", f"{rot_mes} {fmt(mes['ent'][k])}"]
            if nf:
                p.append(f"{rot_media} {fmt(media(f['ent'][k] for _, f in fechados))}")
            return f"{rot} - " + " - ".join(p)
        inteiro = lambda v: "sem dado" if v is None else str(round(v))  # noqa: E731
        corpo.append(titulo("Entregas reais Mercado Livre") + "\n".join([
            lin("Pedidos entregues", "n", inteiro), lin("No prazo", "pct", pct),
            lin("Prazo real medio em dias", "real", n1), lin("Prazo prometido na compra", "prom", n1),
            lin("Atraso medio quando atrasa", "atraso_medio", n1)]))

    # ---- meses fechados
    if fechados:
        ls = []
        for rot, f in fechados:
            p = [f"{c} {n1(f['prom'].get(c))} dias" for c in canais]
            if f["ent"]["n"]:
                p.append(f"entregas reais {n1(f['ent']['real'])} dias, {pct(f['ent']['pct'])} no prazo, {f['ent']['n']} pedidos")
            ls.append(f"{rot} - " + " - ".join(p))
        corpo.append(titulo("Meses fechados") + "\n".join(ls))

    # ---- estados com prazo mais longo
    ufs = sorted({r[2] for r in sem["linhas"]})
    if ufs and canais:
        rank = []
        for u in ufs:
            vs = [M.prometido(sem["linhas"], uf=u).get(c) for c in canais]
            rank.append((max(v for v in vs if v is not None), u, vs))
        rank.sort(reverse=True)
        corpo.append(titulo("Estados com prazo mais longo na semana") + "\n".join(
            f"{u} - " + " - ".join((f"{c} " if len(canais) > 1 else "") + f"{n1(v)} dias" for c, v in zip(canais, vs))
            for _, u, vs in rank[:8]))

    atr = []
    for u in sorted({r[0] for r in conn.execute("SELECT DISTINCT uf FROM entregas WHERE data_entregue BETWEEN ? AND ?",
                                                (s1.isoformat(), s2.isoformat()))}):
        x = M.entregas(conn, s1, s2, uf=u)
        if x["nP"] >= 3 and x["pct"] < 1:
            atr.append((x["pct"], u, x))
    if atr:
        atr.sort()
        corpo.append(titulo("Onde mais atrasou nas entregas da semana") + "\n".join(
            f"{u} - {pct(p)} no prazo - {x['atrasos']} atrasos em {x['nP']} pedidos - prazo real medio {n1(x['real'])} dias"
            for p, u, x in atr[:6]))

    assunto = f"Prazos de entrega - resumo semanal {s1:%d/%m} a {s2:%d/%m}"
    return enviar(cfg, assunto, "\n".join(corpo) + "\n" + RODAPE, log, teste, destino, "semanal")
