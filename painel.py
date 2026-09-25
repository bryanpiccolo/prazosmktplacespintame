"""Gera painel.html (autocontido, funciona offline) a partir do histórico."""
import datetime as dt
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent
CHARTJS = BASE / "vendor" / "chart.umd.min.js"
CDN = '<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>'

SQL_PROMETIDO = """
WITH por_anuncio AS (      -- opção exibida ao comprador, por anúncio/CEP/dia
  SELECT data, marketplace, COALESCE(modalidade, CASE marketplace WHEN 'ml' THEN 'ML' ELSE 'Shopee' END) AS modalidade,
         anuncio_id, uf, regiao, MIN(dias_min) AS dmin, MIN(dias_max) AS dmax
  FROM consultas WHERE status = 'ok' AND data >= ?
  GROUP BY 1,2,3,4,5,6
)
SELECT data, CASE WHEN marketplace = 'shopee' THEN 'Shopee' WHEN modalidade = 'ML' THEN 'Mercado Livre'
            ELSE 'ML · ' || modalidade END AS canal,
       uf, regiao, ROUND(AVG(dmin), 2), ROUND(AVG(dmax), 2)
FROM por_anuncio GROUP BY 1,2,3,4 ORDER BY 1
"""

SQL_SAUDE = """
SELECT marketplace, data, SUM(ok), COUNT(*) FROM (
  SELECT marketplace, data, anuncio_id, uf, regiao, MAX(status = 'ok') AS ok
  FROM consultas WHERE data = (SELECT MAX(data) FROM consultas c2 WHERE c2.marketplace = consultas.marketplace)
  GROUP BY 1,2,3,4,5
) GROUP BY 1,2
"""


def _r(v, c=2):
    return None if v is None else round(v, c)


def _ent(x, publico, minimo=3):
    """Resumo de entregas; no modo público não expõe quantidades de pedidos."""
    if not x["n"]:
        return None
    ok = x["nP"] >= minimo
    d = {"real": _r(x["real"]), "prom": _r(x["prom"]), "pct": _r(x["pct"], 4) if ok else None}
    if not publico:
        d.update(n=x["n"], nP=x["nP"], atrasos=x["atrasos"])
    return d


def _entregas(conn, hoje, publico):
    import metricas as M
    lim = hoje - dt.timedelta(days=30)
    ufs = [r[0] for r in conn.execute("SELECT DISTINCT uf FROM entregas WHERE data_entregue >= ?", (lim.isoformat(),))]
    out = {"tem": bool(ufs)}
    for reg in ("ambas", "capital", "interior"):
        rg = None if reg == "ambas" else reg
        sem = []
        seg = hoje - dt.timedelta(days=hoje.weekday())
        for i in range(16, -1, -1):
            a = seg - dt.timedelta(days=7 * i)
            x = M.entregas(conn, a, a + dt.timedelta(days=6), rg)
            if x["nP"]:
                sem.append([a.isoformat(), _r(x["pct"], 4)] + ([] if publico else [x["nP"]]))
        out[reg] = {"nac": _ent(M.entregas(conn, lim, hoje, rg), publico, 10),
                    "uf": {u: _ent(M.entregas(conn, lim, hoje, rg, u), publico) for u in ufs},
                    "sem": sem}
    return out


def _mensal(conn, hoje, publico):
    import metricas as M
    out = []
    for rot, r in M.mensal(conn, hoje):
        ent = _ent(r["ent"], publico, 10)
        out.append({"mes": rot, "prom": {c: _r(v) for c, v in r["prom"].items()},
                    "pmax": {c: _r(v) for c, v in r["prom_max"].items()}, "ent": ent})
    return out


def _dados(conn, dias, publico):
    hoje = dt.date.today()
    corte = (hoje - dt.timedelta(days=dias)).isoformat()
    return {
        "gerado": dt.datetime.now().strftime("%d/%m/%Y às %H:%M"),
        "publico": publico,
        "prom": conn.execute(SQL_PROMETIDO, (corte,)).fetchall(),
        # link público: só prazos — nenhum dado derivado de vendas
        "E": {"tem": False} if publico else _entregas(conn, hoje, publico),
        "mensal": [dict(m, ent=None) for m in _mensal(conn, hoje, publico)] if publico else _mensal(conn, hoje, publico),
        "saude": conn.execute(SQL_SAUDE).fetchall(),
    }


