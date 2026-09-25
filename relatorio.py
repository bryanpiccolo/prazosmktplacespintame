"""E-mails: resumo semanal (segunda-feira) e alerta quando um prazo sai do normal."""
import csv
import datetime as dt
import html
import os
import re
import smtplib
import ssl
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import metricas as M

BASE = Path(__file__).resolve().parent
FUSO = "America/Sao_Paulo"

# ------------------------------------------------------------ formatação
def n1(v, suf=""):
    return "–" if v is None else f"{v:.1f}".replace(".", ",") + suf


def pct(v):
    return "–" if v is None else f"{round(v * 100)}%"


def dif(v, casas=1, inverso=False):
    """Variação com seta. Para prazo, subir é ruim (vermelho); para % no prazo, `inverso`."""
    if v is None or abs(v) < 0.05:
        return '<span style="color:#7a7974">=</span>'
    ruim = (v > 0) != inverso
    cor = "#c2412d" if ruim else "#1e7a3c"
    return f'<span style="color:{cor};font-weight:600">{"▲ +" if v > 0 else "▼ −"}{f"{abs(v):.{casas}f}".replace(".", ",")}</span>'


def e(txt):
    return html.escape(str(txt))


ESTILO_TAB = 'style="border-collapse:collapse;width:100%;font-size:14px;margin:6px 0 18px"'
TH = 'style="text-align:right;padding:7px 10px;border-bottom:2px solid #e4e2dc;color:#52514e;font-weight:600;font-size:12.5px"'
TH1 = TH.replace("text-align:right", "text-align:left")
TD = 'style="text-align:right;padding:7px 10px;border-bottom:1px solid #eeece7;white-space:nowrap"'
TD1 = TD.replace("text-align:right", "text-align:left")


def tabela(cab, linhas):
    h = f"<table {ESTILO_TAB}><tr>" + "".join(f"<th {TH1 if i == 0 else TH}>{c}</th>" for i, c in enumerate(cab)) + "</tr>"
    for l in linhas:
        h += "<tr>" + "".join(f"<td {TD1 if i == 0 else TD}>{c}</td>" for i, c in enumerate(l)) + "</tr>"
    return h + "</table>"


def moldura(titulo, sub, corpo, url_painel=None):
    botao = (f'<table role="presentation" cellspacing="0" cellpadding="0" style="margin:22px 0 4px"><tr>'
             f'<td bgcolor="#2a78d6" style="background-color:#2a78d6;border-radius:8px;padding:10px 18px">'
             f'<a href="{e(url_painel)}" style="color:#ffffff;text-decoration:none;font-weight:600">'
             f'Abrir o painel completo</a></td></tr></table>') if url_painel else ""
    return f"""<!doctype html><html><body style="margin:0;background:#f4f3ef;padding:24px 12px;
font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif;color:#0b0b0b">
<div style="max-width:720px;margin:0 auto;background:#fff;border:1px solid #e4e2dc;border-radius:14px;padding:26px 26px 20px">
<h1 style="font-size:21px;margin:0 0 4px;letter-spacing:-.01em">{titulo}</h1>
<div style="color:#52514e;font-size:13.5px;margin-bottom:18px">{sub}</div>
{corpo}{botao}
<p style="color:#8f8e86;font-size:11.5px;margin-top:22px;line-height:1.5">Prazos prometidos: opção de frete exibida ao comprador,
em dias corridos, para uma capital e uma cidade do interior de cada estado. Entregas reais: pedidos do Mercado Livre
entregues no período. Enviado automaticamente pelo monitor de prazos.</p>
</div></body></html>"""


def h2(t):
    return f'<h2 style="font-size:15.5px;margin:22px 0 4px">{t}</h2>'


def lista(itens):
    return ('<ul style="padding-left:18px;margin:6px 0 14px;line-height:1.55;font-size:14px">'
            + "".join(f'<li style="margin-bottom:6px">{i}</li>' for i in itens) + "</ul>")


