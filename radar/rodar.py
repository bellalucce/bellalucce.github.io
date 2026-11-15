"""Radar na nuvem (GitHub Actions, a cada 20 min, com o PC desligado ou não):
1. carrega as ofertas do Mercado Livre da última coleta do PC (radar/dados/ml.json — só as vistas nas últimas 36 h);
2. busca as promoções novas na Promobit (Amazon, Magalu, Shopee, Netshoes…) com o MESMO filtro do Hermes;
3. monta o site (index.html + ofertas.json) em _site/ para o GitHub Pages publicar.
O que saiu de promoção não é revisto e some sozinho em até 36 h.
"""
import json
import re
import shutil
import sys
import time
from pathlib import Path
from xml.sax.saxutils import escape

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI))
from vendas import achadinhos as a, config, db  # noqa: E402

REPO = AQUI.parent
SAIDA = REPO / "_site"
CACHE = config.DADOS / "cache_links.json"  # promobit id → link da loja (poupa o site da Promobit)


_OG_IMAGE = re.compile(r'<meta property="og:image" content="(https://[^"]+)"')


def mapa_do_site(base: str, paginas: list, hoje: str, saida: Path = SAIDA) -> str:
    """sitemap.xml. 15/11 08h25 (Claude): cada página leva a foto da prévia (og:image) como <image:image>, para a foto
    do produto aparecer no Google Imagens (nossa loja e a melhor oferta de cada página) e trazer clique de lá."""
    linhas = []
    for p in paginas:
        arq = saida / p.strip("/") / "index.html" if p.strip("/") else saida / "index.html"
        foto = ""
        try:
            m = _OG_IMAGE.search(arq.read_text(encoding="utf-8")[:20000]) if arq.exists() else None
            if m and "/logo.png" not in m.group(1):
                foto = f"<image:image><image:loc>{escape(m.group(1))}</image:loc></image:image>"
        except OSError:
            pass
        linhas.append(f"  <url><loc>{base}{p}</loc><lastmod>{hoje}</lastmod>{foto}</url>\n")
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
            'xmlns:image="http://www.google.com/schemas/sitemap-image/1.1">\n' + "".join(linhas) + "</urlset>\n")


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
    if (a.SITE / "amigo-secreto").exists():  # 31/10 (E3): lista de amigo secreto, remontada pelo vitrine() a cada rodada
        shutil.copytree(a.SITE / "amigo-secreto", SAIDA / "amigo-secreto", dirs_exist_ok=True)
    if (a.SITE / "curadoria").exists():  # 26/10: Rastreia/Confere/Acha — só existe com o número do WhatsApp da loja
        shutil.copytree(a.SITE / "curadoria", SAIDA / "curadoria", dirs_exist_ok=True)
    # 15/11: páginas por categoria (/promocao-perfume/ …) com as ofertas escritas na página (busca do Google/Bing/ChatGPT)
    # 15/11 03h35: + páginas por loja (/promocao-shopee-beleza/ …), mesmo molde
    todas = list(getattr(a, "PAGINAS_CATEGORIA", {}).values()) + list(getattr(a, "PAGINAS_LOJA", {}).values()) \
        + list(getattr(a, "PAGINAS_PRESENTE", {}).values())  # 15/11 05h15: presentes de Natal
    categorias = [v[0] for v in todas if (a.SITE / v[0] / "index.html").exists()]
    for slug in categorias:
        shutil.copytree(a.SITE / slug, SAIDA / slug, dirs_exist_ok=True)
    nossa_loja = []  # 15/11 04h55: página de cada produto da nossa loja (/nossa-loja/<produto>/)
    if (a.SITE / "nossa-loja" / "index.html").exists():
        shutil.copytree(a.SITE / "nossa-loja", SAIDA / "nossa-loja", dirs_exist_ok=True)
        nossa_loja = ["/nossa-loja/"] + [f"/nossa-loja/{d.name}/" for d in sorted((SAIDA / "nossa-loja").iterdir())
                                         if d.is_dir() and (d / "index.html").exists()]
    if (a.SITE / "feed.xml").exists():  # 15/11: feed RSS das ofertas (Pinterest publica sozinho a partir dele)
        shutil.copyfile(a.SITE / "feed.xml", SAIDA / "feed.xml")
    for arq in ("logo.png", "favicon.png", "README.md"):
        if (REPO / arq).exists():
            shutil.copyfile(REPO / arq, SAIDA / arq)
    if (REPO / "loja").exists():  # fotos da faixa "Da nossa loja" (o PC manda só os produtos com estoque)
        shutil.copytree(REPO / "loja", SAIDA / "loja", dirs_exist_ok=True)
    if (REPO / "links").exists():  # página de links (bio do Instagram) — 01/10
        shutil.copytree(REPO / "links", SAIDA / "links", dirs_exist_ok=True)
    if (REPO / "grupo").exists():  # página de entrada do grupo/canal (link dos pins) — 02/10: dava 404, não era copiada
        shutil.copytree(REPO / "grupo", SAIDA / "grupo", dirs_exist_ok=True)
        try:  # 11/10: 3 ofertas reais gravadas na 1ª dobra da /grupo, antes do botão (a página não espera o ofertas.json)
            n_faixa = a.embutir_faixa_grupo(SAIDA / "grupo" / "index.html",
                                            json.loads((SAIDA / "ofertas.json").read_text(encoding="utf-8")))
            print(f"faixa da /grupo: {n_faixa} ofertas")
        except Exception as e:  # a faixa é um extra: sem ela a página sai como está no repositório
            print("faixa da /grupo falhou:", e)
    if (REPO / "sorteio-amigo-secreto").exists():  # 15/11 08h55 (Claude): sorteio grátis pelo WhatsApp (página fixa)
        shutil.copytree(REPO / "sorteio-amigo-secreto", SAIDA / "sorteio-amigo-secreto", dirs_exist_ok=True)
    if (REPO / "black-friday-beleza").exists():  # 14/10 (dona aprovou a ideia 2 da Vitória): lista de desejos da BF
        shutil.copytree(REPO / "black-friday-beleza", SAIDA / "black-friday-beleza", dirs_exist_ok=True)
    if (REPO / "p").exists():  # 02/10: página nossa de cada produto do Pinterest (scripts/pinterest/pagina_produto.py)
        shutil.copytree(REPO / "p", SAIDA / "p", dirs_exist_ok=True)
    if (REPO / "o").exists():  # 15/10 (dona: "pode encurtar a Océane"): link curto /o/<codigo>/ do grupo (fora do sitemap)
        shutil.copytree(REPO / "o", SAIDA / "o", dirs_exist_ok=True)
    if (REPO / "entrar").exists():  # 04/11: convite por origem /entrar/?de=… (fora do sitemap, noindex)
        shutil.copytree(REPO / "entrar", SAIDA / "entrar", dirs_exist_ok=True)
    if (REPO / "iris").exists():  # 05/10: fotos dos posts da Íris (o Instagram lê daqui) — davam 404, não eram copiadas
        shutil.copytree(REPO / "iris", SAIDA / "iris", dirs_exist_ok=True)
    # 03/10 (dona: "estar onde as pessoas procuram"): Google — robots, mapa do site e arquivo de verificação do Search Console
    for arq in REPO.glob("google*.html"):
        shutil.copyfile(arq, SAIDA / arq.name)
    for arq in REPO.glob("pinterest-*.html"):  # 15/11: arquivo de "reivindicar site" do Pinterest (feed.xml → pins sozinhos)
        shutil.copyfile(arq, SAIDA / arq.name)
    # 15/11: IndexNow (Bing, DuckDuckGo, Yahoo, busca do ChatGPT) — a chave pública <32 hex>.txt na raiz prova que o site é nosso
    for arq in REPO.glob("*.txt"):
        if len(arq.stem) == 32 and all(ch in "0123456789abcdef" for ch in arq.stem):
            shutil.copyfile(arq, SAIDA / arq.name)
    base = "https://bellalucce.github.io"
    paginas = ["/", "/oceane/", "/grupo/", "/links/"] + (
        ["/black-friday-beleza/"] if (SAIDA / "black-friday-beleza").exists() else []) + (
        ["/curadoria/"] if (SAIDA / "curadoria").exists() else []) + (
        ["/amigo-secreto/"] if (SAIDA / "amigo-secreto").exists() else []) + (
        ["/sorteio-amigo-secreto/"] if (SAIDA / "sorteio-amigo-secreto").exists() else []) + [f"/{s}/" for s in categorias] + nossa_loja + [
        f"/p/{d.name}/" for d in sorted((SAIDA / "p").glob("*")) if d.is_dir()]
    hoje = time.strftime("%Y-%m-%d")
    (SAIDA / "sitemap.xml").write_text(mapa_do_site(base, paginas, hoje), encoding="utf-8")
    # 15/11 06h10 (Claude): llms.txt (resumo do site para as buscas com IA: ChatGPT, Perplexity), páginas que existem
    try:
        titulos = {v[0]: v[1].replace(" hoje", "") for v in todas}
        linhas = ["# Bella Lucce", "",
                  "> Curadoria de promoções de beleza (perfume, maquiagem, skincare, cabelo) das lojas grandes do Brasil, "
                  "com o preço conferido e sem desconto inflado. Atualizado a cada 20 minutos. Links de afiliado (#publi).",
                  "", "## Promoções de hoje"]
        linhas += [f"- [{titulos[s]}]({base}/{s}/)" for s in categorias]
        if (SAIDA / "amigo-secreto").exists():
            linhas.append(f"- [Amigo secreto: presentes até R$ 30, 50 e 80]({base}/amigo-secreto/)")
        if (SAIDA / "sorteio-amigo-secreto").exists():
            linhas.append(f"- [Sorteio de amigo secreto grátis pelo WhatsApp]({base}/sorteio-amigo-secreto/)")
        if nossa_loja:
            linhas += ["", "## Nossa loja", f"- [Produtos da nossa loja Bella Lucce na Shopee]({base}/nossa-loja/)"]
        linhas += ["", "## Outros", f"- [Todas as ofertas]({base}/)", f"- [Grupo de ofertas no WhatsApp]({base}/grupo/)",
                   f"- [Feed RSS das ofertas]({base}/feed.xml)"]
        (SAIDA / "llms.txt").write_text("\n".join(linhas) + "\n", encoding="utf-8")
    except Exception as e:  # extra: sem ele o site sai igual
        print("llms.txt falhou:", e)
    (SAIDA / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {base}/sitemap.xml\n", encoding="utf-8")
    novo ={r["id"]: r["link_loja"] for r in db.consultar(
        "SELECT id, link_loja FROM ofertas WHERE fonte = 'promobit' AND link_loja IS NOT NULL "
        "AND atualizado_em >= datetime('now', 'localtime', '-3 days')")}
    CACHE.write_text(json.dumps(novo), encoding="utf-8")
    n = len(json.loads((SAIDA / "ofertas.json").read_text(encoding="utf-8")))
    print(f"site: {n} ofertas ({len(ml)} do ML carregadas)")


if __name__ == "__main__":
    main()