def gerar(conn, destino, dias=180, publico=False):
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    dados = json.dumps(_dados(conn, dias, publico), ensure_ascii=False, separators=(",", ":"))
    chart = f"<script>{CHARTJS.read_text(encoding='utf-8')}</script>" if CHARTJS.exists() else CDN
    html = HTML.replace("__CHARTJS__", chart).replace("__DADOS__", dados.replace("</", "<\\/"))
    destino.write_text(html, encoding="utf-8")
    return destino


HTML = r"""<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Prazos de Entrega</title>
__CHARTJS__
<style>
:root{color-scheme:light;--bg:#f4f3ef;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#7a7974;--line:#e4e2dc;
--s1:#2a78d6;--s2:#eb6834;--s3:#1baf7a;--s4:#4a3aa7;--s5:#e87ba4;--s6:#008300;
--good:#0ca30c;--goodtxt:#006300;--crit:#d03b3b;--chip:#efede8;
--h0:#cde2fb;--h1:#9ec5f4;--h2:#6da7ec;--h3:#3987e5;--h4:#256abf;--h5:#184f95}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme="light"])){color-scheme:dark;--bg:#121211;--surface:#1a1a19;--ink:#fff;
--ink2:#c3c2b7;--muted:#8f8e86;--line:#2e2e2b;--s1:#3987e5;--s2:#d95926;--s3:#199e70;--s4:#9085e9;--s5:#d55181;--s6:#008300;
--goodtxt:#3ecf3e;--chip:#262624}}
:root[data-theme="dark"]{color-scheme:dark;--bg:#121211;--surface:#1a1a19;--ink:#fff;--ink2:#c3c2b7;--muted:#8f8e86;--line:#2e2e2b;
--s1:#3987e5;--s2:#d95926;--s3:#199e70;--s4:#9085e9;--s5:#d55181;--s6:#008300;--goodtxt:#3ecf3e;--chip:#262624}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
main{max-width:1200px;margin:0 auto;padding:28px 16px 56px}
header{display:flex;justify-content:space-between;align-items:flex-end;gap:16px;flex-wrap:wrap;margin-bottom:18px}
h1{font-size:24px;margin:0;letter-spacing:-.015em}
.sub{color:var(--ink2);font-size:13px}
.filtros{display:flex;gap:8px;flex-wrap:wrap}
select{background:var(--surface);color:var(--ink);border:1px solid var(--line);border-radius:8px;padding:7px 10px;font:inherit;max-width:100%}
.card{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:18px 20px;margin-bottom:14px}
h2{font-size:15px;margin:0 0 4px;font-weight:650}
.h2sub{color:var(--ink2);font-size:12.5px;margin:0 0 14px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-bottom:14px}
.kpi{margin:0}
.kpi .rot{color:var(--ink2);font-size:12.5px;display:flex;align-items:center;gap:6px}
.kpi .val{font-size:30px;font-weight:680;letter-spacing:-.02em;margin-top:4px}
.kpi .val small{font-size:14px;font-weight:450;color:var(--ink2);margin-left:3px}
.kpi .det{font-size:12.5px;color:var(--ink2)}
.dot{width:10px;height:10px;border-radius:3px;display:inline-block;flex:none;margin-right:6px;vertical-align:-1px}
.piora{color:var(--crit);font-weight:600}.melhora{color:var(--goodtxt);font-weight:600}
.insights{list-style:none;margin:0;padding:0;display:grid;gap:10px}
.insights li{display:flex;gap:10px;align-items:flex-start;line-height:1.45}
.insights .ic{flex:none;width:22px;height:22px;border-radius:6px;display:grid;place-items:center;font-size:12px;font-weight:700;background:var(--chip);color:var(--ink2)}
.insights .ic.alerta{background:color-mix(in srgb,var(--crit) 16%,transparent);color:var(--crit)}
.insights .ic.bom{background:color-mix(in srgb,var(--good) 16%,transparent);color:var(--goodtxt)}
.grid2{display:grid;grid-template-columns:1fr;gap:14px}
@media(min-width:960px){.grid2{grid-template-columns:3fr 2fr}}
.grid2 .card{margin-bottom:0}
.graf{position:relative;height:280px}
.tabela{overflow-x:auto;margin:0 -4px}
table{border-collapse:separate;border-spacing:0;width:100%;font-size:13px}
th,td{padding:5px 8px;text-align:right;white-space:nowrap}
td{border-top:1px solid var(--line)}
th{color:var(--ink2);font-weight:550;font-size:12px;cursor:pointer;user-select:none;vertical-align:bottom}
th:hover{color:var(--ink)}
th:first-child,td:first-child{text-align:left}
th .dot{margin-right:5px;vertical-align:-1px}
.cel{display:inline-block;min-width:44px;text-align:center;border-radius:6px;padding:2px 6px;font-variant-numeric:tabular-nums;font-weight:550}
.d{font-size:11px;margin-left:4px;display:inline-block;min-width:34px;text-align:left}
.leg{display:flex;align-items:center;gap:6px;font-size:12px;color:var(--ink2);margin-top:10px;flex-wrap:wrap}
.leg .sw{width:18px;height:10px;border-radius:2px}
.vazio{color:var(--ink2);padding:24px;text-align:center}
.rodape{color:var(--muted);font-size:12px;margin-top:18px;line-height:1.6}
.sep{border-left:1px solid var(--line)}
tr.reg td{padding:14px 8px 6px;font-size:11.5px;font-weight:650;letter-spacing:.04em;text-transform:uppercase;color:var(--ink2);border-top:none;text-align:left}
</style>
</head>
<body>
<main>
<header>
  <div>
    <h1>Prazos de entrega</h1>
    <div class="sub"><span id="mkts">Mercado Livre</span> · 27 estados · atualizado em <span id="gerado"></span></div>
  </div>
  <div class="filtros">
    <select id="f-reg" aria-label="Região"><option value="ambas">Capital e interior (média)</option><option value="capital">Só capitais</option><option value="interior">Só interior</option></select>
    <select id="f-met" aria-label="Prazo"><option value="min">Prazo prometido (1ª data)</option><option value="max">Prazo prometido (última data)</option></select>
    <select id="f-dias" aria-label="Período do gráfico"><option value="30">Gráfico: 30 dias</option><option value="90" selected>Gráfico: 90 dias</option><option value="100000">Gráfico: tudo</option></select>
  </div>
</header>

<section class="kpis" id="kpis"></section>

<section class="card">
  <h2>Pontos de atenção</h2>
  <p class="h2sub">Gerado automaticamente a partir da última coleta, comparando com 7 dias antes.</p>
  <ul class="insights" id="insights"></ul>
</section>

<section class="card">
  <h2>Prazo por estado</h2>
  <p class="h2sub">Dias corridos entre a compra e a entrega prometida. Quanto mais escuro, mais demorado. ▲/▼ = variação em 7 dias. Estados agrupados por região, na ordem de importância de vendas do e-commerce. Clique no título de uma coluna para ordenar.</p>
  <div class="tabela" id="t-uf"></div>
  <div class="leg" id="leg-heat"></div>
</section>

<section class="grid2">
  <div class="card">
    <h2>Evolução do prazo prometido</h2>
    <p class="h2sub">Média dos 27 estados, em dias.</p>
    <div class="graf"><canvas id="g-linha" aria-label="Evolução do prazo prometido por canal"></canvas></div>
  </div>
  <div class="card" id="card-entregas">
    <h2>Entregas no prazo — Mercado Livre</h2>
    <p class="h2sub">Pedidos reais entregues até a data prometida, por semana.</p>
    <div class="graf"><canvas id="g-entregas" aria-label="Percentual de entregas no prazo por semana"></canvas></div>
  </div>
</section>

<section class="card" style="margin-top:14px">
  <h2>Resumo mensal</h2>
  <p class="h2sub" id="mes-sub">Prazo prometido médio (dias, média dos 27 estados, capital e interior).</p>
  <div class="tabela" id="t-mes"></div>
</section>

<div class="rodape" id="rodape"></div>
</main>

<script>
const D = __DADOS__;
const $ = id => document.getElementById(id);
const css = v => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const num = (v, c=1) => v==null||isNaN(v) ? "–" : v.toFixed(c).replace(".", ",");
const fmt = d => d.slice(8,10)+"/"+d.slice(5,7);
const menos = (d,n) => { const x=new Date(d+"T12:00:00"); x.setDate(x.getDate()-n); return x.toISOString().slice(0,10); };
const media = a => { const v=a.filter(x=>x!=null&&!isNaN(x)); return v.length ? v.reduce((s,x)=>s+x,0)/v.length : null; };
const esc = s => String(s).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
$("gerado").textContent = D.gerado;

// ---------- canais (cor fixa por canal, nunca por posição)
const ORDEM_ML = ["Full","Coleta","Agência","Correios","Flex","Frete próprio","ML"];
const SLOTS_ML = ["--s1","--s3","--s4","--s5","--s6"];
const canais = [...new Set(D.prom.map(r=>r[1]))].sort((a,b) => {
  const k = c => c==="Shopee" ? 99 : c==="Mercado Livre" ? 0 : (ORDEM_ML.indexOf(c.replace("ML · ",""))+1 || 50);
  return k(a)-k(b) || a.localeCompare(b);
});
const COR = {}; let iml = 0;
canais.forEach(c => COR[c] = c==="Shopee" ? "--s2" : SLOTS_ML[Math.min(iml++, SLOTS_ML.length-1)]);
const canaisML = canais.filter(c => c!=="Shopee");  // inclui "Mercado Livre" (modo sem login)
$("mkts").textContent = canais.includes("Shopee") ? (canaisML.length ? "Mercado Livre e Shopee" : "Shopee") : "Mercado Livre";
// Ordem por importância de vendas no e-commerce: regiões (Sudeste, Sul, Nordeste,
// Centro-Oeste, Norte) e, dentro delas, estados do maior para o menor peso nas compras online.
const REGIOES = [["Sudeste",["SP","MG","RJ","ES"]], ["Sul",["PR","RS","SC"]],
  ["Nordeste",["BA","PE","CE","RN","PB","AL","MA","PI","SE"]], ["Centro-Oeste",["DF","GO","MT","MS"]],
  ["Norte",["PA","AM","TO","RO","AC","AP","RR"]]];
const POS = {}, REG = {}; let _p = 0;
REGIOES.forEach(([r, us]) => us.forEach(u => { POS[u] = _p++; REG[u] = r; }));
const UFS = [...new Set(D.prom.map(r=>r[2]))].sort((a,b) => (POS[a]??99) - (POS[b]??99) || a.localeCompare(b));

// ---------- índice: data|canal|uf|regiao -> [dmin,dmax]
const IDX = {}, DATAS = {};
D.prom.forEach(([d,c,u,r,a,b]) => { IDX[d+"|"+c+"|"+u+"|"+r] = [a,b]; (DATAS[c] = DATAS[c]||new Set()).add(d); });
Object.keys(DATAS).forEach(c => DATAS[c] = [...DATAS[c]].sort());
const ultima = c => DATAS[c] ? DATAS[c][DATAS[c].length-1] : null;
const dataRef = (c, alvo, folga=3) => { // data coletada mais próxima <= alvo (até `folga` dias antes)
  const ds = (DATAS[c]||[]).filter(d => d<=alvo && d>=menos(alvo,folga)); return ds.length ? ds[ds.length-1] : null; };

function val(F, c, uf, d){
  if (!d) return null;
  const i = F.met==="min" ? 0 : 1;
  const regs = F.reg==="ambas" ? ["capital","interior"] : [F.reg];
  const ufs = uf ? [uf] : UFS;
  const porUF = ufs.map(u => media(regs.map(r => (IDX[d+"|"+c+"|"+u+"|"+r]||[])[i])));
  return media(porUF);
}
const agora = (F,c,uf) => val(F,c,uf,ultima(c));
const antes = (F,c,uf) => { const u=ultima(c); return u ? val(F,c,uf,dataRef(c,menos(u,7))) : null; };
const delta = (F,c,uf) => { const a=agora(F,c,uf), b=antes(F,c,uf); return a==null||b==null ? null : a-b; };

// ---------- entregas reais (últimos 30 dias)
// pré-calculadas no Python; no link público não há quantidade de pedidos
const PUB = !!D.publico, TEM_ENT = !!(D.E && D.E.tem);
const VAZIO = {real:null, prom:null, pct:null};
function entregas(F, uf){
  const g = (D.E||{})[F.reg]; if (!g) return VAZIO;
  return (uf ? g.uf[uf] : g.nac) || VAZIO;
}

const seta = (dif, casas=1) => dif==null||Math.abs(dif)<0.05 ? "" :
  `<span class="${dif>0?"piora":"melhora"}">${dif>0?"▲ +":"▼ −"}${num(Math.abs(dif),casas)}</span>`;
const nomeCanal = c => `<span class="dot" style="background:${css(COR[c])}"></span>${esc(c)}`;

// ---------- render
let gL, gE, ordem = {col: 0, asc: true};
const F = () => ({reg: $("f-reg").value, met: $("f-met").value, dias: +$("f-dias").value});

function kpis(F){
  const h = canais.map(c => {
    const v = agora(F,c), d = delta(F,c);
    return `<div class="card kpi"><div class="rot">${nomeCanal(c)}</div>
      <div class="val">${num(v)}<small>dias</small></div>
      <div class="det">${d==null ? "prazo médio prometido" : `${seta(d)||"estável"} vs 7 dias antes`}</div></div>`;
  });
  const e = entregas(F);
  if (TEM_ENT){
    h.push(`<div class="card kpi"><div class="rot">Entregas ML no prazo · 30 dias</div>
      <div class="val">${e.pct==null?"–":Math.round(e.pct*100)+"%"}</div>
      <div class="det">${PUB || e.nP==null ? "pedidos reais entregues" : `${e.atrasos} atrasos em ${e.nP} pedidos`}</div></div>`);
    h.push(`<div class="card kpi"><div class="rot">Prazo real ML · 30 dias</div>
      <div class="val">${num(e.real)}<small>dias</small></div>
      <div class="det">prometido: ${num(e.prom)} dias</div></div>`);
  }
  $("kpis").innerHTML = h.join("") || `<div class="card vazio">Ainda não há coletas.</div>`;
}

function insights(F){
  const L = [], add = (tipo, txt) => L.push(`<li><span class="ic ${tipo}">${tipo==="alerta"?"!":tipo==="bom"?"✓":"i"}</span><span>${txt}</span></li>`);
  // 1. tendência nacional por canal
  canais.forEach(c => { const d = delta(F,c);
    if (d!=null && Math.abs(d)>=0.3) add(d>0?"alerta":"bom", `<b>${esc(c)}</b>: o prazo médio ${d>0?"piorou":"melhorou"} <b>${num(Math.abs(d))} dia${Math.abs(d)>=1.95?"s":""}</b> na última semana (agora ${num(agora(F,c))} dias).`); });
  // 2. estados que pioraram
  const pioras = [];
  canais.forEach(c => UFS.forEach(u => { const d = delta(F,c,u); if (d!=null && d>=1) pioras.push([d,u,c]); }));
  pioras.sort((a,b)=>b[0]-a[0]);
  if (pioras.length) add("alerta", `Prazo aumentou 1 dia ou mais em 7 dias: ${pioras.slice(0,6).map(([d,u,c])=>`<b>${u}</b> (${esc(c)} +${num(d)})`).join(", ")}${pioras.length>6?` e mais ${pioras.length-6}`:""}.`);
  // 3. ML x Shopee
  if (canaisML.length && canais.includes("Shopee")){
    let ml=[], sh=[];
    UFS.forEach(u => { const m = Math.min(...canaisML.map(c=>agora(F,c,u)??Infinity)), s = agora(F,"Shopee",u);
      if (isFinite(m) && s!=null){ if (s-m>=1) ml.push([s-m,u]); else if (m-s>=1) sh.push([m-s,u]); } });
    ml.sort((a,b)=>b[0]-a[0]); sh.sort((a,b)=>b[0]-a[0]);
    const t = [];
    if (ml.length) t.push(`o <b>Mercado Livre</b> entrega pelo menos 1 dia mais rápido em <b>${ml.length} estado${ml.length>1?"s":""}</b> (maior diferença: ${ml[0][1]}, ${num(ml[0][0])} dias)`);
    if (sh.length) t.push(`a <b>Shopee</b> é mais rápida em <b>${sh.length}</b> (${sh.slice(0,4).map(x=>x[1]).join(", ")})`);
    if (t.length) add("", (t.join("; ")).replace(/^o /,"O ") + ". Onde um canal é bem mais lento, vale reforçar o outro nos anúncios e campanhas.");
    else add("", "Mercado Livre e Shopee prometem prazos parecidos (menos de 1 dia de diferença) em todos os estados.");
  }
  // 4. Full x demais modalidades
  const full = canaisML.find(c=>c.endsWith("Full")), outra = canaisML.find(c=>!c.endsWith("Full"));
  if (full && outra){
    const g = media(UFS.map(u => { const a=agora(F,outra,u), b=agora(F,full,u); return a==null||b==null?null:a-b; }));
    let top = null; UFS.forEach(u => { const a=agora(F,outra,u), b=agora(F,full,u); if(a!=null&&b!=null&&(!top||a-b>top[0])) top=[a-b,u]; });
    if (g!=null) add(g>0?"bom":"", `<b>Full</b> entrega em média <b>${num(Math.abs(g))} dia${Math.abs(g)>=1.95?"s":""} ${g>0?"mais rápido":"mais devagar"}</b> que ${esc(outra.replace("ML · ",""))}${top&&top[0]>=1?` — maior ganho em ${top[1]} (${num(top[0])} dias)`:""}. Use isso para decidir quais produtos levar ao Full.`);
  }
  // 5. estados mais lentos, mesmo no melhor canal
  const lentos = UFS.map(u => [Math.min(...canais.map(c=>agora(F,c,u)??Infinity)), u]).filter(x=>isFinite(x[0])).sort((a,b)=>b[0]-a[0]);
  if (lentos.length && lentos[0][0]>=7) add("", `Prazos mais longos mesmo no canal mais rápido: ${lentos.filter(x=>x[0]>=7).slice(0,5).map(([v,u])=>`<b>${u}</b> ${num(v)} dias`).join(", ")}.`);
  // 6. entregas reais
  const e = entregas(F);
  if (e.pct != null){
    const porUF = UFS.map(u => [u, entregas(F,u)]).filter(([,x]) => x.pct!=null && x.pct<1).sort((a,b)=>a[1].pct-b[1].pct);
    const dif = e.real!=null&&e.prom!=null ? e.real-e.prom : null;
    add(e.pct<0.9?"alerta":"bom", `<b>${Math.round(e.pct*100)}%</b> das entregas do ML nos últimos 30 dias chegaram até a data prometida${dif!=null?`; na média, chegaram <b>${num(Math.abs(dif))} dia${Math.abs(dif)>=1.95?"s":""} ${dif<=0?"antes":"depois"}</b> do prometido`:""}.${porUF.length?` Mais atrasos: ${porUF.slice(0,4).map(([u,x])=>`<b>${u}</b> (${PUB||x.nP==null ? Math.round(x.pct*100)+"% no prazo" : `${x.atrasos} de ${x.nP}`})`).join(", ")}.`:""}`);
  } else if (!TEM_ENT && !PUB) add("", "Os pedidos reais do Mercado Livre (prometido × entregue) aparecem aqui assim que as primeiras entregas forem registradas.");
  $("insights").innerHTML = L.join("") || `<li class="vazio">Sem variações relevantes. Com mais dias de coleta, este quadro fica mais rico.</li>`;
}

function tabela(F){
  const RAMP = ["--h0","--h1","--h2","--h3","--h4","--h5"];
  const todos = []; canais.forEach(c => UFS.forEach(u => { const v=agora(F,c,u); if(v!=null) todos.push(v); }));
  const mn = Math.min(...todos), mx = Math.max(...todos);
  const bin = v => Math.max(0, Math.min(5, Math.floor((v-mn)/Math.max(0.01,mx-mn)*6)));
  const temEnt = TEM_ENT;
  const linhas = UFS.map(u => {
    const vs = canais.map(c => agora(F,c,u));
    const ord = canais.map((c,i)=>[vs[i],c]).filter(x=>x[0]!=null).sort((a,b)=>a[0]-b[0]);
    const e = entregas(F,u);
    return {u, vs, ds: canais.map(c=>delta(F,c,u)), melhor: ord[0], margem: ord.length>1 ? ord[1][0]-ord[0][0] : null, e};
  });
  const chave = [r=>POS[r.u]??99, ...canais.map((c,i)=>r=>r.vs[i]??-1), r=>r.melhor?r.melhor[0]:99, r=>r.e.real??-1, r=>r.e.pct??2];
  linhas.sort((a,b) => { const k=chave[ordem.col]||chave[0], x=k(a), y=k(b); return (x<y?-1:x>y?1:0)*(ordem.asc?1:-1); });
  const comp = canais.length > 1;   // "Mais rápido" só faz sentido com mais de um canal
  const cab = ["Estado (por região)", ...canais.map(nomeCanal), ...(comp?["Mais rápido"]:[]), ...(temEnt?["Real ML*","No prazo*"]:[])];
  let h = `<table><thead><tr>${cab.map((t,i)=>`<th data-col="${i}" class="${i===canais.length+1?"sep":""}">${t}${ordem.col===i?(ordem.asc?" ↑":" ↓"):""}</th>`).join("")}</tr></thead><tbody>`;
  const agrupar = ordem.col === 0 && ordem.asc;
  let regAtual = null;
  linhas.forEach(r => {
    if (agrupar && REG[r.u] !== regAtual){
      regAtual = REG[r.u];
      h += `<tr class="reg"><td colspan="${cab.length}">${esc(regAtual || "Outros")}</td></tr>`;
    }
    h += `<tr><td><b>${r.u}</b></td>` + r.vs.map((v,i) => { if (v==null) return "<td>–</td>";
      const b = bin(v); return `<td><span class="cel" style="background:var(${RAMP[b]});color:${b>=3?"#fff":"#0b0b0b"}">${num(v)}</span><span class="d">${seta(r.ds[i])}</span></td>`; }).join("");
    if (comp) h += `<td class="sep">${r.melhor ? (r.margem!=null&&r.margem<0.5 ? `<span style="color:var(--ink2)">empate</span>` : `${nomeCanal(r.melhor[1])}${r.margem!=null?` <span style="color:var(--ink2)">(${num(r.margem)} d à frente)</span>`:""}`) : "–"}</td>`;
    if (temEnt) h += `<td>${r.e.real!=null?num(r.e.real):"–"}</td><td>${r.e.pct!=null?`<span class="${r.e.pct<0.85?"piora":""}">${r.e.pct<0.85?"⚠ ":""}${Math.round(r.e.pct*100)}%</span>${PUB||r.e.nP==null?"":` <span style="color:var(--muted)">(${r.e.nP})</span>`}`:"–"}</td>`;
    h += "</tr>";
  });
  $("t-uf").innerHTML = h + "</tbody></table>";
  document.querySelectorAll("#t-uf th").forEach(th => th.onclick = () => {
    const c = +th.dataset.col; ordem = {col:c, asc: ordem.col===c ? !ordem.asc : c===0}; tabela(F); });
  const passo = (mx-mn)/6;
  $("leg-heat").innerHTML = todos.length ? `<span>Mais rápido</span>` + RAMP.map((v,i)=>`<span class="sw" style="background:var(${v})" title="${num(mn+passo*i)}–${num(mn+passo*(i+1))} dias"></span>`).join("") +
    `<span>Mais lento</span><span style="margin-left:10px">(${num(mn)} a ${num(mx)} dias)</span>` + (temEnt?`<span style="margin-left:auto">* pedidos reais entregues nos últimos 30 dias${PUB?"":" (entre parênteses, nº de pedidos)"}</span>`:"") : "";
}

function graficos(F){
  const eixo = {ticks:{color:css("--ink2")}, grid:{color:css("--line")}, border:{display:false}};
  const lim = menos(new Date().toISOString().slice(0,10), F.dias);
  const datas = [...new Set(canais.flatMap(c => (DATAS[c]||[]).filter(d=>d>=lim)))].sort();
  gL && gL.destroy();
  gL = new Chart($("g-linha"), {type:"line",
    data:{labels: datas.map(fmt), datasets: canais.map(c => ({label:c, data: datas.map(d => (DATAS[c]||[]).includes(d) ? val(F,c,null,d) : null),
      borderColor: css(COR[c]), backgroundColor: css(COR[c]), borderWidth:2, tension:.25, spanGaps:true,
      pointRadius: datas.length>30?0:3, pointHoverRadius:5}))},
    options:{responsive:true, maintainAspectRatio:false, interaction:{mode:"index",intersect:false},
      scales:{y:{...eixo, beginAtZero:true, title:{display:true,text:"dias",color:css("--ink2")}}, x:{...eixo, grid:{display:false}, ticks:{color:css("--ink2"),maxRotation:0,autoSkipPadding:12}}},
      plugins:{legend:{labels:{color:css("--ink"),boxWidth:12,boxHeight:12}}, tooltip:{callbacks:{label:x=>` ${x.dataset.label}: ${num(x.parsed.y)} dias`}}}}});

  // entregas por semana (segunda-feira)
  if (!$("g-entregas")) return;
  const sem = ((D.E||{})[F.reg]||{}).sem || [];
  gE && gE.destroy();
  if (!sem.length){ $("g-entregas").parentElement.innerHTML = `<div class="vazio">Ainda sem entregas registradas.</div>`; return; }
  gE = new Chart($("g-entregas"), {type:"line",
    data:{labels: sem.map(k=>"sem. "+fmt(k[0])), datasets:[{label:"% no prazo", data: sem.map(k=>k[1]*100),
      borderColor: css("--s1"), backgroundColor: css("--s1"), borderWidth:2, tension:.25, pointRadius:3}]},
    options:{responsive:true, maintainAspectRatio:false,
      scales:{y:{...eixo, min:0, max:100, ticks:{color:css("--ink2"), callback:v=>v+"%"}}, x:{...eixo, grid:{display:false}, ticks:{color:css("--ink2"),maxRotation:0,autoSkipPadding:12}}},
      plugins:{legend:{display:false}, tooltip:{callbacks:{label:x=>` ${Math.round(x.parsed.y)}% no prazo${sem[x.dataIndex][2]?` (${sem[x.dataIndex][2]} pedidos)`:""}`}}}}});
}

function rodape(){
  const nome = {ml:"Mercado Livre", shopee:"Shopee"};
  $("rodape").innerHTML = "Última coleta: " + (D.saude.map(([m,d,ok,t]) => `${nome[m]||m} em ${fmt(d)} — ${ok} de ${t} consultas com prazo`).join(" · ") || "nenhuma") +
    "<br>Cada canal é medido em anúncios representativos (os mais vendidos de cada modalidade de envio), simulando entrega para uma capital e uma cidade do interior de cada estado. " +
    "O prazo considerado é o da opção de frete que o marketplace exibe ao comprador.";
}

function mensal(){
  const M = D.mensal || [];
  if (!M.length){ $("t-mes").innerHTML = `<div class="vazio">O resumo mensal aparece depois do primeiro mês de coleta.</div>`; return; }
  const i = $("f-met").value==="min" ? "prom" : "pmax";
  const temE = M.some(m=>m.ent);
  const fech = M.filter(m=>!/até/.test(m.mes));
  const med = (f) => media(fech.map(f));
  let h = `<table><thead><tr><th>Mês</th>${canais.map(c=>`<th>${nomeCanal(c)}</th>`).join("")}${temE?`<th class="sep">Real ML</th><th>No prazo</th>${PUB?"":"<th>Pedidos</th>"}`:""}</tr></thead><tbody>`;
  M.forEach(m => { h += `<tr><td>${esc(m.mes)}</td>${canais.map(c=>`<td>${num(m[i][c])}</td>`).join("")}` +
    (temE?`<td class="sep">${num(m.ent&&m.ent.real)}</td><td>${m.ent&&m.ent.pct!=null?Math.round(m.ent.pct*100)+"%":"–"}</td>${PUB?"":`<td>${m.ent&&m.ent.n||"–"}</td>`}`:"") + "</tr>"; });
  if (fech.length > 1) h += `<tr><td><b>Média dos meses fechados</b></td>${canais.map(c=>`<td><b>${num(med(m=>m[i][c]))}</b></td>`).join("")}` +
    (temE?`<td class="sep"><b>${num(med(m=>m.ent&&m.ent.real))}</b></td><td><b>${(v=>v==null?"–":Math.round(v*100)+"%")(med(m=>m.ent&&m.ent.pct))}</b></td>${PUB?"":"<td></td>"}`:"") + "</tr>";
  $("t-mes").innerHTML = h + "</tbody></table>";
}

if (!TEM_ENT){ const c = $("card-entregas"); c.parentElement.style.gridTemplateColumns = "1fr"; c.remove(); }
function render(){ const f = F(); kpis(f); insights(f); tabela(f); graficos(f); mensal(); }
document.querySelectorAll(".filtros select").forEach(s => s.addEventListener("change", render));
rodape();
if (!D.prom.length) document.querySelector("main").insertAdjacentHTML("beforeend", `<div class="card vazio">Ainda não há coletas. Rode <code>python prazos.py coletar</code>.</div>`);
else if (window.Chart) render();
else { kpis(F()); insights(F()); tabela(F()); mensal(); }
</script>
</body>
</html>
"""
