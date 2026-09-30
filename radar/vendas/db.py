"""SQLite temporário do radar na nuvem (recriado a cada execução)."""
import sqlite3
from contextlib import contextmanager

from vendas import config


@contextmanager
def conectar():
    config.DADOS.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(config.DB_PATH, timeout=60)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


def consultar(sql: str, params=()) -> list[dict]:
    with conectar() as con:
        return [dict(r) for r in con.execute(sql, params).fetchall()]