# ------------------------------------------------------------ envio
def enviar(cfg, assunto, corpo_html, texto, log, teste=False, destino=None, nome="email"):
    if teste:
        arq = Path(destino or BASE) / f"{nome}_teste.html"
        arq.write_text(corpo_html, encoding="utf-8")
        arq.with_suffix(".assunto.txt").write_text(assunto, encoding="utf-8")   # usado pelo agendamento do Claude
        log(f"E-mail de teste salvo em {arq} (assunto: {assunto})")
        return arq
    para = os.environ.get("EMAIL_PARA") or ",".join(cfg.get("email", {}).get("para", []))
    usuario, senha = os.environ.get("SMTP_USER"), os.environ.get("SMTP_PASS")
    if not (para and usuario and senha):
        log("E-mail não enviado: faltam SMTP_USER, SMTP_PASS ou EMAIL_PARA.")
        return None
    msg = MIMEMultipart("alternative")
    msg["Subject"], msg["From"], msg["To"] = assunto, os.environ.get("EMAIL_DE", usuario), para
    msg.attach(MIMEText(texto, "plain", "utf-8"))
    msg.attach(MIMEText(corpo_html, "html", "utf-8"))
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    porta = int(os.environ.get("SMTP_PORT", "465"))
    with smtplib.SMTP_SSL(host, porta, context=ssl.create_default_context()) as s:
        s.login(usuario, senha)
        s.sendmail(usuario, [p.strip() for p in para.split(",") if p.strip()], msg.as_string())
    log(f"E-mail enviado para {para}: {assunto}")
    return True


def aguardar_ate(hhmm):
    """O agendador do GitHub às vezes adianta/atrasa; se chegar cedo, espera até HH:MM."""
    from zoneinfo import ZoneInfo
    agora = dt.datetime.now(ZoneInfo(FUSO))
    h, m = (int(x) for x in hhmm.split(":"))
    alvo = agora.replace(hour=h, minute=m, second=0, microsecond=0)
    espera = (alvo - agora).total_seconds()
    if 0 < espera <= 45 * 60:
        time.sleep(espera)


def _cidades():
    return {(r["uf"], r["regiao"]): r["cidade"] for r in csv.DictReader((BASE / "ceps.csv").open(encoding="utf-8"))}


# ------------------------------------------------------------ alertas
def _casos(conn, cfg, dia, cid):
    """Situações fora do normal num dia: [(tipo, chave, colunas_html)]."""
    ca = cfg.get("alertas", {})
    lim_dias, lim_pct = ca.get("dias_acima", 2), ca.get("percentual_acima", 0.3)
    normal = M.normal_por_ponto(conn, dia, ca.get("janela_dias", 28), ca.get("minimo_dias", 7))
    linhas = M.diario(conn, dia, dia)
    casos = []
    for d, c, u, rg, dmin, dmax, fr in linhas:
        n = normal.get((c, u, rg))
        if not n:
            continue
        delta = dmin - n["dias"]
        if delta >= max(lim_dias, lim_pct * n["dias"]):
            casos.append(("prazo", f"prazo|{c}|{u}|{rg}",
                          [e(c), f"<b>{u}</b> · {e(cid.get((u, rg), rg))}", f"<b>{n1(dmin)}</b>", n1(n["dias"]), dif(delta)]))
        if (fr or 0) > 0 and n["frete"] == 0:
            casos.append(("frete", f"frete|{c}|{u}|{rg}",
                          [e(c), f"<b>{u}</b> · {e(cid.get((u, rg), rg))}", f"R$ {fr:.2f}".replace(".", ","), "grátis", ""]))
    presentes = {(r[1], r[2], r[3]) for r in linhas}
    falhas = conn.execute("""SELECT marketplace, COALESCE(modalidade,''), uf, regiao FROM consultas
                             WHERE data = ? GROUP BY 1,2,3,4 HAVING MAX(status = 'ok') = 0""", (dia.isoformat(),)).fetchall()
    for mk, mod, u, rg in falhas:
        c = "Shopee" if mk == "shopee" else ("Mercado Livre" if mod in ("", "ML") else f"ML · {mod}")
        if (c, u, rg) in normal and (c, u, rg) not in presentes:
            casos.append(("sem", f"sem|{c}|{u}|{rg}",
                          [e(c), f"<b>{u}</b> · {e(cid.get((u, rg), rg))}", "sem entrega", n1(normal[(c, u, rg)]["dias"]), ""]))
    return casos


