"""Cálculos compartilhados entre o painel e os e-mails."""
import datetime as dt
import statistics

SQL_DIARIO = """
WITH por_anuncio AS (      -- opção exibida ao comprador, por anúncio/CEP/dia
  SELECT data, marketplace,
         COALESCE(modalidade, CASE marketplace WHEN 'ml' THEN 'ML' ELSE 'Shopee' END) AS modalidade,
         anuncio_id, uf, regiao, MIN(dias_min) AS dmin, MIN(dias_max) AS dmax, MIN(frete) AS frete
  FROM consultas WHERE status = 'ok' AND data BETWEEN ? AND ?
  GROUP BY 1,2,3,4,5,6
)
SELECT data, CASE WHEN marketplace = 'shopee' THEN 'Shopee' WHEN modalidade = 'ML' THEN 'Mercado Livre'
            ELSE 'ML · ' || modalidade END AS canal,
       uf, regiao, AVG(dmin), AVG(dmax), AVG(frete)
FROM por_anuncio GROUP BY 1,2,3,4 ORDER BY 1
"""

MESES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]


def iso(d):
    return d.isoformat()


def diario(conn, d1, d2):
    """[(data, canal, uf, regiao, dmin, dmax, frete)] entre d1 e d2 (inclusive)."""
    return conn.execute(SQL_DIARIO, (iso(d1), iso(d2))).fetchall()


def ordenar_canais(canais):
    ordem = ["Full", "Coleta", "Agência", "Correios", "Flex", "Frete próprio", "ML"]
    def k(c):
        if c == "Shopee":
            return (99, c)
        if c == "Mercado Livre":
            return (0, c)
        m = c.replace("ML · ", "")
        return (ordem.index(m) if m in ordem else 50, c)
    return sorted(set(canais), key=k)


def prometido(linhas, regiao=None, uf=None, campo=4):
    """Média do prazo prometido por canal (cada estado/região/dia pesa igual)."""
    acc = {}
    for r in linhas:
        if (regiao and r[3] != regiao) or (uf and r[2] != uf) or r[campo] is None:
            continue
        acc.setdefault(r[1], []).append(r[campo])
    return {c: sum(v) / len(v) for c, v in acc.items()}


def frete_pago(linhas):
    """{canal: [(uf, regiao, frete)]} onde o frete ao comprador não é grátis (último dia do período)."""
    ult = {}
    for r in linhas:
        ult[(r[1], r[2], r[3])] = r
    out = {}
    for (c, u, rg), r in sorted(ult.items()):
        if r[6] and r[6] > 0:
            out.setdefault(c, []).append((u, rg, r[6]))
    return out


def entregas(conn, d1, d2, regiao=None, uf=None):
    sql = """SELECT dias_prometidos, dias_reais, no_prazo FROM entregas
             WHERE data_entregue BETWEEN ? AND ?"""
    p = [iso(d1), iso(d2)]
    if regiao:
        sql += " AND regiao = ?"
        p.append(regiao)
    if uf:
        sql += " AND uf = ?"
        p.append(uf)
    rs = conn.execute(sql, p).fetchall()
    com = [r for r in rs if r[2] is not None]
    atras = [r[1] - r[0] for r in com if r[2] == 0 and r[0] is not None]
    media = lambda v: sum(v) / len(v) if v else None  # noqa: E731
    return {
        "n": len(rs), "nP": len(com),
        "pct": (sum(r[2] for r in com) / len(com)) if com else None,
        "atrasos": sum(1 for r in com if r[2] == 0),
        "real": media([r[1] for r in rs]), "prom": media([r[0] for r in com if r[0] is not None]),
        "atraso_medio": media(atras),
    }


def semana_passada(hoje):
    """Segunda a domingo da semana anterior a `hoje`."""
    seg = hoje - dt.timedelta(days=hoje.weekday() + 7)
    return seg, seg + dt.timedelta(days=6)


def mes_label(d, parcial_ate=None):
    t = f"{MESES[d.month - 1]}/{d.year % 100:02d}"
    return t + (f" (até {parcial_ate:%d/%m})" if parcial_ate else "")


def meses_fechados(conn, hoje, n=6):
    """Até n meses completos antes do mês de `hoje` que tenham dados."""
    out, ini = [], hoje.replace(day=1)
    for _ in range(n):
        fim = ini - dt.timedelta(days=1)
        ini = fim.replace(day=1)
        # só conta como "mês fechado" se a coleta cobriu o mês (evita o 1º mês parcial)
        dias = conn.execute("SELECT COUNT(DISTINCT data) FROM consultas WHERE data BETWEEN ? AND ?",
                            (iso(ini), iso(fim))).fetchone()[0]
        if dias >= 20:
            out.append((ini, fim))
    return list(reversed(out))


def resumo_periodo(conn, d1, d2, regiao=None):
    linhas = diario(conn, d1, d2)
    return {"prom": prometido(linhas, regiao), "prom_max": prometido(linhas, regiao, campo=5),
            "ent": entregas(conn, d1, d2, regiao), "linhas": linhas}


def mensal(conn, hoje, n=6):
    """Linhas do resumo mensal: meses fechados + mês atual parcial."""
    ontem = hoje - dt.timedelta(days=1)
    per = [(a, b, mes_label(a)) for a, b in meses_fechados(conn, hoje, n)]
    if ontem >= hoje.replace(day=1):
        per.append((hoje.replace(day=1), ontem, mes_label(hoje, parcial_ate=ontem)))
    elif not per or per[-1][0] != ontem.replace(day=1):
        pass
    return [(rot, resumo_periodo(conn, a, b)) for a, b, rot in per]


def normal_por_ponto(conn, hoje, janela=28, minimo=7):
    """Prazo 'normal' de cada canal/UF/região: mediana dos `janela` dias anteriores a hoje."""
    linhas = diario(conn, hoje - dt.timedelta(days=janela), hoje - dt.timedelta(days=1))
    acc = {}
    for d, c, u, rg, dmin, dmax, fr in linhas:
        acc.setdefault((c, u, rg), []).append((dmin, fr))
    return {k: {"dias": statistics.median(x[0] for x in v),
                "frete": statistics.median((x[1] or 0) for x in v), "n": len(v)}
            for k, v in acc.items() if len(v) >= minimo}
