"""Radar na nuvem (GitHub Actions, a cada 20 min, com o PC desligado ou não):
1. carrega as ofertas do Mercado Livre da última coleta do PC (radar/dados/ml.json — só as vistas nas últimas 36 h);
2. busca as promoções novas na Promobit (Amazon, Magalu, Shopee, Netshoes…) com o MESMO filtro do Hermes;
3. monta o site (index.html + ofertas.json) em _site/ para o GitHub Pages publicar.
O que saiu de promoção não é revisto e some sozinho em até 36 h.
"""
import json
import shutil
import sys
import time
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI))
from vendas import achadinhos as a, config, db  # noqa: E402

REPO = AQUI.parent
SAIDA = REPO / "_site"
CACHE = config.DADOS / "cache_links.json"  # promobit id → link da loja (poupa o site da Promobit)


def main() -> None:
    config.DADOS.mkdir(parents=True, exist_ok=True)
    config.DB_PATH.unlink(missing_ok=True)
    a._tabela()
    cols = {r["name"] for r in db.consultar("PRAGMA table_info(ofertas)")}
    ml = json.loads((config.DADOS / "ml.json").read_text(encoding="utf-8")) if (config.DADOS / "ml.json").exists() else []
    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    hist = config.DADOS / "historico.json"  # histórico de preço do PC (o banco daqui nasce vazio) → selo "menor preço"
    a.HIST_EXTERNO = json.loads(hist.read_text(encoding="utf-8")) if hist.exists() else {}
    with db.conectar() as con:
        for r in ml:
            r = {k: v for k, v in r.items() if k in cols}
            con.execute(f"INSERT OR REPLACE INTO ofertas ({','.join(r)}) VALUES ({','.join('?' * len(r))})", tuple(r.values()))
        for id_, link in cache.items():  # linhas "velhas" só com o link: o coletar reaproveita e atualiza se a oferta voltar
            con.execute("INSERT OR IGNORE INTO ofertas (id, fonte, titulo, grupo, link_loja, aprovada, visto_em, atualizado_em) "
                        "VALUES (?, 'promobit', '', 'outros', ?, 0, '2000-01-01 00:00:00', '2000-01-01 00:00:00')", (id_, link))
    try:
        a.coletar(log=print)
    except Exception as e:  # Promobit fora do ar não pode derrubar o site: publica só com o ML
        print("coleta falhou:", e)
        a.reavaliar()
    a.vitrine()
    SAIDA.mkdir(exist_ok=True)
    for arq in ("index.html", "ofertas.json"):
        shutil.copyfile(a.SITE / arq, SAIDA / arq)
    if (a.SITE / "oceane").exists():  # 09/10 (dona): área só da Océane, montada pelo vitrine() junto com a página
        shutil.copytree(a.SITE / "oceane", SAIDA / "oceane", dirs_exist_ok=True)
    for arq in ("logo.png", "favicon.png", "README.md"):
        if (REPO / arq).exists():
            shutil.copyfile(REPO / arq, SAIDA / arq)
    if (REPO / "loja").exists():  # fotos da faixa "Da nossa loja" (o PC manda só os produtos com estoque)
        shutil.copytree(REPO / "loja", SAIDA / "loja", dirs_exist_ok=True)
    if (REPO / "links").exists():  # página de links (bio do Instagram) — 01/10
        shutil.copytree(REPO / "links", SAIDA / "links", dirs_exist_ok=True)
    if (REPO / "grupo").exists():  # página de entrada do grupo/canal (link dos pins) — 02/10: dava 404, não era copiada
        shutil.copytree(REPO / "grupo", SAIDA / "grupo", dirs_exist_ok=True)
    if (REPO / "p").exists():  # 02/10: página nossa de cada produto do Pinterest (scripts/pinterest/pagina_produto.py)
        shutil.copytree(REPO / "p", SAIDA / "p", dirs_exist_ok=True)
    if (REPO / "iris").exists():  # 05/10: fotos dos posts da Íris (o Instagram lê daqui) — davam 404, não eram copiadas
        shutil.copytree(REPO / "iris", SAIDA / "iris", dirs_exist_ok=True)
    # 03/10 (dona: "estar onde as pessoas procuram"): Google — robots, mapa do site e arquivo de verificação do Search Console
    for arq in REPO.glob("google*.html"):
        shutil.copyfile(arq, SAIDA / arq.name)
    base = "https://bellalucce.github.io"
    paginas = ["/", "/oceane/", "/grupo/", "/links/"] + [f"/p/{d.name}/" for d in sorted((SAIDA / "p").glob("*")) if d.is_dir()]
    hoje = time.strftime("%Y-%m-%d")
    (SAIDA / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "".join(f"  <url><loc>{base}{p}</loc><lastmod>{hoje}</lastmod></url>\n" for p in paginas) + "</urlset>\n",
        encoding="utf-8")
    (SAIDA / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {base}/sitemap.xml\n", encoding="utf-8")
    novo ={r["id"]: r["link_loja"] for r in db.consultar(
        "SELECT id, link_loja FROM ofertas WHERE fonte = 'promobit' AND link_loja IS NOT NULL "
        "AND atualizado_em >= datetime('now', 'localtime', '-3 days')")}
    CACHE.write_text(json.dumps(novo), encoding="utf-8")
    n = len(json.loads((SAIDA / "ofertas.json").read_text(encoding="utf-8")))
    print(f"site: {n} ofertas ({len(ml)} do ML carregadas)")


if __name__ == "__main__":
    main()