def alertas(conn, cfg, hoje, log, teste=False, destino=None):
    ca = cfg.get("alertas", {})
    cid = _cidades()
    casos = _casos(conn, cfg, hoje, cid)
    # não repetir: ignora o que já estava fora do normal em algum dos 3 dias anteriores
    recentes = {x[1] for k in (1, 2, 3) for x in _casos(conn, cfg, hoje - dt.timedelta(days=k), cid)}
    novos = [x for x in casos if x[1] not in recentes]

    ativos = [m for m in ("ml", "shopee") if cfg.get(m, {}).get("ativo", m == "ml")]
    ok_hoje = {r[0] for r in conn.execute("SELECT DISTINCT marketplace FROM consultas WHERE data = ? AND status = 'ok'",
                                          (hoje.isoformat(),))}
    coleta_falhou = [m for m in ativos if m not in ok_hoje]
    if not novos and not coleta_falhou:
        log(f"Alertas: nada novo fora do normal hoje ({len(casos)} caso(s) já em andamento).")
        return None

    corpo, resumo = "", []
    grupos = [("prazo", "Prazo bem acima do normal", ["Canal", "Estado · cidade", "Hoje (dias)", "Normal", "Diferença"]),
              ("frete", "Frete deixou de ser grátis", ["Canal", "Estado · cidade", "Frete hoje", "Normalmente", ""]),
              ("sem", "Sem opção de entrega hoje", ["Canal", "Estado · cidade", "Hoje", "Normal (dias)", ""])]
    for tipo, tit, cab in grupos:
        ls = [x[2] for x in novos if x[0] == tipo]
        if ls:
            corpo += h2(f"{tit} ({len(ls)})") + tabela(cab, ls)
            resumo.append(f"{len(ls)} {tit.lower()}")
    if coleta_falhou:
        nomes = ", ".join({"ml": "Mercado Livre", "shopee": "Shopee"}[m] for m in coleta_falhou)
        corpo = (f'<p style="background:#fdecea;border-radius:8px;padding:10px 12px;color:#8a1f11">'
                 f'<b>A coleta de hoje não trouxe dados de {nomes}.</b> Veja a aba Actions do repositório no GitHub '
                 f'para o motivo.</p>') + corpo
        resumo.insert(0, f"coleta sem dados ({nomes})")
    corpo += (f'<p style="color:#52514e;font-size:12.5px">“Acima do normal” = pelo menos {ca.get("dias_acima", 2)} dias ou '
              f'{round(ca.get("percentual_acima", 0.3) * 100)}% a mais que a mediana dos últimos {ca.get("janela_dias", 28)} dias '
              f'para o mesmo canal, estado e cidade. Cada situação é avisada uma vez; se continuar, não repete.</p>')
    assunto = f"⚠ Prazos de entrega fora do normal — {hoje:%d/%m}: " + "; ".join(resumo)
    texto = assunto + "\n\n" + "\n".join(
        " | ".join(html.unescape(re.sub("<[^>]+>", "", c)) for c in x[2]) for x in novos)
    return enviar(cfg, assunto, moldura("Prazos fora do normal", f"Coleta de {hoje:%d/%m/%Y}", corpo, cfg.get("painel_url")),
                  texto, log, teste, destino, "alerta")


