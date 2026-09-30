"""Configuração mínima do radar na nuvem (GitHub Actions) — espelha `vendas.config` do Hermes (que fica no PC).

O código do radar (`achadinhos.py`) é o MESMO do Hermes, copiado para cá pelo Hermes a cada publicação.
"""
import json
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent  # radar/
DADOS = RAIZ / "dados"
CONFIG = RAIZ / "config"
DB_PATH = DADOS / "radar.db"


def segredos() -> dict:
    """Só os códigos de afiliado — são públicos por natureza (aparecem em todo link do site)."""
    arq = CONFIG / "afiliados.json"
    return {"afiliados": json.loads(arq.read_text(encoding="utf-8")) if arq.exists() else {}}
