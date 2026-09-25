"""Histórico local em SQLite."""
import sqlite3

COLUNAS = [
    "coletado_em", "data", "marketplace", "modalidade", "anuncio_id", "anuncio_titulo",
    "uf", "regiao", "cidade", "cep", "opcao", "dias_min", "dias_max",
    "data_entrega_min", "data_entrega_max", "frete", "status", "erro", "bruto",
]

COLUNAS_ENTREGAS = [
    "envio_id", "pedido_id", "marketplace", "modalidade", "data_pedido", "data_prometida",
    "data_entregue", "uf", "cidade", "regiao", "dias_prometidos", "dias_reais", "no_prazo",
    "atualizado_em",
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS consultas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    coletado_em TEXT NOT NULL,      -- data e hora da consulta
    data TEXT NOT NULL,             -- AAAA-MM-DD
    marketplace TEXT NOT NULL,      -- ml | shopee
    modalidade TEXT,                -- Full, Coleta, Agência, Flex, Shopee...
    anuncio_id TEXT NOT NULL,
    anuncio_titulo TEXT,
    uf TEXT NOT NULL,
    regiao TEXT NOT NULL,           -- capital | interior
    cidade TEXT,
    cep TEXT NOT NULL,
    opcao TEXT,                     -- opção de frete oferecida (Normal, Expresso...)
    dias_min INTEGER,               -- dias corridos até a data mínima prometida
    dias_max INTEGER,               -- dias corridos até a data máxima prometida
    data_entrega_min TEXT,
    data_entrega_max TEXT,
    frete REAL,
    status TEXT NOT NULL,           -- ok | indisponivel | erro
    erro TEXT,
    bruto TEXT                      -- resposta original (só quando não deu para interpretar)
);
CREATE INDEX IF NOT EXISTS ix_consultas_data ON consultas (data, marketplace);

CREATE TABLE IF NOT EXISTS entregas (   -- pedidos reais já entregues (Mercado Livre)
    envio_id TEXT PRIMARY KEY,
    pedido_id TEXT,
    marketplace TEXT,
    modalidade TEXT,
    data_pedido TEXT,
    data_prometida TEXT,
    data_entregue TEXT,
    uf TEXT,
    cidade TEXT,
    regiao TEXT,
    dias_prometidos INTEGER,
    dias_reais INTEGER,
    no_prazo INTEGER,               -- 1 entregue até a data prometida, 0 atrasou
    atualizado_em TEXT
);
CREATE INDEX IF NOT EXISTS ix_entregas_data ON entregas (data_entregue);
"""


def conectar(caminho):
    conn = sqlite3.connect(str(caminho), check_same_thread=False)
    conn.executescript(SCHEMA)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(consultas)")}
    if "modalidade" not in cols:  # banco criado pela primeira versão
        conn.execute("ALTER TABLE consultas ADD COLUMN modalidade TEXT")
        conn.commit()
    return conn


def inserir(conn, linhas):
    if not linhas:
        return
    sql = f"INSERT INTO consultas ({','.join(COLUNAS)}) VALUES ({','.join('?' * len(COLUNAS))})"
    conn.executemany(sql, [[l.get(c) for c in COLUNAS] for l in linhas])
    conn.commit()


def gravar_entregas(conn, linhas):
    if not linhas:
        return
    sql = (f"INSERT OR REPLACE INTO entregas ({','.join(COLUNAS_ENTREGAS)}) "
           f"VALUES ({','.join('?' * len(COLUNAS_ENTREGAS))})")
    conn.executemany(sql, [[l.get(c) for c in COLUNAS_ENTREGAS] for l in linhas])
    conn.commit()


def envios_ja_gravados(conn):
    return {r[0] for r in conn.execute("SELECT envio_id FROM entregas")}


def conectar_alertas(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS alertas (
        data TEXT, chave TEXT, descricao TEXT, PRIMARY KEY (data, chave))""")
    conn.commit()
    return conn