# ------------------------------------------------------------ resumo semanal
def semanal(conn, cfg, hoje, log, teste=False, destino=None):
    s1, s2 = M.semana_passada(hoje)
    a1, a2 = s1 - dt.timedelta(days=7), s1 - dt.timedelta(days=1)
    ontem = hoje - dt.timedelta(days=1)
    if ontem >= hoje.replace(day=1):          # caso normal: mês corrente até ontem
        m1 = hoje.replace(day=1)
        rot_mes = f"{M.mes_label(m1)} até {ontem:%d/%m}"
    else:                                      # segunda-feira dia 1º: mostra o mês que acabou de fechar
        m1 = ontem.replace(day=1)
        rot_mes = f"{M.mes_label(m1)} (fechado)"

    sem, ant, mes = M.resumo_periodo(conn, s1, s2), M.resumo_periodo(conn, a1, a2), M.resumo_periodo(conn, m1, ontem)
    fechados = [(M.mes_label(a), M.resumo_periodo(conn, a, b)) for a, b in M.meses_fechados(conn, hoje) if a != m1]
    canais = M.ordenar_canais(list(sem["prom"]) + list(mes["prom"]) + [c for _, f in fechados for c in f["prom"]])
    media_f = lambda chave, sub=None: (  # noqa: E731
        (lambda v: sum(v) / len(v) if v else None)(
            [(f[chave][sub] if sub else f[chave]) for _, f in fechados
             if (f[chave].get(sub) if sub else f[chave]) is not None]))
    nf = len(fechados)
    col_media = f"Média {nf} {'mês fechado' if nf == 1 else 'meses fechados'}" if nf else "Média meses fechados"

    # ---- destaques
    dest = []
    for c in canais:
        v, a = sem["prom"].get(c), ant["prom"].get(c)
        mf = media_f("prom", c) if nf else None
        if v is None:
            continue
        t = f"<b>{e(c)}</b>: prazo prometido médio de <b>{n1(v)} dias</b> na semana"
        if a is not None:
            t += f" ({dif(v - a)} vs semana anterior)"
        if mf is not None:
            t += f"; média dos meses fechados: {n1(mf)}"
        dest.append(t + ".")
    es, ea = sem["ent"], ant["ent"]
    if es["n"]:
        t = f"<b>Entregas reais ML</b>: {es['n']} pedidos entregues, <b>{pct(es['pct'])} no prazo</b>"
        if ea["pct"] is not None and es["pct"] is not None:
            t += f" ({dif((es['pct'] - ea['pct']) * 100, 0, inverso=True)} p.p. vs semana anterior)"
        t += f"; prazo real médio {n1(es['real'])} dias (prometido {n1(es['prom'])})."
        dest.append(t)
    # estados que mais pioraram
    pioras = []
    for c in canais:
        for u in sorted({r[2] for r in sem["linhas"]}):
            v, a = M.prometido(sem["linhas"], uf=u).get(c), M.prometido(ant["linhas"], uf=u).get(c)
            if v is not None and a is not None and v - a >= 1:
                pioras.append((v - a, u, c))
    pioras.sort(reverse=True)
    if pioras:
        dest.append("Maiores altas de prazo na semana: " + ", ".join(
            f"<b>{u}</b> ({e(c)} +{n1(d)})" for d, u, c in pioras[:5]) + ".")
    pagos = M.frete_pago(sem["linhas"])
    for c, lst in pagos.items():
        dest.append(f"<b>{e(c)}</b>: frete pago pelo comprador em {len(lst)} dos 54 destinos (" +
                    ", ".join(f"{u} {rg}" for u, rg, _ in lst[:6]) + ("…" if len(lst) > 6 else "") + ").")

    corpo = h2("Destaques") + lista(dest or ["Ainda não há dados suficientes para a semana."])

    # ---- tabela prometido
    cab = ["Prazo prometido (dias)", f"Semana {s1:%d/%m}–{s2:%d/%m}", "Semana anterior", rot_mes, col_media]
    linhas = []
    for c in canais:
        for rg, nome in ((None, ""), ("capital", " · capitais"), ("interior", " · interior")):
            f = lambda r: M.prometido(r["linhas"], rg).get(c)  # noqa: E731
            mf = [M.prometido(x["linhas"], rg).get(c) for _, x in fechados]
            mf = [v for v in mf if v is not None]
            v, a = f(sem), f(ant)
            linhas.append([(f"<b>{e(c)}</b>" if not rg else f'<span style="color:#52514e">&nbsp;&nbsp;{nome.strip(" ·")}</span>'),
                           f"{n1(v)} {dif(None if v is None or a is None else v - a)}" if v is not None else "–",
                           n1(a), n1(f(mes)), n1(sum(mf) / len(mf) if mf else None)])
    corpo += h2("Prazo prometido") + tabela(cab, linhas)

    # ---- tabela entregas
    if any(x["ent"]["n"] for x in (sem, ant, mes)) or any(f["ent"]["n"] for _, f in fechados):
        def mf_ent(k):
            v = [f["ent"][k] for _, f in fechados if f["ent"][k] is not None]
            return sum(v) / len(v) if v else None
        cab2 = ["Entregas reais · Mercado Livre", f"Semana {s1:%d/%m}–{s2:%d/%m}", "Semana anterior", rot_mes, col_media]
        L = [["Pedidos entregues", str(es["n"]), str(ea["n"]), str(mes["ent"]["n"]), (str(round(mf_ent("n"))) if mf_ent("n") is not None else "–")],
             ["No prazo", pct(es["pct"]), pct(ea["pct"]), pct(mes["ent"]["pct"]), pct(mf_ent("pct"))],
             ["Prazo real médio (dias)", n1(es["real"]), n1(ea["real"]), n1(mes["ent"]["real"]), n1(mf_ent("real"))],
             ["Prazo prometido na compra", n1(es["prom"]), n1(ea["prom"]), n1(mes["ent"]["prom"]), n1(mf_ent("prom"))],
             ["Atraso médio quando atrasa", n1(es["atraso_medio"]), n1(ea["atraso_medio"]), n1(mes["ent"]["atraso_medio"]), n1(mf_ent("atraso_medio"))]]
        corpo += h2("Entregas reais") + tabela(cab2, L)

    # ---- meses fechados
    if fechados:
        cab3 = ["Mês"] + [e(c) for c in canais] + ["Real ML", "No prazo", "Pedidos"]
        L = [[rot] + [n1(f["prom"].get(c)) for c in canais] + [n1(f["ent"]["real"]), pct(f["ent"]["pct"]), str(f["ent"]["n"] or "–")]
             for rot, f in fechados]
        L.append(["<b>Média</b>"] + [f"<b>{n1(media_f('prom', c))}</b>" for c in canais] +
                 [f"<b>{n1(media_f_ent)}</b>" if (media_f_ent := (lambda v: sum(v) / len(v) if v else None)(
                     [f['ent']['real'] for _, f in fechados if f['ent']['real'] is not None])) is not None else "–",
                  f"<b>{pct((lambda v: sum(v) / len(v) if v else None)([f['ent']['pct'] for _, f in fechados if f['ent']['pct'] is not None]))}</b>", ""])
        corpo += h2("Meses fechados") + '<p style="color:#52514e;font-size:12.5px;margin:0">Prazo prometido médio (dias) por canal e resultado das entregas reais.</p>' + tabela(cab3, L)

    # ---- estados mais lentos e mais atrasos
    ufs = sorted({r[2] for r in sem["linhas"]})
    if ufs and canais:
        rank = []
        for u in ufs:
            vs = [M.prometido(sem["linhas"], uf=u).get(c) for c in canais]
            rank.append((max(v for v in vs if v is not None), u, vs))
        rank.sort(reverse=True)
        cab4 = ["Estado"] + [e(c) for c in canais]
        corpo += h2("Estados com prazo mais longo na semana") + tabela(cab4, [[f"<b>{u}</b>"] + [n1(v) for v in vs] for _, u, vs in rank[:8]])
    atr = []
    for u in sorted({r[0] for r in conn.execute("SELECT DISTINCT uf FROM entregas WHERE data_entregue BETWEEN ? AND ?", (s1.isoformat(), s2.isoformat()))}):
        x = M.entregas(conn, s1, s2, uf=u)
        if x["nP"] >= 3 and x["pct"] < 1:
            atr.append((x["pct"], u, x))
    if atr:
        atr.sort()
        corpo += h2("Onde mais atrasou (entregas da semana)") + tabela(
            ["Estado", "No prazo", "Atrasos", "Prazo real médio"],
            [[f"<b>{u}</b>", pct(p), f"{x['atrasos']} de {x['nP']}", n1(x["real"])] for p, u, x in atr[:6]])

    assunto = f"Prazos de entrega · semana {s1:%d/%m} a {s2:%d/%m}"
    if es["pct"] is not None:
        assunto += f" · {pct(es['pct'])} no prazo"
    texto = assunto + "\n\n" + "\n".join(html.unescape(re.sub("<[^>]+>", "", d)) for d in dest)
    return enviar(cfg, assunto, moldura(f"Resumo da semana {s1:%d/%m} a {s2:%d/%m}",
                                        f"Mercado Livre{' e Shopee' if cfg.get('shopee', {}).get('ativo') else ''} · enviado em {hoje:%d/%m/%Y}",
                                        corpo, cfg.get("painel_url")), texto, log, teste, destino, "semanal")
