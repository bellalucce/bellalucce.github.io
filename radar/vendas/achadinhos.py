"""Radar de achadinhos: acha as MELHORES promoções (Amazon, Mercado Livre, Shopee e outras) a cada 20 min, sem gastar
o limite do Claude (roda pela tarefa do Windows "Hermes - achadinhos"). Estudo e decisões:
cerebro/50-Decisoes/2026-09-29-Grupo-de-achadinhos.md

Fontes (só páginas públicas permitidas no robots.txt, poucas requisições por ciclo):
- Promobit (comunidade que vota as ofertas; cobre Amazon/ML/Shopee/Magalu…): páginas "em alta", "recentes" e categorias
  do nosso foco. Sinais de qualidade: TOP_OFFER, avaliações "great/amazing", curtidas, cliques, % de desconto.
- Mercado Livre: NÃO raspar — o robots.txt do ML proíbe robôs de IA (ClaudeBot/GPTBot "Disallow: /"). ML entra pelo
  painel oficial de afiliados. Amazon idem (robots proíbe ClaudeBot): ofertas da Amazon vêm pela Promobit ou PA-API.
- (depois do cadastro do usuário) Shopee Afiliados Open API — productOfferV2 oficial, com comissão.
O link da loja é resolvido só para as ofertas aprovadas; o código de afiliado de terceiros é removido e trocado pelo
nosso quando existir (config/segredos.json → "afiliados": {"amazon_tag": "...", ...}).
"""
import html as _html
import json
import re
import time
from datetime import datetime, timedelta

import httpx

from vendas import config, db, ganchos

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0 Safari/537.36", "Accept-Language": "pt-BR,pt;q=0.9"}
PROMOBIT = "https://www.promobit.com.br"
PAGINAS_PROMOBIT = ["/promocoes/em-alta/", "/promocoes/recentes/", "/promocoes/perfumes-e-beleza/",
                    "/promocoes/moda-e-calcados-femininos/", "/promocoes/saude-e-higiene/",
                    "/promocoes/utensilios-domesticos/", "/promocoes/casa-e-construcao/", "/promocoes/menor-preco/",
                    "/promocoes/loja/magazine-luiza/",  # Magalu (pedido do usuário; o site da Magalu bloqueia robôs)
                    # 30/09: resto da linha do grupo (bebê, fitness) e joias/relógios (tipo de oferta "premium" dos grupos)
                    "/promocoes/bebes-e-criancas/", "/promocoes/suplementos-e-fitness/", "/promocoes/relogios-e-joias/",
                    # 30/09 04h: lojas de beleza (linha principal do grupo) — teste: 24/20/7/6 aprovadas por página
                    "/promocoes/loja/beleza-na-web/", "/promocoes/loja/sephora/", "/promocoes/loja/epoca-cosmeticos/",
                    "/promocoes/loja/natura/",
                    # 30/09 15h (dona: vídeos de GRIFE → o grupo precisa ter grife de verdade): Farfetch = luxo
                    # (Ray-Ban de R$ 1.030 por R$ 515 no teste); Dafiti/Zattini/Amazon/Magalu/Vivara não trouxeram produto
                    "/promocoes/loja/farfetch/"]

# categoria da fonte → grupo mostrado na vitrine
GRUPOS = {
    "beleza": ["perfumes-e-beleza", "saude-e-higiene", "beleza", "perfume", "maquiagem"],
    "moda": ["moda-e-calcados-femininos", "moda-e-calcados-masculinos", "relogios-e-joias", "mochilas-e-malas"],
    "casa": ["casa-e-construcao", "moveis-e-decoracao", "cama-mesa-e-banho", "utensilios-domesticos",
             "eletroportateis", "eletrodomesticos", "ferramentas-e-jardim"],
    "eletronicos": ["eletronicos-audio-e-video", "informatica", "smartphones-tablets-e-telefones", "games",
                    "cameras-filmadoras-e-drones"],
    "mercado": ["supermercado-e-delivery", "bebidas"],
    "infantil": ["bebes-e-criancas", "brinquedos-e-hobbies"],
    "esporte": ["esporte-e-lazer", "suplementos-e-fitness"],
    "pet": ["petshop"],
}
PALAVRAS = {  # para o ML, que não diz a categoria na página de ofertas
    "beleza": r"perfume|batom|gloss|maquiag|base |rímel|rimel|sérum|serum|creme|hidratante|protetor solar|shampoo|condicionador|"
              r"skincare|máscara facial|mascara facial|esmalte|secador|chapinha|escova|delineador|paleta|blush|colônia|body splash",
    "moda": r"tênis|tenis|camiseta|blusa|vestido|calça|calca|bolsa|sandália|sandalia|jaqueta|moletom|relógio|relogio|óculos|mochila|biquíni",
    "casa": r"panela|colchão|colchao|toalha|lençol|lencol|travesseiro|aspirador|air fryer|fritadeira|liquidificador|cafeteira|"
            r"organizador|cortina|tapete|jogo de cama|potes|faqueiro|ventilador|micro-ondas|geladeira|fogão",
    "eletronicos": r"smart ?tv|notebook|(?<!renova[çc][ãa]o )celular|smartphone|fone|headset|monitor|tablet|carregador|ssd|mouse|teclado|caixa de som|câmera|camera",
    "mercado": r"café|cafe|leite|cerveja|vinho|chocolate|sabão|detergente|papel higiênico|fralda",
    "esporte": r"creatina|whey|pré-treino|halter|esteira|bicicleta|academia|suplemento|colágeno",
}
PALAVRAS["eletronicos"] += r"|motorola|samsung|iphone|xiaomi|redmi|galaxy|kindle|echo dot|alexa|smartwatch|playstation|xbox"
# Linha do grupo (usuário, 29/09 noite): beleza/cabelo/perfume primeiro; fitness, casa viral e bebê; moda; pet só de
# marca popular. Peso somado ao score — segue uma linha em vez de "aleatório".
PESO_GRUPO = {"beleza": 10, "cabelo": 10, "perfume": 9, "esporte": 7, "casa": 6, "infantil": 6, "moda": 5, "pet": 3,
              "eletronicos": 0}
CABELO = re.compile(r"shampoo|xampu|condicionador|m[áa]scara capilar|capilar|cabelo|secador|chapinha|prancha|babyliss|"
                    r"escova (secadora|alisadora|rotativa)|modelador de cachos|finalizador|leave-?in|[óo]leo capilar|"
                    r"t[ôo]nico capilar|progressiva|tintura|coloraç", re.I)
FITNESS = re.compile(r"bicicleta ergom|esteira|legging|top fitness|conjunto fitness|academia|halter|anilha|el[áa]stico de "
                     r"exerc|colchonete|yoga|pilates|whey|creatina|pr[ée]-?treino|squeeze|coqueteleira|corda de pular|"
                     r"suplemento em p|hipercal|carboidrat|albumina|bioimped|pasta de amendoim|barra de prote|"
                     r"termog[êe]nic|probi[óo]tic|\bprotein\b|isolate", re.I)
BEBE = re.compile(r"fralda|len[çc]o umedecido|beb[êe]|infantil|mamadeira|chupeta|carrinho de beb|body infantil|"
                  r"banheira|trocador|kit ber[çc]o|brinquedo", re.I)
# pet: só o que vende muito e serve para qualquer bicho (nada de remédio nem ração específica)
PET_POPULAR = re.compile(r"whiskas|pedigree|golden|premier|gran plus|special (dog|cat)|friskies|dog chow|cat chow|"
                         r"areia (sanit|higi)|tapete higi|arranhador|caminha|cama pet|comedouro|bebedouro|fonte para gato|"
                         r"brinquedo (pet|para (c[ãa]es|gatos))|coleira|peitoral", re.I)
FORA = re.compile(  # fora da linha do grupo (pedido do usuário): automotivo, remédios, peças, industrial
    r"automotiv|para-?brisa|palheta|taramps|m[óo]dulo (amplificador|de pot[êe]ncia)|som automotivo|alto-?falante automot|"
    r"pneu|[óo]leo (de )?motor|aditivo|farol|retrovisor|som para carro|"
    # 30/09 (auditoria do site): shampoo DE CARRO caía em "cabelo"; bike/patinete elétrico e soprador em "infantil"
    r"vonixx|lava[- ]?autos?|(?<!infantil )(?<!cadeirinha )para carros?\b|veicular|bike el[ée]trica|"
    r"patinete el[ée]tric|carregador (de )?bike|soprador|\bgamer\b|drone|"
    # suplemento em cápsula parece remédio (usuário: nada de remédio); colágeno/whey em pó continuam
    # (cápsula de café — Dolce Gusto/Nespresso — é achadinho de cozinha, não remédio)
    r"^(?!.*(caf[ée]\b|dolce gusto|nespresso|tr[êe]s cora))(.*?)(\d+ ?c[áa]psulas|\bem c[áa]psulas?\b|\d+ ?(c[áa]ps|tabs?|tabletes)\b)|"
    r"coenzima|c[úu]rcuma|metilcobalamina|vitamina b ?\d|melatonina|seringa|insulina|agulha|compress[ãa]o \d|raspador (de )?l[íi]ngua|"
    # Lei 11.265/2006 (NBCAL): proibido PROMOVER mamadeira, bico, chupeta, fórmula infantil e afins
    r"mamadeira|chupeta|bicos? (de mamadeira|ortod|de silicone)|protetor de mamilo|f[óo]rmula infantil|leite infantil|"
    r"composto l[áa]cteo|\bnan (supreme|comfor|pro)|aptamil|nestog[êe]no|milnutri|papinha|"
    r"simparic|bravecto|nexgard|credeli|verm[íi]fugo|antipulgas|medicamento|rem[ée]dio|comprimidos? de|"
    r"ra[çc][ãa]o .*(renal|urin|gastro|hipoalerg|obes|hep[áa]t|terap|veterin|diet)|"
    r"placa de v[íi]deo|processador (intel|amd|ryzen|core)|placa-?m[ãa]e|mem[óo]ria ram|fonte atx|gabinete gamer|"
    r"rolamento|parafuso|disjuntor|cabo flex|fio el[ée]tric|v[áa]lvula|mangueira de press|motor el[ée]tric|"
    r"livro|apostila|camiseta de time|uniforme (escolar|de time|profissional|militar)|"
    r"inalador|nebulizador|ox[íi]metro|medidor de press|aparelho de press|term[ôo]metro cl[íi]nic|glicos|palmilha", re.I)
STOP = {"de", "da", "do", "das", "dos", "com", "para", "e", "em", "a", "o", "kit", "c", "p", "sem", "novo", "nova",
        "original", "promo", "oferta", "unidade", "un", "pcs", "peças", "pecas", "the"}


def chave_produto(titulo: str) -> str:
    """'Kit 2 Alicates de Cutícula Inox' → 'alicates cuticula': tipo do produto (2 primeiras palavras relevantes),
    para mostrar só o MELHOR de cada tipo (pedido do usuário: 'não precisa de 4, escolhe o melhor')."""
    import unicodedata
    t = unicodedata.normalize("NFKD", titulo.lower()).encode("ascii", "ignore").decode()
    pal = [p for p in re.findall(r"[a-z]+", t) if p not in STOP and len(p) > 2]
    return " ".join(pal[:2])


def sem_repetidos(ofs: list[dict], por_tipo: int = 1) -> list[dict]:
    """Mantém a ordem (melhores primeiro) e só `por_tipo` oferta(s) de cada tipo de produto."""
    vistos: dict[str, int] = {}
    out = []
    for o in ofs:
        k = chave_produto(o.get("titulo") or "")
        if vistos.get(k, 0) < por_tipo:
            vistos[k] = vistos.get(k, 0) + 1
            out.append(o)
    return out


def _tabela() -> None:
    with db.conectar() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS ofertas (
            id TEXT PRIMARY KEY,               -- fonte:id
            fonte TEXT, loja TEXT, titulo TEXT, grupo TEXT,
            preco REAL, preco_antigo REAL, desconto INTEGER, cupom TEXT,
            foto TEXT, link_fonte TEXT, link_loja TEXT,
            nota REAL, vendidos TEXT, sinais TEXT, score REAL,
            aprovada INTEGER DEFAULT 0,
            visto_em TEXT, atualizado_em TEXT, publicado_em TEXT
        );""")
        if "link_tentado_em" not in {r[1] for r in con.execute("PRAGMA table_info(ofertas)")}:
            # link da loja que não deu para resolver (ex.: meli.la de terceiros): não tenta de novo por 24 h
            con.execute("ALTER TABLE ofertas ADD COLUMN link_tentado_em TEXT")
        # histórico próprio (30/09, estudo de achadinhos): menor preço visto por dia → "desconto de verdade?" e selo
        # "menor preço em N dias", como Promobit/Keepa mostram
        con.execute("CREATE TABLE IF NOT EXISTS ofertas_precos (id TEXT, dia TEXT, preco REAL, PRIMARY KEY (id, dia))")


def _guardar_precos(con, pares) -> None:
    """pares = [(id_oferta, preco)]; fica o MENOR preço do dia."""
    con.executemany("""INSERT INTO ofertas_precos (id, dia, preco) VALUES (?, date('now','localtime'), ?)
                       ON CONFLICT(id, dia) DO UPDATE SET preco = MIN(preco, excluded.preco)""",
                    [(i, p) for i, p in pares if p])


HIST_MIN_DIAS = 7  # abaixo disso "menor preço" não quer dizer nada


def historico(id_oferta: str, dias: int = 30) -> dict | None:
    """Preços dos dias ANTERIORES a hoje (até `dias`): {'dias', 'minimo', 'maximo'}; None sem histórico."""
    r = db.consultar("""SELECT COUNT(*) dias, MIN(preco) minimo, MAX(preco) maximo FROM ofertas_precos
                        WHERE id = ? AND dia < date('now','localtime') AND dia >= date('now','localtime', ?)""",
                     (id_oferta, f"-{dias} days"))[0]
    return r if r["dias"] else None


HIST_EXTERNO: dict | None = None  # na nuvem o banco nasce vazio: {id: [dias, minimo]} que o PC manda (historico.json)


def minimos(dias: int = 30) -> dict:
    """{id: [dias, minimo]} das ofertas com HIST_MIN_DIAS+ dias de histórico ANTES de hoje (selo do site)."""
    if HIST_EXTERNO is not None:
        return HIST_EXTERNO
    return {r["id"]: [r["dias"], r["minimo"]] for r in db.consultar(
        """SELECT id, COUNT(*) dias, MIN(preco) minimo FROM ofertas_precos WHERE dia < date('now','localtime')
           AND dia >= date('now','localtime', ?) GROUP BY id HAVING COUNT(*) >= ?""", (f"-{dias} days", HIST_MIN_DIAS))}


def selo_preco(o: dict) -> str:
    """'📉 Menor preço em N dias' só com HIST_MIN_DIAS+ de histórico e preço ABAIXO do menor anterior; senão ''."""
    h = historico(o["id"]) if o.get("id") and o.get("preco") else None
    if not h or h["dias"] < HIST_MIN_DIAS or o["preco"] >= h["minimo"]:
        return ""
    return f"📉 Menor preço em {h['dias']} dias no nosso radar"


def _grupo(slug_ou_titulo: str) -> str:
    s = (slug_ou_titulo or "").lower()
    for g, slugs in GRUPOS.items():
        if s in slugs:
            return g
    for g, pat in PALAVRAS.items():
        if re.search(pat, s):
            return g
    return "outros"


PERFUME = re.compile(r"perfume|col[ôo]nia|body splash|eau de|parfum|deo col|\bed[pt]\b", re.I)


BEM_ESTAR = re.compile(r"vitamin|multivitam|suplement|col[áa]geno|whey|creatina|[ôo]mega ?3", re.I)
PET = re.compile(r"para (c[ãa]es|cachorros?|gatos?|pets?|felinos?|caninos?)|\bpet\b|\bra[çc][ãa]o\b|arranhador|"
                 r"caixa de areia|areia sanit|coleira|comedouro|cama de cachorro|casinha de cachorro", re.I)
CAMA_BANHO = re.compile(r"travesseiro|almofada|len[çc]ol|edredom|toalha|cobertor|manta de sof|tapete|"
                        r"papel higi[êe]nico|umidificador|balan[çc]a", re.I)  # casa (antes caíam em "beleza")


def _grupo_final(g: str, titulo: str) -> str:
    """Refina a categoria pelo título: perfume vira aba própria; categoria genérica tenta pelas palavras."""
    if PERFUME.search(titulo or "") and not re.search(r"expositor|organizador|porta[- ]", titulo or "", re.I):
        return "perfume"
    if CABELO.search(titulo or ""):
        return "cabelo"
    if FITNESS.search(titulo or ""):
        return "esporte"
    if PET.search(titulo or ""):
        return "pet"      # item de bicho em casa/moda/infantil escapava da regra "pet só de marca popular"
    if BEBE.search(titulo or ""):
        return "infantil"  # vale para qualquer g: antes só alguns → infantil↔casa alternava a cada ciclo
    if BEM_ESTAR.search(titulo or ""):
        return "beleza"   # bem-estar fica junto de beleza (a loja da Promobit às vezes classifica errado)
    if CAMA_BANHO.search(titulo or ""):
        return "casa"
    if g in ("outros", "mercado", "esporte", "pet"):
        g2 = _grupo(titulo)
        return g2 if g2 in GRUPOS_OK else g
    return g


# ---------------- fontes ----------------
LINK_OU_FONE = re.compile(r"(?i)\b(?:https?://|www\.)\S+|\b[\w-]+(?:\.[\w-]+)*\.(?:com|net|org|ly|io|me|to|br|co|gl|app|link|"
                          r"site|xyz|info|shop|store)(?:\.br)?(?:/\S*)?\b|(?<![\w.])(?:\+?55\s?)?\(?\d{2}\)?\s?9?\d{4}[-\s]\d{4}(?!\d)|"
                          r"[\w.+-]+@[\w-]+\.[\w.]+")
PRECO_NO_TITULO = re.compile(r"\s+De:?\s*R\$\s*[\d.,]+\s*Por:?\s*R\$\s*[\d.,]*.*$|\s+Selo:?\s*\d*\s*$", re.I)


def limpar_titulo(t: str) -> str:
    """30/09: títulos da Beleza na Web vêm com o preço colado ("... 200ml De: R$ 481,90 Por: R$ 279,90 Selo: 4") →
    no post saía o preço duas vezes e um "Selo: 4" sem sentido."""
    t = PRECO_NO_TITULO.sub("", (t or "").strip()).strip(" -–|")
    # 30/09 segurança: título vem da comunidade (Promobit) → link/telefone no título viraria link clicável no grupo
    # (golpe). O único link do post é o da loja, conferido por pagina_de_produto().
    t = re.sub(r"\s{2,}", " ", LINK_OU_FONE.sub(" ", t)).strip(" -–|:")
    letras = [c for c in t if c.isalpha()]
    if len(t) > 20 and letras and sum(c.isupper() for c in letras) / len(letras) > 0.85:
        t = _sem_gritar(t)  # título TODO EM MAIÚSCULAS parece spam no grupo
    return t


SIGLAS = {"edp", "edt", "edc", "fps", "uv", "led", "usb", "tv", "hd", "pc", "ph", "bb", "cc"}
MIUDAS = {"de", "da", "do", "das", "dos", "e", "com", "para", "em", "no", "na", "a", "o", "p/", "c/"}


def _sem_gritar(t: str) -> str:
    out = []
    for i, w in enumerate(t.lower().split()):
        if w in SIGLAS:
            out.append(w.upper())
        elif re.fullmatch(r"\d+(?:[.,]\d+)?(?:ml|g|kg|l|w|mm|cm)?", w) or (i and w in MIUDAS) or w in ("ml", "g", "kg"):
            out.append(w)
        else:
            out.append(w[:1].upper() + w[1:])
    return " ".join(out)


def _promobit(caminho: str, cli: httpx.Client) -> list[dict]:
    h = cli.get(PROMOBIT + caminho).text
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', h, re.S)
    if not m:
        return []
    achados: dict[int, dict] = {}

    def varrer(o):
        if isinstance(o, dict):
            if "offerTitle" in o and "offerId" in o:
                achados[o["offerId"]] = o
            for v in o.values():
                varrer(v)
        elif isinstance(o, list):
            for v in o:
                varrer(v)
    varrer(json.loads(m.group(1)))
    out = []
    for o in achados.values():
        r = o.get("ratings") or {}
        antigo = o.get("offerOldPrice") or None
        preco = o.get("offerPrice")
        desc = o.get("offerDiscontPercentage") or (round(100 * (1 - preco / antigo)) if antigo and preco else 0)
        sinais = {"tipo": o.get("offerPriceType"),  # NORMAL = produto; STARTING_AT/COUPON/FREE = lista, cupom, propaganda
                  "top": o.get("offerStatusName") == "TOP_OFFER", "destaque": bool(o.get("offerIsHighlight")),
                  "otima": (r.get("great") or 0) + 2 * (r.get("amazing") or 0), "ruim": r.get("bad") or 0,
                  "curtidas": o.get("offerLikes") or 0, "cliques": o.get("offerClicks") or 0}
        out.append({
            "id": f"promobit:{o['offerId']}", "fonte": "promobit", "loja": o.get("storeName"),
            "titulo": limpar_titulo(_html.unescape(o.get("offerTitle") or "")),
            "grupo": _grupo_final(_grupo(o.get("categorySlug") or o.get("offerTitle") or ""), o.get("offerTitle") or ""),
            "preco": preco, "preco_antigo": antigo, "desconto": int(desc or 0), "cupom": cupom_valido(o.get("offerCoupon")),
            "foto": f"https://i.promobit.com.br/400{o['offerPhoto']}" if o.get("offerPhoto") else None,
            "link_fonte": f"{PROMOBIT}/Redirect/to/{o['offerId']}/", "sinais": sinais,
            "publicada_fonte": o.get("offerPublished")})
    return out


# ---------------- qualidade ----------------
def pontuar(o: dict) -> float:
    """0–100. Só entra na vitrine/grupo o que é promoção boa DE VERDADE (pedido do usuário)."""
    s, d = o.get("sinais") or {}, o.get("desconto") or 0
    p = min(d, 70) * 0.8                                   # desconto real pesa mais
    p += 20 if s.get("top") else 0
    p += min(s.get("otima", 0), 6) * 4 - s.get("ruim", 0) * 6
    p += min(s.get("curtidas", 0), 30) * 0.5 + min(s.get("cliques", 0), 1000) / 100
    p += 8 if s.get("oficial") else 0
    p += 6 if (o.get("nota") or 0) >= 4.6 else 0
    vend = s.get("vendidos_num", 0) or (1000 if "mil" in (o.get("vendidos") or "") else 0)
    p += 16 if vend >= 50000 else 12 if vend >= 10000 else 8 if vend >= 5000 else 4 if vend >= 1000 else 0  # viral
    destaque = s.get("destaque")  # ML: texto ("MAIS VENDIDO"); Promobit: sim/não
    p += 5 if isinstance(destaque, str) and re.search(r"MAIS VENDIDO|IMPERD", destaque, re.I) else 0
    p += min(s.get("comissao", 0), 20) * 0.4               # ML Afiliados: comissão maior = mais ganho para a loja
    p += PESO_GRUPO.get(o.get("grupo"), 0)                 # segue a linha do grupo
    p += 8 if MARCAS.search(o.get("titulo") or "") else 0  # marca conhecida (o que os grupos grandes mais postam)
    p += 4 if 20 <= (o.get("preco") or 0) <= 120 else 0    # faixa de preço que mais sai nos grupos
    titulo = o.get("titulo") or ""
    p -= 18 if MASCULINO.search(titulo) else 0             # público do grupo é principalmente feminino
    p += 4 if re.search(r"feminin|mulher", titulo, re.I) else 0
    return round(max(0.0, min(100.0, p)), 1)


MASCULINO = re.compile(r"masculin|\bmen\b|\bhomem\b|cueca|boxer|barbear|\bbarba|p[óo]s[- ]barba|"
                       r"m[áa]quina de (acabamento|corte|cortar)|aparador de pelos|barbeador|testosteron|"
                       r"cortador de cabelo|groom|trimmer|clipper|"
                       # 30/09: perfumes masculinos famosos que não dizem "masculino" no título (Lattafa Asad caiu no vídeo)
                       r"\basad\b|fakhar black|club de nuit intense man|\bsauvage\b|bleu de chanel|\b1 million\b|"
                       r"\binvictus\b|\beros\b|\bstronger with you\b|\bpour homme\b|\bfor him\b|\bhomme\b", re.I)


def cupom_valido(c: str | None) -> str | None:
    """Cupom só se parecer código de verdade (a fonte às vezes manda texto como 'do anúncio')."""
    c = (c or "").strip()
    return c if re.fullmatch(r"[A-Za-z0-9_-]{3,24}", c) and not re.fullmatch(r"(?i)cupom|desconto|anuncio", c) else None


# Regras do usuário (29/09): SÓ anúncio de UM produto, link direto da página do produto, até ~R$ 500, categorias
# moda/beleza/perfume/casa/eletrônicos/infantil. Nada de viagem, cupom solto, "entre no grupo", listas ("ACHADOS X"),
# aba "outros" — e NUNCA link com afiliado de terceiros.
GRUPOS_OK = ("beleza", "cabelo", "perfume", "moda", "casa", "eletronicos", "infantil", "esporte", "pet")
# teto por categoria (o público é principalmente feminino: perfume caro ok, geladeira não). O usuário deixou claro que
# R$ 500 era exemplo: promoção boa (≥ 50% off) pode passar até 1,5× o teto.
PRECO_MAX = {"perfume": 1500, "beleza": 1000, "cabelo": 1000, "moda": 1000, "casa": 600, "eletronicos": 800,
             "infantil": 600, "esporte": 1500, "pet": 400}  # esporte até 1.500: bicicleta ergométrica (usuário)
VOLUMOSOS = re.compile(r"geladeira|refrigerador|freezer|conservador|fog[ãa]o|cooktop|forno el[ée]tri|m[áa]quina de lavar|"
                       r"lava e seca|lavadora|lava[- ]lou[çc]a|secadora de roupa|ar[- ]condicionado|smart ?tv|televis|"
                       r"\btv \d|colch[ãa]o|sof[áa]|guarda[- ]roupa|cama box|bicicleta (?!ergom)|pneu|"
                       # 01/10: móvel de banheiro/cozinha (gabinete com cuba, armário aéreo) — fora do tom do grupo
                       r"gabinete .*(cuba|banheiro|pia)|arm[áa]rio (de |para )?(banheiro|cozinha|a[ée]reo)|\bcuba\b|"
                       # material de obra/ferramenta: nada a ver com o público do grupo
                       r"manta l[íi]quida|impermeabiliz|bomba (pressurizadora|d.?[áa]gua)|refletor|holofote|argamassa|"
                       r"cimento|furadeira|parafusadeira|motosserra|compressor de ar", re.I)
SPAM = re.compile(r"cupo(m|ns)|pacote|viagem|passage|hotel|\bvoo|assinatura|receb(a|er) |grupo d|whatsapp|telegram|"
                  r"achados|sele[çc][ãa]o|todo o site|frete gr[áa]tis|cashback|cart[ãa]o de|cr[ée]dito|gift ?card|"
                  r"vale[- ]presente|streaming|\bplano |liberad|imperd[íi]vel dia|at[ée] \d+% off em|"
                  # política/religião/armas: nada que divida o público do grupo
                  r"\blula\b|bolsonar|\bpt\b|partido|presidente|elei[çc][ãa]o|pol[íi]tic|airsoft|arma de press", re.I)
PAGINA_PRODUTO = [  # loja → padrão de URL de página de UM produto (loja fora da lista = rejeitada)
    r"amazon\.com\.br/(?:.*/)?(?:dp|gp/product)/[A-Z0-9]{10}",
    r"shopee\.com\.br/(?:product/\d+/\d+|.+-i\.\d+\.\d+)",
    r"mercadolivre\.com\.br/(?:.+/p/MLB\d+|MLB-?\d{6,})|produto\.mercadolivre\.com\.br/MLB-\d+",
    r"magazineluiza\.com\.br/.+/p/\w+/", r"(?:netshoes|zattini|centauro)\.com\.br/(?:p/|.+-[A-Z0-9]{3}-\d{4}-\d{3})",
    r"kabum\.com\.br/produto/\d+", r"(?:americanas|submarino|shoptime)\.com\.br/produto/\d+",
    r"(?:casasbahia|pontofrio|extra)\.com\.br/.+/p/\d+", r"aliexpress\.com/item/\d+", r"sephora\.com\.br/.+\.html",
    r"belezanaweb\.com\.br/[a-z0-9-]+/?$", r"dafiti\.com\.br/.+-\d+\.html", r"renner\.com\.br/.+/p/\d+",
    r"boticario\.com\.br/[a-z0-9-]+/?$", r"natura\.com\.br/p/", r"epocacosmeticos\.com\.br/[a-z0-9-]+/p",
    r"vivara\.com\.br/[a-z0-9-]+/p", r"pandora\.(?:com\.br|net)/.+\.html",  # joias (sem comissão até o cadastro na Awin)
    r"farfetch\.com/br/shopping/[a-z]+/[a-z0-9-]+-item-\d+\.aspx",  # luxo (30/09)
]
AFILIADO_TERCEIRO = re.compile(r"[?&](tag|promoter_id|partner_id|matt_tool|matt_word|utm_[a-z]+|aff[a-z_]*|affiliate|"
                               r"clickid|smtt|pid|lp|ref|sp_atk|mmp_pid)=|divulgador|meli\.la|s\.shopee|shope\.ee|amzn\.to|"
                               r"linksynergy|awin|/social/|afiliad|onelink|bit\.ly|tidd\.ly|promobit", re.I)


def tem_afiliado_terceiro(url: str | None) -> bool:
    """True se o link (antes de pormos o NOSSO código) ainda carrega qualquer sinal de afiliado/rastreio de outros."""
    return bool(url) and bool(AFILIADO_TERCEIRO.search(url))


def eh_produto(o: dict) -> bool:
    """Só anúncio de 1 produto, com página de produto, preço até PRECO_MAX e categoria permitida."""
    s = o.get("sinais") or {}
    if isinstance(s, str):
        s = json.loads(s or "{}")
    if s.get("tipo") and s["tipo"] != "NORMAL":
        return False
    if (o.get("loja") or "") in ("Promobit", "Decolar", "Booking", "Hurb", "123 Milhas", "MaxMilhas"):
        return False
    titulo = o.get("titulo") or ""
    if o.get("grupo") not in GRUPOS_OK or SPAM.search(titulo) or VOLUMOSOS.search(titulo) or FORA.search(titulo):
        return False
    if o.get("grupo") == "pet" and not PET_POPULAR.search(titulo):  # pet: só marca/item popular
        return False
    teto = PRECO_MAX[o["grupo"]] * (1.5 if (o.get("desconto") or 0) >= 50 else 1)
    if LUXO.search(titulo):  # 30/09 (dona): grife é a vitrine do grupo (vídeos de divulgação) — bolsa de marca passa de R$ 1.000
        teto *= 2
    if not (o.get("preco") and 1 <= o["preco"] <= teto):
        return False
    link = o.get("link_loja")
    if link is not None and (tem_afiliado_terceiro(link) or not pagina_de_produto(link)):
        return False
    return True


def pagina_de_produto(link: str) -> bool:
    """O DOMÍNIO tem que ser da loja (antes 'https://x.com/?u=produto.mercadolivre.com.br/MLB-1' passava)."""
    return any(re.match(r"https?://(?:[\w-]+\.)*(?:" + p + ")", link) for p in PAGINA_PRODUTO)


FALSIFICAVEL = re.compile(rf"k[ée]rastase|{ganchos.KB}", re.I)  # 30/09: marcas muito falsificadas no marketplace


def aprovada(o: dict) -> bool:
    s, d = o.get("sinais") or {}, o.get("desconto") or 0
    if not eh_produto(o):
        return False
    if s.get("ruim", 0) > s.get("otima", 0):
        return False
    if o.get("grupo") in ("beleza", "cabelo", "perfume") and o["fonte"] == "promobit":  # foco: mais permissivo
        return s.get("top") or d >= 30 or s.get("otima", 0) >= 3 or s.get("curtidas", 0) >= 5
    # "só promoções muito boas": desconto alto + algum sinal de qualidade (loja oficial, nota, voto da comunidade)
    if o["fonte"] == "ml_ofertas":
        return d >= 55 or (d >= 40 and (s.get("oficial") or (o.get("nota") or 0) >= 4.6))
    if o["fonte"] in ("ml_afiliados", "shopee_afiliados"):  # listas oficiais: sem votos → desconto + nota + vendas
        nota, vend = o.get("nota") or 0, s.get("vendidos_num", 0)
        # Shopee: coreano/Kérastase/maquiagem importada SÓ de loja oficial (Shopee Mall) — o marketplace é cheio de cópia
        if o["fonte"] == "shopee_afiliados" and (FALSIFICAVEL.search(o.get("titulo") or "")
                                                 or LUXO.search(o.get("titulo") or "")) and not s.get("oficial"):
            return False
        # Kérastase/coreana: muita falsificação no marketplace e o painel não diz se é loja oficial → só anúncio com
        # nota ≥ 4,7 e +1.000 vendidos (falsificado junta avaliação "não é original"; a loja oficial tem 4,8–4,9 e +10 mil)
        if FALSIFICAVEL.search(o.get("titulo") or "") and not (nota >= 4.7 and vend >= 1000):
            return False
        bom, viral = nota >= 4.6 and vend >= 1000, nota >= 4.5 and vend >= 10000
        if o.get("grupo") in ("beleza", "cabelo", "perfume"):   # linha principal do grupo
            return d >= 40 or (d >= 20 and bom) or (d >= 15 and viral)
        if o.get("grupo") == "eletronicos":
            return d >= 55 or (d >= 40 and bom)
        return d >= 50 or (d >= 30 and bom) or (d >= 20 and viral)
    return (s.get("top") and (d >= 20 or s.get("otima", 0) >= 3)) or s.get("otima", 0) >= 5 or d >= 55 \
        or (d >= 35 and s.get("curtidas", 0) >= 8)


def _link_loja(link_fonte: str, cli: httpx.Client) -> str | None:
    """Promobit /Redirect/to/ID/ → URL real da loja, sem o código de afiliado deles."""
    h = cli.get(link_fonte).text
    m = re.search(r"""href="(https?://(?!www\.promobit)[^"]+)\"""", h) or re.search(r"l = '(https?://[^']+)'", h)
    if not m:
        return None
    return limpar_link(_html.unescape(m.group(1)), cli)


ENCURTADORES = ("meli.la", "s.shopee.com.br", "shope.ee", "amzn.to", "tidd.ly", "bit.ly", "onelink.me")
RASTREIO = re.compile(r"tag|ref_|matt_[a-z_]+|utm_[a-z]+|af_[a-z_]+|smtt|sp_atk|xptdk|mmp_pid|uls_trackid|gads_t_sig|lp|c|"
                      r"pid|clickid|tracking_id|forceInApp|promoter_id|partner_id|seller_id_divulgador")


def limpar_link(url: str, cli: httpx.Client | None = None) -> str | None:
    """Endereço real do produto, SEM afiliado de terceiros (senão a comissão vai pra eles):
    abre encurtadores (meli.la, s.shopee…), extrai o destino de deeplinks (linksynergy murl/u) e tira tag/utm."""
    from urllib.parse import parse_qs, parse_qsl, unquote, urlencode, urlparse, urlsplit, urlunsplit
    u = urlparse(url)
    if "linksynergy" in u.netloc or "awin" in u.netloc:
        q = parse_qs(u.query)
        destino = (q.get("murl") or q.get("ued") or q.get("u") or [None])[0]
        if destino and destino.startswith("http"):
            url = unquote(destino)
    elif any(e in u.netloc for e in ENCURTADORES) and cli is not None:
        # só lê o cabeçalho Location do encurtador — não entra na página da loja (ML/Amazon proíbem robôs de IA)
        for _ in range(5):
            try:
                r = cli.get(url, follow_redirects=False)
            except httpx.HTTPError:
                break
            loc = r.headers.get("location")
            if not (300 <= r.status_code < 400 and loc):
                break
            url = loc if loc.startswith("http") else str(r.url.join(loc))
            if not any(e in urlparse(url).netloc for e in ENCURTADORES):
                break
    # tira parâmetros de rastreio/afiliado de terceiros montando a query de novo (o regex antigo perdia o "?" quando
    # 2+ parâmetros removidos vinham antes de um mantido)
    partes = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(partes.query, keep_blank_values=True) if not RASTREIO.fullmatch(k)
             and not (k == "q" and "belezanaweb" in partes.netloc)]  # ?q= da Beleza na Web = rastreio de busca
    url = urlunsplit(partes._replace(query=urlencode(query)))
    url = re.sub(r"/divulgador/oferta/(\w+)/", r"/p/\1/", url)  # Magalu divulgador → página normal do produto
    m = re.search(r"shopee\.com\.br/(?:opaanlp|product)/(\d+)/(\d+)", url)
    if m:
        url = f"https://shopee.com.br/product/{m.group(1)}/{m.group(2)}"
    if "/social/" in url:  # meli.la de terceiros abre uma vitrine deles, não o produto → sem link (não posta)
        return None
    return url


NAO_PERMITE_WHATSAPP = re.compile(r"mercadolivre\.com\.br/|mercadolibre\.com/", re.I)


def link_afiliado(url: str | None, canal: str = "site") -> str | None:
    """Troca pelo NOSSO código de afiliado quando existir (config/segredos.json → afiliados).
    canal="whatsapp": programas que PROÍBEM WhatsApp/Telegram (ML Afiliados — página oficial
    mercadolivre.com.br/l/afiliados-pode-compartilhar, conferida 30/09) saem como link comum, SEM a nossa etiqueta
    (a conta de afiliada é a mesma da loja no ML: não arriscar)."""
    if not url:
        return None
    if canal == "whatsapp" and NAO_PERMITE_WHATSAPP.search(url):
        return url
    af = config.segredos().get("afiliados") or {}
    m = re.search(r"amazon\.com\.br/(?:.*/)?(?:dp|gp/product)/([A-Z0-9]{10})", url)  # /gp/product/ saía sem a tag
    if m and af.get("amazon_tag"):
        return f"https://www.amazon.com.br/dp/{m.group(1)}?tag={af['amazon_tag']}"
    m = re.search(r"magazineluiza\.com\.br/(.+/p/\w+/.*)$", url)
    if m and af.get("magalu_loja"):  # Parceiro Magalu: a lojinha do usuário no magazinevoce
        return f"https://www.magazinevoce.com.br/magazine{af['magalu_loja']}/{m.group(1)}"
    if re.search(r"mercadolivre\.com\.br/", url) and af.get("ml_tool"):  # ML Afiliados: mesmos parâmetros do meli.la oficial
        base = url.split("#")[0]
        return f"{base}{'&' if '?' in base else '?'}matt_word={af.get('ml_etiqueta', 'bellalucce')}&matt_tool={af['ml_tool']}"
    m = re.search(r"shopee\.com\.br/product/\d+/(\d+)", url)
    if m:  # Shopee Afiliados (permite WhatsApp): o link curto da Open API, guardado na coleta
        r = db.consultar("SELECT sinais FROM ofertas WHERE id = ?", (f"shpaf:{m.group(1)}",))
        if r and (curto := json.loads(r[0]["sinais"] or "{}").get("offer_link")):
            return curto
        if curto := _shopee_curto(url):  # produto da Shopee vindo de outra fonte (Promobit): gera o NOSSO link
            return curto
    return url  # ML: link curto só pela ferramenta do painel


def _shopee_curto(url: str) -> str | None:
    """Link de afiliada da Shopee pela Open API (só com AppID/Senha; cache em dados/achadinhos/shopee_links.json)."""
    try:
        from vendas.integracoes import shopee_afiliados as sa
    except ImportError:  # radar na nuvem do GitHub não leva a integração
        return None
    if not sa.credenciais():
        return None
    arq = config.DADOS / "achadinhos" / "shopee_links.json"
    cache = json.loads(arq.read_text(encoding="utf-8")) if arq.exists() else {}
    if url not in cache:
        try:
            curto = sa.link_curto(url)
        except Exception:  # noqa: BLE001 — sem o link curto, vai o link comum
            return None
        if not (curto and re.match(r"https://s\.shopee\.com\.br/\w+$", curto)):
            return None
        cache[url] = curto
        arq.write_text(json.dumps(cache, indent=0), encoding="utf-8")
    return cache[url]


# ---------------- ciclo ----------------
def coletar(log=print) -> dict:
    """1 ciclo (roda a cada 20 min): lê as fontes, pontua, grava, resolve o link só das aprovadas novas."""
    _tabela()
    agora = datetime.now().isoformat(sep=" ", timespec="seconds")
    vistos, erros = [], []
    with httpx.Client(headers=UA, timeout=25, follow_redirects=True) as cli:
        for cam in PAGINAS_PROMOBIT:
            try:
                vistos += _promobit(cam, cli)
            except Exception as e:
                erros.append(f"promobit{cam}: {e}")
            time.sleep(1.5)  # educado com o site
        novos = aprov = 0
        antes = {r["id"]: r for r in db.consultar("SELECT id, link_loja, link_tentado_em FROM ofertas WHERE fonte = 'promobit'")}
        ontem = (datetime.now() - timedelta(hours=24)).isoformat(sep=" ", timespec="seconds")
        linhas = []
        # fase 1 (internet, SEM segurar o banco): pontua e resolve o link só das aprovadas novas
        for o in {x["id"]: x for x in vistos}.values():
            o["score"], ok = pontuar(o), aprovada(o)
            ant = antes.get(o["id"])
            link, tentado = (ant["link_loja"] if ant else None) or o.get("link_loja"), (ant or {}).get("link_tentado_em")
            link = limpar_link(link) if link else None  # link guardado (cache da nuvem) passa pela limpeza atual
            if ok and not link and o.get("link_fonte", "").startswith(PROMOBIT) and not (tentado and tentado > ontem):
                try:
                    link = _link_loja(o["link_fonte"], cli)
                    time.sleep(1)
                except Exception as e:
                    erros.append(f"link {o['id']}: {e}")
                tentado = None if link else agora
            if link:  # confere de novo com o link real: página de UM produto e sem afiliado de terceiros
                o["link_loja"] = link
                ok = aprovada(o)
            linhas.append((o["id"], o["fonte"], o.get("loja"), o["titulo"], o["grupo"], o.get("preco"), o.get("preco_antigo"),
                           o.get("desconto"), o.get("cupom"), o.get("foto"), o.get("link_fonte"), link, o.get("nota"),
                           o.get("vendidos"), json.dumps(o.get("sinais"), ensure_ascii=False), o["score"], int(ok),
                           agora, agora, tentado))
            novos += ant is None
            aprov += ok
    # fase 2 (rápida): grava tudo. Linha que já existia (ex.: esqueleto do cache da nuvem, só com o link) é COMPLETADA —
    # antes só preço/score eram atualizados e a oferta ficava sem título/grupo/loja → reprovada (site sem Amazon/Magalu)
    with db.conectar() as con:
        con.executemany("""INSERT INTO ofertas (id, fonte, loja, titulo, grupo, preco, preco_antigo, desconto, cupom, foto,
                             link_fonte, link_loja, nota, vendidos, sinais, score, aprovada, visto_em, atualizado_em,
                             link_tentado_em)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                           ON CONFLICT(id) DO UPDATE SET loja=excluded.loja, titulo=excluded.titulo, grupo=excluded.grupo,
                             preco=excluded.preco, preco_antigo=excluded.preco_antigo, desconto=excluded.desconto,
                             cupom=excluded.cupom, foto=COALESCE(excluded.foto, foto), link_fonte=excluded.link_fonte,
                             nota=excluded.nota, vendidos=excluded.vendidos, sinais=excluded.sinais, score=excluded.score,
                             aprovada=excluded.aprovada, link_loja=COALESCE(excluded.link_loja, link_loja),
                             atualizado_em=excluded.atualizado_em, link_tentado_em=excluded.link_tentado_em""", linhas)
        _guardar_precos(con, [(x[0], x[5]) for x in linhas])
    reavaliar()
    problemas = auditar()
    res = {"vistas": len(vistos), "novas": novos, "aprovadas": aprov, "auditoria": len(problemas), "erros": erros}
    log(f"achadinhos: {res}")
    return res


def _vendidos_num(txt: str | None) -> int:
    """'1000' → 1000; '50mil' / '50 mil' → 50000."""
    m = re.match(r"\s*([\d.,]+)\s*(mil)?", txt or "")
    if not m:
        return 0
    n = float(m.group(1).replace(".", "").replace(",", "."))
    return int(n * (1000 if m.group(2) else 1))


def salvar_ml_afiliados(itens: list[dict]) -> dict:
    """Ofertas da Central de Afiliados do ML (js/ml_afiliados.js, via receptor). Fonte oficial para afiliados: link =
    página do anúncio + nosso matt_tool (link_afiliado). Mesmo filtro de qualidade das outras fontes."""
    _tabela()
    agora = datetime.now().isoformat(sep=" ", timespec="seconds")
    novos = aprov = 0
    with db.conectar() as con:
        for it in itens:
            titulo = (it.get("titulo") or "").strip()
            if not (it.get("id") and titulo and it.get("preco")):
                continue
            grupo = _grupo_final(it.get("grupo") or _grupo(titulo), titulo)
            vend = _vendidos_num(it.get("vendidos"))
            sinais = {"tipo": "NORMAL", "comissao": it.get("comissao") or 0, "comissao_extra": bool(it.get("comissao_extra")),
                      "destaque": it.get("destaque") or "", "vendidos_num": vend}
            o = {"id": f"mlaf:{it['id']}", "fonte": "ml_afiliados", "loja": "Mercado Livre", "titulo": titulo, "grupo": grupo,
                 "preco": it["preco"], "preco_antigo": it.get("preco_antigo"), "desconto": int(it.get("desconto") or 0),
                 # 30/09 segurança: foto só do CDN do ML (o JS monta assim; nada de caminho local/rede interna)
                 "cupom": None, "foto": it.get("foto") if re.match(r"https://http2\.mlstatic\.com/",
                                                                    it.get("foto") or "") else None,
                 "link_fonte": None, "link_loja": limpar_link(it.get("url")),
                 "nota": it.get("nota"), "vendidos": f"{vend} vendidos" if vend else None, "sinais": sinais}
            o["score"], ok = pontuar(o), aprovada(o)
            ant = con.execute("SELECT 1 FROM ofertas WHERE id = ?", (o["id"],)).fetchone()
            con.execute("""INSERT INTO ofertas (id, fonte, loja, titulo, grupo, preco, preco_antigo, desconto, cupom, foto,
                             link_fonte, link_loja, nota, vendidos, sinais, score, aprovada, visto_em, atualizado_em)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                           ON CONFLICT(id) DO UPDATE SET preco=excluded.preco, preco_antigo=excluded.preco_antigo,
                             desconto=excluded.desconto, sinais=excluded.sinais, score=excluded.score, grupo=excluded.grupo,
                             aprovada=excluded.aprovada, foto=excluded.foto, nota=excluded.nota, vendidos=excluded.vendidos,
                             atualizado_em=excluded.atualizado_em""",
                        (o["id"], o["fonte"], o["loja"], titulo, grupo, o["preco"], o["preco_antigo"], o["desconto"], None,
                         o["foto"], None, o["link_loja"], o["nota"], o["vendidos"], json.dumps(sinais, ensure_ascii=False),
                         o["score"], int(ok), agora, agora))
            _guardar_precos(con, [(o["id"], o["preco"])])
            novos += ant is None
            aprov += ok
    problemas = auditar()
    return {"salvos": len(itens), "novos": novos, "aprovados": aprov, "auditoria": len(problemas)}


def salvar_shopee_afiliados(itens: list[dict]) -> dict:
    """Ofertas da Open API de Afiliados da Shopee (integracoes/shopee_afiliados.py). link_loja = página do produto;
    o link de afiliada (offerLink, s.shopee…) fica em sinais e entra no post por link_afiliado()."""
    _tabela()
    agora = datetime.now().isoformat(sep=" ", timespec="seconds")
    novos = aprov = 0
    with db.conectar() as con:
        for it in {x["id"]: x for x in itens if x.get("id")}.values():
            titulo = limpar_titulo((it.get("titulo") or "").strip())
            if not (titulo and it.get("preco")):
                continue
            grupo = _grupo_final(_grupo(titulo), titulo)
            vend = int(it.get("vendidos") or 0)
            sinais = {"tipo": "NORMAL", "comissao": it.get("comissao") or 0, "vendidos_num": vend,
                      "oficial": bool(it.get("oficial")), "loja_nome": it.get("loja_nome") or "",
                      "offer_link": it.get("offer_link") if re.match(r"https://s\.shopee\.com\.br/\w+$",
                                                                     it.get("offer_link") or "") else None}
            link = it.get("url") if it.get("url") and pagina_de_produto(it["url"]) else None
            foto = it.get("foto") if re.match(r"https://(?:cf|down-br\.img)\.(?:shopee|susercontent)\.com(?:\.br)?/",
                                              it.get("foto") or "") else None
            o = {"id": f"shpaf:{it['id']}", "fonte": "shopee_afiliados", "loja": "Shopee", "titulo": titulo,
                 "grupo": grupo, "preco": it["preco"], "preco_antigo": it.get("preco_antigo"),
                 "desconto": int(it.get("desconto") or 0), "cupom": None, "foto": foto, "link_fonte": None,
                 "link_loja": link, "nota": it.get("nota"), "vendidos": f"{vend} vendidos" if vend else None,
                 "sinais": sinais}
            o["score"], ok = pontuar(o), aprovada(o) and bool(link)
            ant = con.execute("SELECT 1 FROM ofertas WHERE id = ?", (o["id"],)).fetchone()
            con.execute("""INSERT INTO ofertas (id, fonte, loja, titulo, grupo, preco, preco_antigo, desconto, cupom, foto,
                             link_fonte, link_loja, nota, vendidos, sinais, score, aprovada, visto_em, atualizado_em)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                           ON CONFLICT(id) DO UPDATE SET preco=excluded.preco, preco_antigo=excluded.preco_antigo,
                             desconto=excluded.desconto, sinais=excluded.sinais, score=excluded.score, grupo=excluded.grupo,
                             aprovada=excluded.aprovada, foto=excluded.foto, nota=excluded.nota, vendidos=excluded.vendidos,
                             link_loja=excluded.link_loja, atualizado_em=excluded.atualizado_em""",
                        (o["id"], o["fonte"], o["loja"], titulo, grupo, o["preco"], o["preco_antigo"], o["desconto"], None,
                         foto, None, link, o["nota"], o["vendidos"], json.dumps(sinais, ensure_ascii=False),
                         o["score"], int(ok), agora, agora))
            _guardar_precos(con, [(o["id"], o["preco"])])
            novos += ant is None
            aprov += ok
    return {"salvos": len(itens), "novos": novos, "aprovados": aprov}


def melhores(horas: int = 24, limite: int = 60, grupo: str | None = None) -> list[dict]:
    """Ofertas aprovadas vistas nas últimas `horas`, das melhores para as piores."""
    _tabela()
    sql = """SELECT * FROM ofertas WHERE aprovada = 1 AND atualizado_em >= datetime('now','localtime', ?)"""
    args: list = [f"-{horas} hours"]
    if grupo:
        sql += " AND grupo = ?"
        args.append(grupo)
    sql += " ORDER BY score DESC, atualizado_em DESC LIMIT ?"
    args.append(limite)
    return db.consultar(sql, tuple(args))


# ---------------- vitrine (página com todas as promoções, por categoria) ----------------
NOMES = {"beleza": "Beleza", "cabelo": "Cabelo", "perfume": "Perfumes", "esporte": "Fitness", "casa": "Casa",
         "infantil": "Bebê & Infantil", "moda": "Moda", "pet": "Pet", "eletronicos": "Eletrônicos"}
SITE = config.DADOS / "achadinhos" / "site"


def _brl(v) -> str:
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") if v else ""


def _brl_zap(v) -> str:
    """Preço para o WhatsApp: no celular, "234,12" (5+ dígitos) vira link VERDE de telefone (30/09, dona). Um
    separador invisível (U+2060) depois da vírgula quebra a sequência sem mudar o que se vê."""
    return _brl(v).replace(",", ",⁠")


def vitrine(horas: int = 36) -> str:
    """Gera dados/achadinhos/site/index.html (celular primeiro). Só ofertas aprovadas e com link."""
    # todas as ofertas ativas (o que saiu de promoção não é revisto na coleta e some em até `horas`), o melhor de cada tipo
    ofs = sem_repetidos([o for o in melhores(horas, 20000) if o["link_loja"] and o.get("grupo") in NOMES])
    grupos = [g for g in NOMES if any(o["grupo"] == g for o in ofs)]
    e = _html.escape
    dados = [{"g": o["grupo"], "l": o.get("loja") or "", "t": limpar_titulo(o["titulo"])[:90], "p": _brl(o["preco"]),
              "a": _brl(o["preco_antigo"]) if o.get("preco_antigo") else "", "d": o.get("desconto") or 0,
              "c": o.get("cupom") or "", "f": o.get("foto") or "", "u": link_afiliado(o["link_loja"]) or ""} for o in ofs]
    mins = minimos()
    for o, x in zip(ofs, dados):  # selo "menor preço em N dias" (só quando vale: 7+ dias e abaixo do menor anterior)
        if o["id"] in mins and o["preco"] < mins[o["id"]][1]:
            x["h"] = mins[o["id"]][0]
    (SITE).mkdir(parents=True, exist_ok=True)
    (SITE / "ofertas.json").write_text(json.dumps(dados, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    # a página desenha os cards a partir de ofertas.json (60 por vez)
    abas ="".join(f'<button data-g="{g}">{NOMES[g]}</button>' for g in grupos)
    agora = datetime.now().strftime("%d/%m %H:%M")
    # faixa "Da nossa loja": produtos da Bella Lucce com estoque (config/loja.json — o PC gera e manda para a nuvem)
    arq_loja = config.CONFIG / "loja.json"
    loja = json.loads(arq_loja.read_text(encoding="utf-8")) if arq_loja.exists() else []
    faixa = ""
    if loja:
        itens_loja = "".join(
            f'<a class="card" href="{e(next(iter(p["links"].values())))}" target="_blank" rel="noopener">'
            f'<div class="img"><img loading="lazy" src="{e(p["foto"])}" alt=""></div><div class="loja">Bella Lucce</div>'
            f'<div class="tit">{e(p["nome"])}</div><div class="preco"><b>{e(p["preco"])}</b></div>'
            f'<div class="btn">Ver na loja</div></a>' for p in loja)
        faixa = f'<section class="nossa"><h2>💖 Da nossa loja</h2><div class="trilho">{itens_loja}</div></section>'
    grupo = canais().get("grupo_whatsapp")
    entrar = (f'<a class="entrar" href="{e(grupo)}" target="_blank" rel="noopener">💬 Entrar no grupo de achadinhos do WhatsApp</a>'
              if grupo else "")
    # 30/09 segurança: CSP — o site não carrega nada de fora (só fotos https); se algum texto escapasse do E(), o navegador
    # ainda bloquearia script/formulário/objeto de terceiros.
    pagina = f"""<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'self'; img-src 'self' https: data:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'"><meta name="referrer" content="strict-origin-when-cross-origin">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Achadinhos Bella Lucce</title>
<link rel="icon" href="favicon.png"><meta property="og:image" content="https://bellalucce.github.io/logo.png">
<meta property="og:title" content="Achadinhos Bella Lucce ✨"><meta property="og:type" content="website">
<meta property="og:description" content="Promoções de beleza, cabelo, perfume, fitness e casa conferidas a cada 20 minutos.">
<meta name="description" content="Promoções de beleza, cabelo, perfume, fitness e casa conferidas a cada 20 minutos — Achadinhos Bella Lucce.">
<style>
:root{{--rosa:#e85d8c;--fundo:#fff6f9;--txt:#222}}*{{box-sizing:border-box}}body{{margin:0;font-family:system-ui,Segoe UI,Arial;background:var(--fundo);color:var(--txt)}}
header{{background:var(--rosa);color:#fff;padding:18px 16px;text-align:center}}header h1{{margin:0;font-size:22px}}header p{{margin:6px 0 0;font-size:13px;opacity:.9}}
nav{{display:flex;gap:8px;overflow-x:auto;padding:12px 16px;position:sticky;top:0;background:var(--fundo);z-index:2}}
nav button{{border:1px solid var(--rosa);background:#fff;color:var(--rosa);border-radius:20px;padding:7px 14px;font-weight:600;white-space:nowrap}}
nav button.on{{background:var(--rosa);color:#fff}}
main{{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:12px;padding:0 16px 30px}}
.card{{background:#fff;border-radius:14px;overflow:hidden;text-decoration:none;color:inherit;box-shadow:0 1px 4px #0001;display:flex;flex-direction:column}}
.img{{position:relative;aspect-ratio:1;background:#fafafa}}.img img{{width:100%;height:100%;object-fit:contain}}
.selo{{position:absolute;top:8px;left:8px;background:var(--rosa);color:#fff;font-weight:700;font-size:13px;padding:3px 8px;border-radius:10px}}
.loja{{font-size:11px;color:#888;padding:8px 10px 0}}.tit{{font-size:13px;padding:4px 10px;line-height:1.3;flex:1}}
.preco{{padding:0 10px;font-size:16px}}.preco s{{color:#999;font-size:12px;margin-right:6px}}.preco b{{color:#1a8a3a}}
.hist{{margin:4px 10px 0;font-size:12px;font-weight:600;color:#0a7a3f}}
.cupom{{margin:6px 10px 0;font-size:12px;background:#fff0f5;border:1px dashed var(--rosa);border-radius:8px;padding:4px 6px}}
.btn{{margin:10px;background:var(--rosa);color:#fff;text-align:center;border-radius:10px;padding:9px;font-weight:700}}
.lnk{{display:flex;flex-direction:column;flex:1;text-decoration:none;color:inherit}}
.share{{margin:-4px 10px 10px;text-align:center;font-size:12px;font-weight:600;color:#1f9d55;text-decoration:none}}
footer{{text-align:center;font-size:11px;color:#999;padding:0 16px 24px}}
.logo{{width:72px;height:72px;border-radius:50%;border:2px solid #fff;display:block;margin:0 auto 8px}}
.busca{{display:flex;gap:10px;align-items:center;padding:0 16px 12px}}.busca input{{flex:1;border:1px solid #f1c6d6;border-radius:20px;padding:9px 14px;font-size:14px}}.busca span{{font-size:12px;color:#888;white-space:nowrap}}
#mais{{display:block;margin:0 auto 24px;border:0;background:var(--rosa);color:#fff;font-weight:700;border-radius:22px;padding:12px 22px;font-size:15px}}
.entrar{{display:block;margin:12px auto 0;max-width:420px;background:#25d366;color:#fff;text-decoration:none;font-weight:700;border-radius:24px;padding:11px 16px}}
.nossa{{padding:12px 16px 0}}.nossa h2{{font-size:17px;margin:4px 0 10px;color:var(--rosa)}}
.trilho{{display:grid;grid-auto-flow:column;grid-auto-columns:minmax(150px,170px);gap:12px;overflow-x:auto;padding-bottom:8px}}
</style></head><body>
<header><img class="logo" src="logo.png" alt="bella lucce"><h1>Achadinhos Bella Lucce ✨</h1><p>As melhores promoções do dia, conferidas a cada 20 minutos · atualizado {agora}</p>{entrar}</header>
{faixa}
<nav><button class="on" data-g="">Tudo</button>{abas}</nav>
<div class="busca"><input id="q" type="search" placeholder="Buscar oferta (ex.: sérum, legging, fralda)"><span id="n"></span></div>
<main id="lista"><p>Carregando ofertas…</p></main>
<button id="mais">Carregar mais ofertas</button>
<p style="text-align:center;margin:0 0 14px"><a href="https://www.instagram.com/abella.lucce/" target="_blank" rel="noopener" style="color:var(--rosa);font-weight:700;text-decoration:none">✨ Siga a gente no Instagram: @abella.lucce</a></p>
<footer>#publi · Preços e cupons podem mudar a qualquer momento (conferidos na data da oferta). Links de afiliado: a loja pode nos pagar uma comissão, sem custo para você. Como Associado da Amazon, a Bella Lucce recebe por compras qualificadas.</footer>
<script>
let T=[],F=[],N=0,G='',Q='';const P=60,E=s=>String(s).replace(/[&<>"]/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}})[c]);
const semAf=u=>/mercadolivre\\.com\\.br\\//.test(u)?u.replace(/[?&]matt_(word|tool)=[^&]*/g,'').replace(/[?&]$/,''):u;  // ML Afiliados proíbe WhatsApp
const zap=o=>'https://wa.me/?text='+encodeURIComponent(o.t+' por '+o.p+' 👉 '+semAf(o.u)+'\\n\\nMais achadinhos: {SITE_URL}');
const card=o=>`<div class="card"><a class="lnk" href="${{E(o.u)}}" target="_blank" rel="nofollow sponsored noopener"><div class="img">${{o.f?`<img loading="lazy" src="${{E(o.f)}}" alt="">`:''}}${{o.d?`<span class="selo">-${{o.d}}%</span>`:''}}</div><div class="loja">${{E(o.l)}}</div><div class="tit">${{E(o.t)}}</div><div class="preco">${{o.a?`<s>${{o.a}}</s>`:''}}<b>${{o.p}}</b></div>${{o.h?`<div class="hist">📉 menor preço em ${{o.h}} dias</div>`:''}}${{o.c?`<div class="cupom">Cupom: <b>${{E(o.c)}}</b></div>`:''}}<div class="btn">Pegar oferta</div></a><a class="share" href="${{E(zap(o))}}" target="_blank" rel="noopener">Compartilhar no WhatsApp</a></div>`;
const filtrar=()=>{{const q=Q.normalize('NFD').replace(/[\\u0300-\\u036f]/g,'').toLowerCase();F=T.filter(o=>(!G||o.g===G)&&(!q||o.t.normalize('NFD').replace(/[\\u0300-\\u036f]/g,'').toLowerCase().includes(q)));N=0;document.getElementById('lista').innerHTML='';mais();document.getElementById('n').textContent=F.length+' ofertas'}};
const mais=()=>{{document.getElementById('lista').insertAdjacentHTML('beforeend',F.slice(N,N+P).map(card).join('')||(N?'':'<p>Nenhuma oferta aqui agora.</p>'));N+=P;document.getElementById('mais').style.display=N<F.length?'':'none'}};
document.getElementById('mais').onclick=mais;document.getElementById('q').oninput=e=>{{Q=e.target.value;filtrar()}};
document.querySelectorAll('nav button').forEach(b=>b.onclick=()=>{{document.querySelectorAll('nav button').forEach(x=>x.classList.toggle('on',x===b));G=b.dataset.g;filtrar()}});
fetch('ofertas.json?v='+Date.now()).then(r=>r.json()).then(d=>{{T=d;filtrar()}});
</script>
</body></html>"""
    SITE.mkdir(parents=True, exist_ok=True)
    (SITE / "index.html").write_text(pagina, encoding="utf-8")
    return str(SITE / "index.html")


# ---------------- posts do grupo (formato estudado nos grupos que o usuário segue) ----------------
# Ganchos por BENEFÍCIO do produto, na voz de amiga (estudo dos grupos que funcionam, 29/09 —
# cerebro/20-Divulgacao/2026-09-29-Estudo-grupos-de-achadinhos.md). O primeiro padrão que casar com o título vence.
# Gancho de benefício = o TIPO do produto (30/09, auditoria das 1.327 ofertas depois da balança que virou "LOOK DE
# TREINO"): vale o termo que aparece PRIMEIRO no título (em marketplace o tipo vem na frente), só antes de
# "com/para/+" (depois disso é característica), palavra inteira (+ plural) e sem "de/para/porta…" logo antes.
# Frase None = tipo conhecido sem gancho próprio (aparelho, ferramenta…) → usa frase neutra, nunca um termo de depois.
BENEFICIOS = [
    (r"protetor solar|protetor corporal|bronzeador|fps ?\d+", "PELE PROTEGIDA SEM PESAR NO BOLSO ☀️"),
    (r"m[áa]scara de c[íi]lios|r[íi]mel", "CÍLIOS DE BONECA POR ESSE PREÇO? 👀"),
    (r"s[ée]rum|retinal|retinol|vitamina c|niacinamida|[áa]cido hialur\w*|booster|ampoule|antirrugas|anti-?idade|"
     r"antissinais|hidratante facial|creme facial|clareador facial|sabonete facial|kit skincare|[áa]gua micelar|micelar|"
     # 30/09: coreanas (toner/máscara facial/cleansing balm); toner de impressora fora ("Toner HP 85A", "CF283A")
     r"t[ôo]nico facial|toner(?!\s*(?:hp|brother|samsung|lexmark|xerox|ricoh|kyocera|canon|epson|compat|p/|para imp|"
     r"de imp|tn-?\d|[a-z]{1,3}\d{2,}))|m[áa]scara facial|sheet mask|deep mask|cleansing balm|balm de limpeza|"
     r"[óo]leo de limpeza",  # "Creamy Skincare" = marca
     "PELE LISINHA E COM VIÇO, AMIGA ✨"),
    (r"base|corretivo|concealer|p[óo] compacto|primer|fixador de maquiagem|bb cream|cc cream|blush|iluminador|contorno|"
     r"bronzer|cushion", "PELE DE FILTRO NA VIDA REAL 💄"),
    (r"paleta de sombras?|sombras?|delineador|l[áa]pis de olho|kajal", "OLHAR PODEROSO NO PRECINHO 👁️"),
    (r"gloss|batom|batons|lip ?tint|lip ?oil|lip ?balm|balm|hidratante labial|l[áa]bios|lips|tint|lip sleeping|"
     r"lip mask|m[áa]scara labial", "BOCA LINDA GASTANDO POUCO 💋"),
    (r"escova secadora|secador|secadora(?! de roupa)|escova rotativa|escova alisadora", "CABELO LINDO E SECO RAPIDINHO 💨"),
    (r"chapinha|prancha", "LISO PERFEITO EM MINUTOS ✨"),
    (r"cachos|cacheador|cachead\w*|fitagem|babyliss|modelador de cachos|modelador(?= curves| de ondas| ondas)|ondas perfeitas",
     "CACHOS DE SALÃO EM CASA 🌀"),
    (r"m[áa]scara capilar|[óo]leo capilar|s[ée]rum capilar|shampoo|condicionador|ampola|leave-?in|elseve|"
     r"k[ée]rastase|wella|truss|lola cosmetics|salon line|pantene|tresemm[ée]|cadiveu|si[àa]ge|keune|braé|matrix",
     "CABELO MACIO DE SALÃO EM CASA 💆‍♀️"),
    (r"lat+af+a|armaf|al wataniah|maison alhambra|[áa]r[áa]be|asad|yara|fakhar|khamrah|club de nuit|french avenue|"
     r"al wesal|durrat al aroos|sabah al ward", "CHEIRO DE GRIFE, PREÇO DE ÁRABE 🔥"),
    (r"perfume|eau de|parfum|body splash|splash|col[ôo]nia|deo col[ôo]nia|edp|edt", "CHEIROSA O DIA INTEIRO, AMIGA 🌸"),
    (r"legging|^top|top fitness|conjunto fitness|short fitness|cal[çc]a fitness|macaquinho|roupa de academia",
     "LOOK DE TREINO QUE VALORIZA TUDO 🍑"),
    (r"whey|creatina|pr[ée]-?treino|col[áa]geno", "SUPLEMENTO BOM COM PREÇO DE AMIGA 💪"),
    (r"bicicleta ergom\w*|esteira", "ACADEMIA EM CASA NO PRECINHO 🚴‍♀️"),
    (r"jogo de cama|len[çc]ol|len[çc][óo]is|edredom|travesseiro|colcha", "CAMA DE HOTEL NA SUA CASA 🛏️"),
    (r"organizador|caixa organizadora|cesto|porta(?! beb[êe])|expositor|sacos? (?:de )?armazenamento|saco a v[áa]cuo",
     "ORGANIZE SUA BAGUNÇA 🧺"),
    (r"toalha|toalh[ãa]o", "TOALHA FOFINHA DE HOTEL 🛁"),
    (r"panela(?! (?:de )?cera)|frigideira|air ?fryer|fritadeira|mixer|processador de alimentos|liquidificador|cafeteira|"
     r"sanduicheira|batedeira|potes? herm[ée]ticos?|formas? de silicone|travessas?", "COZINHA LINDA GASTANDO POUCO 🍳"),
    (r"fralda|len[çc]os? umedecidos?|beb[êe]|baby|porta beb[êe]", "MAMÃE, CORRE QUE TÁ BARATO 👶"),  # "canguru": moletom
    (r"bolsa(?:s)?(?! (?:de )?(?:t[ée]rmica|isot[ée]rmica|ferramentas?|maternidade|marmita|lancheira))",
     "A BOLSA QUE COMBINA COM TUDO 👜"),
    (r"t[êe]nis|sand[áa]lia|chinelo|tamanco|rasteira|pantufa|sapatilha|bota|scarpin|mocassim", "PÉ LINDO E CONFORTÁVEL 👟"),
    (r"vivara|pandora|life by vivara", "JOIA DE MARCA COM DESCONTO 💎"),  # R$ 500 não é "sem gastar muito"
    (r"brinco|colar|colares|anel|an[ée]is|pulseira|rel[óo]gio|semijoia|conjunto de joias|alian[çc]a|pingente", "BRILHO NO LOOK SEM GASTAR MUITO ✨"),
    (r"cal[çc]a|vestido|blusa|saia|macac[ãa]o|cropped|pijama|suti[ãa]|calcinha|camiseta|camisa|blazer|moletom|jaqueta|"
     r"casaco|regata|shorts?|bermuda|conjunto feminino|body(?! splash)|cardig[ãa]", "LOOK NOVO GASTANDO POUCO 👗"),
    (r"whiskas|pedigree|golden|premier|ra[çc][ãa]o|areia", "O PET AGRADECE E O BOLSO TAMBÉM 🐾"),
    (r"creme hidratante|hidratante corporal|lo[çc][ãa]o hidratante|lo[çc][ãa]o corporal|body lotion|[óo]leo corporal|"
     r"bio oil|manteiga corporal|hidratante desodorante", "PELE MACIA O DIA INTEIRO 🧴"),
    # 30/09 (dona: "além de correto, tem que ser humano"): tipos que ficavam com frase genérica agora têm a sua
    (r"mai[ôo]|biqu[íi]ni|sa[íi]da de praia|canga", "PRONTA PRO VERÃO 👙"),
    (r"brinquedo|boneca|pel[úu]cia|carrinho de controle|carrinhos|pista|caminh[ãa]o (?:de )?controle|patinete|lego|blocos de montar|"
     r"quebra-?cabe[çc]a|massinha|maquiagem (?:infantil|crian[çc]a)|reborn|hama beads", "PRESENTE PROS PEQUENOS 🧸"),
    (r"roupa infantil|conjunto infantil|menin[oa]", "ROUPINHA FOFA PROS PEQUENOS 🧸"),
    (r"garrafa t[ée]rmica|copo t[ée]rmico|caneca t[ée]rmica|squeeze|tumbler|stanley", "GELADINHO OU QUENTINHO O DIA TODO 🧊"),
    (r"massageador", "ALÍVIO PRO CORPO CANSADO 💆‍♀️"),
    (r"depilador[a]?|cera quente|termocera|aquecedor de cera|depila[çc][ãa]o", "DEPILAÇÃO EM CASA, SEM SOFRER ✨"),
    (r"smart ?watch|rel[óo]gio inteligente|fones? de ouvido|fone bluetooth|caixa de som|caixinha de som|"
     r"carregador port[áa]til|power ?bank", "TECNOLOGIA NO PRECINHO 📱"),
    (r"[óo]culos de sol|[óo]culos", "PROTEÇÃO COM ESTILO 😎"),
    # tipo conhecido SEM frase própria → frase de reserva da categoria (nunca um termo de depois no título)
    (r"balan[çc]a|ferramentas?|trampolim|bolsa (?:de )?(?:t[ée]rmica|isot[ée]rmica|ferramentas?|maternidade)|mochila|"
     r"aspirador|ventilador|carrinho|marmita|lancheira|m[áa]scara (?:de )?led|led facial|garrafa|copo|umidificador|"
     r"projetor|controle|barraca|mesa", None),
]
_BENEF = [(re.compile(rf"(?<!\w)(?:{pad})(?:s|es)?(?!\w)", re.I), frase) for pad, frase in BENEFICIOS]
CONECTORES = {"com", "c/", "para", "p/", "pra", "+", "|"}  # "-" não: "Brinox - Jogo de Panelas"
COLETIVOS = {"jogo", "conjunto", "kit", "par", "pares", "pack", "combo", "trio", "duo", "set", "caixa", "box"}
MARCAS_SKINCARE = re.compile(r"(?<!\w)(?:principia|creamy|sallve|la roche|cetaphil|neutrogena|kokeshi|laneige|medicube|"
                             r"anua|cosrx|skin1004|beauty of joseon|av[èe]ne|isdin|eucerin|bior[ée]|dermachem|"
                             r"celimax|garnier|vichy|payot|bioderma|"
                             # 30/09: coreanas (K-beauty já é 7% do skincare no Brasil — Mercado&Consumo, jul/26)
                             r"skin ?1004|missha|klairs|some ?by ?mi|biodance|mediheal|dr\.? ?jart|torriden|round ?lab|"
                             r"numbuzin|isntree|purito|innisfree|sulwhasoo|abib|mixsoon|axis-?y|aestura|illiyoon|"
                             r"d'?alba|beplain|haruharu|benton|heimish|neogen|skinfood|banila|tocobo|vt cosmetics|"
                             r"goodal|cos de baha|dr\.? ?althea)(?!\w)", re.I)
TREINO, MAMAE = "LOOK DE TREINO QUE VALORIZA TUDO 🍑", "MAMÃE, CORRE QUE TÁ BARATO 👶"
REFINO = {  # o tipo certo, mas a frase certa é a mais específica que também aparece na cabeça do título
    "CHEIROSA O DIA INTEIRO, AMIGA 🌸": ["CHEIRO DE GRIFE, PREÇO DE ÁRABE 🔥"],
    "BRILHO NO LOOK SEM GASTAR MUITO ✨": ["JOIA DE MARCA COM DESCONTO 💎"],
    "LOOK NOVO GASTANDO POUCO 👗": [TREINO, MAMAE, "ROUPINHA FOFA PROS PEQUENOS 🧸"],  # legging / body bebê / infantil
    "CABELO MACIO DE SALÃO EM CASA 💆‍♀️": [MAMAE, "CHEIROSA O DIA INTEIRO, AMIGA 🌸"],  # Johnson's Baby / Braé Body Splash
}
TECNOLOGIA = re.compile(r"(?<!\w)(?:smart|smart ?watch|smartwatch|inteligente|bluetooth|fone)(?!\w)", re.I)
PRA_ELE = "PRESENTE CERTO PRA ELE 🎁"
FEMININAS = {"CHEIROSA O DIA INTEIRO, AMIGA 🌸", "BRILHO NO LOOK SEM GASTAR MUITO ✨", "LOOK NOVO GASTANDO POUCO 👗",
             TREINO, "PÉ LINDO E CONFORTÁVEL 👟", "A BOLSA QUE COMBINA COM TUDO 👜"}
BENEFICIOS_BELEZA = 12  # as 12 primeiras frases são de beleza/cabelo/perfume
# 30/09: frases variadas e por característica moram em vendas/ganchos.py (repertório próprio, sem repetir)
SEGREDO = "AMIGAS NÃO GUARDAM SEGREDO! 🤫"  # frase de DICA de cuidado (retinol, protetor, tratamento) — dona, 30/09
DICAS = ("PELE PROTEGIDA", "CÍLIOS", "PELE LISINHA", "CABELO MACIO")
# "peças" fora: "Jogo de Lençol 2 Peças"/"Conjunto de Panelas 10 Peças" são partes de UM jogo, não itens → "SÓ R$ X CADA" enganava
KIT = re.compile(r"(?:kit|combo|pack)\s*(?:com\s*|c/\s*)?(\d{1,3})\b|\b(\d{1,3})\s*(?:pares|unidades|un\b|rolos)", re.I)
MARCAS = re.compile(  # marcas que os grupos grandes mais postam (confiança = clique)
    r"la roche|vichy|cetaphil|neutrogena|principia|creamy|sallve|nivea|eudora|botic[áa]rio|natura|avon|dove|"
    r"k[ée]rastase|l.or[ée]al|wella|lola|salon line|elseve|pantene|tresemm|celimax|beauty of joseon|skin1004|"
    r"medicube|anua|cosrx|tirtir|torriden|round lab|laneige|some by mi|biodance|numbuzin|rom&nd|peripera|"
    r"vizzela|ruby rose|mari maria|boca rosa|bruna tavares|fran by|max love|dailus|"
    r"dark lab|growth|max titanium|integral ?m[ée]dica|dux|soldiers|probi[óo]tica|puma|adidas|nike|olympikus|"
    r"lupo|insider|alto giro|mizuno|fila|tramontina|electrolux|mondial|brit[âa]nia|philco|oster|wap\b|brinox|"
    r"oxford|lattafa|armaf|al wataniah|maison alhambra|carolina herrera|paco rabanne|jean paul|lanc[ôo]me|"
    r"vivara|pandora|"
    r"pampers|huggies|johnson|whiskas|pedigree|golden|premier|samsung|jbl|xiaomi|apple|stanley", re.I)


PARA_ALGO = {"de", "da", "do", "das", "dos", "para", "p/", "pra", "sem", "porta", "suporte", "expositor", "organizador", "estojo"}


def cabeca_do_titulo(titulo: str) -> str:
    """As até 8 primeiras palavras, parando no primeiro conector ("com", "para", "+"): é onde está o TIPO."""
    titulo = re.sub(r"^(?:kit|combo|pack)\s*(?:c/|com)\s*\d+\s*(?:un\w*)?\s*", "", titulo, flags=re.I)  # "Kit C/ 4 Toalha"
    out = []
    for p in titulo.split()[:8]:  # 8: "Braé By Ana Paula 3 Body Splash" (marca na frente)
        if p.lower() in CONECTORES:
            break
        out.append(p)
    return " ".join(out)


def beneficio(titulo: str, grupo: str | None) -> str | None:
    """Frase de benefício do TIPO do produto (ou None): o termo que aparece primeiro na cabeça do título vence."""
    cabeca = cabeca_do_titulo(titulo)
    melhor: tuple[int, int, str | None] | None = None  # (posição, ordem na lista, frase)
    for i, (rx, frase) in enumerate(_BENEF):
        if i < BENEFICIOS_BELEZA and grupo not in (None, "beleza", "cabelo", "perfume"):
            continue  # frase de maquiagem/cabelo só em produto de beleza (cama de cachorro "com base" virava "PELE DE FILTRO")
        for m in rx.finditer(cabeca):
            antes = [p.lower().strip(",") for p in cabeca[:m.start()].split()[-2:]]
            if antes and antes[-1] in PARA_ALGO and not (len(antes) == 2 and antes[0] in COLETIVOS):
                continue  # "Expositor De Perfumes", "Porta Batom": o produto é PARA aquilo, não aquilo
                # (mas "Jogo de Panelas", "Conjunto De Maiô", "Kit de Pincéis": o produto É aquilo)
            if melhor is None or (m.start(), i) < melhor[:2]:
                melhor = (m.start(), i, frase)
            break
    if not melhor:  # título sem o tipo ("Principia Kit Essencial…"): marca de skincare diz o que é
        if grupo in (None, "beleza") and MARCAS_SKINCARE.search(cabeca):
            return "PELE LISINHA E COM VIÇO, AMIGA ✨"
        return None
    if not melhor[2]:
        return None
    frase = melhor[2]
    for fino in REFINO.get(frase, []):  # "Perfume Árabe Lattafa": o tipo é perfume, mas a frase certa é a do árabe
        if any(rx.search(cabeca) for rx, f in _BENEF if f == fino):
            frase = fino
            break
    if frase == "BRILHO NO LOOK SEM GASTAR MUITO ✨" and TECNOLOGIA.search(cabeca):
        return "TECNOLOGIA NO PRECINHO 📱"  # smartwatch não é joia
    if frase in FEMININAS and MASCULINO.search(titulo) and not re.search(r"feminin|unissex|mulher", titulo, re.I):
        return PRA_ELE  # perfume/relógio/tênis masculino não é "amiga"
    return frase


def gancho_post(o: dict, n: int = 0, recentes: list[str] | None = None) -> str:
    """Gancho do post: desconto absurdo → preço por unidade em kit → frase do TIPO/CARACTERÍSTICA do produto
    (vendas/ganchos.py) → sinal real de venda / frase universal. `recentes` = ganchos dos últimos posts (não repetir)."""
    from vendas import ganchos
    d, titulo, preco = o.get("desconto") or 0, o.get("titulo") or "", o.get("preco") or 0
    if d >= 70 and n % 3 == 0:  # nos grupos o "% OFF" é tempero, não regra — o que domina é o benefício
        return ganchos.escolher([f.format(d=d) for f in ganchos.OFF], n, recentes)
    m = KIT.search(titulo)
    qtd = int(next(g for g in m.groups() if g)) if m else 0
    if 2 <= qtd <= 12 and preco and preco / qtd <= 30:  # "60 unidades" de lenço/cápsula: "R$ 0,53 CADA" engana
        return ganchos.escolher([f.format(p=_brl(preco / qtd).upper()) for f in ganchos.KIT], n, recentes)
    tipo = beneficio(titulo, o.get("grupo"))
    if tipo:
        return ganchos.escolher(ganchos.candidatos(tipo, titulo), n, recentes)
    if MASCULINO.search(titulo) and not re.search(r"feminin|unissex|mulher", titulo, re.I):
        return ganchos.escolher(ganchos.REPERTORIO[PRA_ELE], n, recentes)
    sinais = json.loads(o["sinais"]) if isinstance(o.get("sinais"), str) else (o.get("sinais") or {})
    opcoes = []
    vend = sinais.get("vendidos_num", 0) or 0
    if vend >= 10000:  # número real do anúncio, em vez de "tá bombando" genérico
        opcoes.append(f"MAIS DE {vend // 1000} MIL VENDIDOS 🔥")
    if sinais.get("top"):
        opcoes.append("A GALERA AMOU ESSA 🔥")
    if d >= 60:
        opcoes.append("PRECINHO DE BUG 🐞")
    return ganchos.escolher(opcoes + ganchos.UNIVERSAL, n, recentes)


LUXO = re.compile(  # marcas "caras" que fazem a pessoa parar o dedo (vídeo de divulgação do grupo, 30/09)
    r"(?<!\w)(?:dior|chanel|carolina herrera|lanc[ôo]me|givenchy|ysl|yves saint|prada|michael kors|coach|guess|arezzo|"
    r"schutz|carmen steffens|lacoste|tommy hilfiger|calvin klein|ray-?ban|oakley|vivara|pandora|k[ée]rastase|clinique|"
    # "dolce" sozinho pegava Dolce Gusto (café) e Dolce Arome (cafeteira) como grife — 30/09
    r"victoria.?s secret|jean paul gaultier|paco rabanne|azzaro|montblanc|hugo boss|armani|versace|"
    r"dolce\s*(?:&|e|and)\s*gabbana|d&g|burberry|gucci|fendi|valentino|bvlgari|bulgari|tiffany|cartier|swarovski|"
    r"jimmy choo|marc jacobs|kate spade|longchamp|furla|victor hugo|mont blanc|"
    r"shiseido|too faced|laneige|fossil|mac cosmetics|kiko|la roche-?posay|sephora collection|lattafa|stanley)(?!\w)",
    re.I)


def ofertas_luxo(horas: int = 36, n: int = 8, preco_de_min: float = 200) -> list[dict]:
    """Ofertas de MARCA com desconto real e preço cheio alto (a vitrine do vídeo de divulgação): aprovadas, com foto e
    link, 'de' ≥ preco_de_min e ≥ 25% off; a melhor de cada tipo de produto, das mais impressionantes para as menos."""
    ofs = [o for o in sem_repetidos(melhores(horas, 20000)) if o["link_loja"] and o.get("foto")
           and LUXO.search(o["titulo"] or "") and (o.get("preco_antigo") or 0) >= preco_de_min
           and (o.get("desconto") or 0) >= 25 and not MASCULINO.search(o["titulo"] or "")]
    return sorted(ofs, key=lambda o: ((o["preco_antigo"] or 0) - o["preco"]), reverse=True)[:n]


def ganchos_recentes(limite: int = 60) -> list[str]:
    """Ganchos dos últimos posts do grupo (do mais antigo ao mais novo) — para não repetir frase."""
    linhas = db.consultar("SELECT saida FROM ia_exemplos WHERE fonte = 'grupo' ORDER BY id DESC LIMIT ?", (limite,)) \
        if db.consultar("SELECT 1 FROM sqlite_master WHERE name = 'ia_exemplos'") else []
    return [(r["saida"] or "").split("\n", 1)[0].strip("* ") for r in reversed(linhas)]


def hora_do_preco(o: dict, agora: datetime | None = None) -> str:
    """Amazon (Associados): preço exibido fora da API precisa da data/hora em que foi conferido. "11h40" se foi hoje,
    "29/09 23h10" se não. Vazio para as outras lojas (a dona não quer rodapé nem carimbo à toa)."""
    if not re.search(r"amazon\.com\.br|amzn\.to", o.get("link_loja") or "", re.I) or not o.get("atualizado_em"):
        return ""
    try:
        t = datetime.strptime(str(o["atualizado_em"])[:16], "%Y-%m-%d %H:%M")
    except ValueError:
        return ""
    hora = f"{t.hour}h{t.minute:02d}"
    return hora if t.date() == (agora or datetime.now()).date() else f"{t:%d/%m} {hora}"


def legenda_post(o: dict, n: int = 0, recentes: list[str] | None = None) -> str:
    """Legenda no formato dos grupos: GANCHO → loja → produto → De/Por → cupom → link → aviso."""
    gancho = gancho_post(o, n, recentes)
    if recentes is not None:
        recentes.append(gancho)  # a própria rodada também não repete
    preco = (f"De {_brl_zap(o['preco_antigo'])} por *{_brl_zap(o['preco'])}*" if o.get("preco_antigo")
             else f"Por *{_brl_zap(o['preco'])}*")
    if hora := hora_do_preco(o):  # 30/09 (dona): Amazon exige data/hora junto do preço → "hora curtinha"
        preco += f" _(às {hora})_"
    linhas = [f"*{gancho}*", "", limpar_titulo(o["titulo"])[:100], f"🏬 {o.get('loja') or ''}", preco]
    if selo := selo_preco(o):
        linhas.append(selo)
    if o.get("cupom"):
        linhas.append(f"🎟️ Cupom: *{o['cupom']}*")
    # 30/09 (dona): SEM rodapé nas mensagens ("Preço de… pode mudar. #publi · Associado Amazon…") — o aviso de
    # afiliado fica no site e na descrição do grupo, não em cada post
    linhas += ["", f"👉 {link_afiliado(o['link_loja'], canal='whatsapp')}"]  # post do grupo = WhatsApp
    if n % 5 == 4:  # como os grupos grandes: de vez em quando pede indicação (crescimento sem pegar número de ninguém)
        linhas += ["", f"💌 Indique pra uma amiga: {canais().get('site', SITE_URL)}"]
    return "\n".join(linhas)


SITE_URL = "https://bellalucce.github.io"
HORAS_LOJA = (10, 13, 16, 19)  # 4 posts/dia de produto NOSSO no grupo (exposição grátis; 30/09: 1 visita/semana no ML)


def post_da_loja(agora: datetime | None = None) -> tuple[str, str] | None:
    """(legenda, foto local) de um produto da PRÓPRIA loja com estoque, em rodízio, nas HORAS_LOJA (1ª rodada da hora).
    Produtos/links/legendas vêm de config/midia.json (os mesmos dos vídeos). None fora do horário ou sem estoque."""
    from vendas import midia
    agora = agora or datetime.now()
    if agora.hour not in HORAS_LOJA or agora.minute >= 15:
        return None
    prods = json.loads((config.CONFIG / "midia.json").read_text(encoding="utf-8"))["produtos"]
    ok = [(k, p) for k, p in prods.items() if p.get("links") and midia.tem_estoque(k) and (config.RAIZ / p["foto"]).exists()]
    if not ok:
        return None
    vez = agora.timetuple().tm_yday * len(HORAS_LOJA) + HORAS_LOJA.index(agora.hour)
    sku, p = ok[vez % len(ok)]
    legenda = p["legendas"][vez % len(p["legendas"])] if p.get("legendas") else p["nome"]
    linhas = ["*DA NOSSA LOJINHA 💖*", "", legenda, "", f"💰 {p.get('preco', '')}"]
    linhas += [f"👉 {canal}: {url}" for canal, url in p["links"].items()]
    linhas += ["", "_Produto da loja Bella Lucce, notificado na ANVISA._"]
    return "\n".join(linhas), str(config.RAIZ / p["foto"])


LEMBRETE_HORAS = (12, 18)  # 30/09 (dona): "repete 2× por dia" — quem entrou depois não vê as ofertas da manhã
TEXTO_LEMBRETE = ("📌 *CHEGOU AGORA?*\n\nTodas as ofertas de hoje ficam no nosso site, separadinhas por categoria 👇\n{site}"
                  "\n\n_Aqui no grupo chegam ofertas novas o dia todo, das 8h às 22h._")


def lembrete_site(agora: datetime | None = None) -> tuple[str, str] | None:
    """(texto, foto) do lembrete "chegou agora? as ofertas de hoje estão no site" — 1ª rodada das LEMBRETE_HORAS."""
    agora = agora or datetime.now()
    if agora.hour not in LEMBRETE_HORAS or agora.minute >= 15:
        return None
    from vendas import convite
    site = canais().get("site", SITE_URL)
    return TEXTO_LEMBRETE.format(site=site), str(convite.banner_site(site))


def canais() -> dict:
    """Links públicos dos canais (config/achadinhos.json): grupo_whatsapp, telegram, site."""
    arq = config.CONFIG / "achadinhos.json"
    return {"site": SITE_URL, **(json.loads(arq.read_text(encoding="utf-8")) if arq.exists() else {})}


def fila_posts(n: int = 5, horas: int = 30) -> list[dict]:
    """Próximas ofertas para o grupo: aprovadas, com link e foto, ainda não postadas; o melhor de cada tipo de produto
    (sem repetir tipo postado nas últimas 24 h) e variando categoria (sem 3 iguais seguidas)."""
    recentes = {chave_produto(r["titulo"]) for r in db.consultar(
        "SELECT titulo FROM ofertas WHERE publicado_em >= datetime('now','localtime','-24 hours')")}
    # 30/09: Amazon vai com a hora do preço no post → só preço visto nas últimas 6 h (antes saiu perfume com preço de
    # ontem 12h51 — com o carimbo apareceria "29/09" e o preço podia já ter mudado)
    amazon_ok = (datetime.now() - timedelta(hours=6)).isoformat(sep=" ", timespec="seconds")
    cand = [o for o in sem_repetidos(melhores(horas, 3000)) if o["link_loja"] and o.get("foto") and not o["publicado_em"]
            and chave_produto(o["titulo"]) not in recentes
            and (not re.search(r"amazon\.com\.br|amzn\.to", o["link_loja"], re.I) or (o.get("atualizado_em") or "") >= amazon_ok)]
    # linha do grupo (usuário): beleza/cabelo/perfume primeiro → em cada 5 posts, 3 da linha principal e 2 das outras
    # (fitness, casa, bebê, moda, pet), sempre o de maior score de cada lado; sem 3 da mesma categoria seguidas
    out, ultimo = [], []
    # 30/09: 1 oferta de GRIFE abre cada rodada (o vídeo de divulgação promete "itens de marca por preço de verdade" no
    # grupo — tem que ser verdade quando a pessoa entra): a de maior desconto em reais, 'de' ≥ R$ 200, ≥ 25% off
    fresco = (datetime.now() - timedelta(hours=12)).isoformat(sep=" ", timespec="seconds")
    grife = [o for o in cand if LUXO.search(o["titulo"] or "") and (o.get("preco_antigo") or 0) >= 200
             and (o.get("desconto") or 0) >= 25 and not MASCULINO.search(o["titulo"] or "")
             and (o.get("atualizado_em") or "") >= fresco]  # preço visto há pouco (grife muda rápido)
    for top in sorted(grife, key=lambda o: (o["preco_antigo"] or 0) - o["preco"], reverse=True)[:5] if n >= 3 else []:
        cand.remove(top)
        if foto_boa(top):  # 30/09 (dona): SEMPRE verificar a imagem antes de enviar
            out.append(top)
            ultimo.append(top["grupo"])
            break
    filas = {"B": [o for o in cand if o["grupo"] in LINHA_PRINCIPAL], "O": [o for o in cand if o["grupo"] not in LINHA_PRINCIPAL]}
    tentativas = 0
    for vez in (PADRAO_LINHA * (n // len(PADRAO_LINHA) + 12)):
        if len(out) >= n or not (filas["B"] or filas["O"]) or tentativas > n * 6:
            break
        fila = filas[vez] or filas["O" if vez == "B" else "B"]
        o = next((x for x in fila if not (len(ultimo) >= 2 and ultimo[-1] == ultimo[-2] == x["grupo"])), None)
        if o is None:
            continue
        fila.remove(o)
        tentativas += 1
        if not foto_boa(o):  # foto pequena/borrada de origem → não vai pro grupo (fica pro site, que usa miniatura)
            continue
        out.append(o)
        ultimo.append(o["grupo"])
    return out


FOTO_MIN_PX = 600  # maior lado da foto que vai pro grupo (a Promobit guarda 200–300 px → esticada fica borrada)
_FOTO_PX_ARQ = config.DADOS / "achadinhos" / "foto_px.json"


def foto_px(url: str) -> int:
    """Maior lado, em px, da MELHOR versão disponível da foto (cache em dados/achadinhos/foto_px.json)."""
    try:
        cache = json.loads(_FOTO_PX_ARQ.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        cache = {}
    if url in cache:
        return cache[url]
    import io

    from PIL import Image

    from vendas.integracoes.whatsapp import foto_grande
    px = 0
    for u in dict.fromkeys((foto_grande(url), url)):
        try:
            r = httpx.get(u, timeout=20, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code == 200:
                px = max(px, max(Image.open(io.BytesIO(r.content)).size))
        except Exception:  # noqa: BLE001 — foto que não abre = ruim
            pass
    cache[url] = px
    if len(cache) > 5000:
        cache = dict(list(cache.items())[-4000:])
    _FOTO_PX_ARQ.parent.mkdir(parents=True, exist_ok=True)
    _FOTO_PX_ARQ.write_text(json.dumps(cache), encoding="utf-8")
    return px


def foto_boa(o: dict) -> bool:
    """30/09 (dona: "sempre que for enviar a imagem, verifique"): só vai pro grupo foto com ≥ FOTO_MIN_PX de verdade.
    Oferta da Promobit que aponta pro ML: usa a foto em alta da coleta do ML do MESMO anúncio, se tivermos."""
    f = o.get("foto") or ""
    m = re.search(r"MLB-?(\d{6,})", o.get("link_loja") or "")
    if "promobit.com.br" in f and m:
        alt = db.consultar("SELECT foto FROM ofertas WHERE id = ? AND foto IS NOT NULL", (f"mlaf:MLB{m.group(1)}",))
        if alt:
            o["foto"] = f = alt[0]["foto"]
    return bool(f) and foto_px(f) >= FOTO_MIN_PX


LINHA_PRINCIPAL = ("beleza", "cabelo", "perfume")
PADRAO_LINHA = ("B", "O", "B", "B", "O")


def marcar_postado(id_oferta: str) -> None:
    with db.conectar() as con:
        con.execute("UPDATE ofertas SET publicado_em = datetime('now','localtime') WHERE id = ?", (id_oferta,))


def reavaliar() -> int:
    """Reaplica as regras (que mudam com os pedidos do usuário) em tudo que está no banco."""
    _tabela()
    n = 0
    with db.conectar() as con:
        for r in con.execute("SELECT * FROM ofertas").fetchall():
            o = dict(r)
            o["sinais"] = json.loads(o["sinais"] or "{}")
            o["grupo"] = _grupo_final(o["grupo"], o["titulo"])
            o["cupom"] = cupom_valido(o["cupom"])
            o["score"] = pontuar(o)
            ok = int(aprovada(o))
            if (ok, o["grupo"], o["cupom"], o["score"]) != (r["aprovada"], r["grupo"], r["cupom"], r["score"]):
                con.execute("UPDATE ofertas SET aprovada = ?, grupo = ?, cupom = ?, score = ? WHERE id = ?",
                            (ok, o["grupo"], o["cupom"], o["score"], o["id"]))
                n += 1
    return n


def auditar() -> list[str]:
    """Revisão de segurança a cada ciclo (pedido do usuário: 'revê isso sempre'): nenhuma oferta aprovada pode ter
    afiliado de terceiros nem link que não seja página de produto. Achou? Derruba e registra no log."""
    problemas = []
    with db.conectar() as con:
        for r in con.execute("SELECT id, titulo, link_loja FROM ofertas WHERE aprovada = 1").fetchall():
            link = r["link_loja"]
            final = link_afiliado(link) if link else None
            ruim = tem_afiliado_terceiro(link) or (link and not pagina_de_produto(link))
            nosso = config.segredos().get("afiliados", {}).get("amazon_tag")
            if final and "tag=" in final and (not nosso or f"tag={nosso}" not in final):
                ruim = True  # tag de Amazon que não é a nossa
            if ruim:
                problemas.append(f"{r['id']} {link}")
                con.execute("UPDATE ofertas SET aprovada = 0 WHERE id = ?", (r["id"],))
    if problemas:
        (config.DADOS / "achadinhos").mkdir(parents=True, exist_ok=True)  # na nuvem a pasta não existe
        with open(config.DADOS / "achadinhos" / "auditoria.log", "a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M} derrubadas: {problemas}\n")
    return problemas


def _publicar_loja(pasta, radar) -> None:
    """Faixa "Da nossa loja" do site: produtos da Bella Lucce COM estoque (config/midia.json + estoque do Hermes) →
    radar/config/loja.json + fotos reduzidas em loja/<sku>.jpg no repositório do site."""
    from PIL import Image

    from vendas import midia
    prods = json.loads((config.CONFIG / "midia.json").read_text(encoding="utf-8"))["produtos"]
    (pasta / "loja").mkdir(exist_ok=True)
    lista = []
    for sku, p in prods.items():
        foto = config.RAIZ / p.get("foto", "")
        if not (p.get("links") and foto.is_file() and midia.tem_estoque(sku)):
            continue
        destino = pasta / "loja" / f"{sku}.jpg"
        if not destino.exists() or destino.stat().st_mtime < foto.stat().st_mtime:
            im = Image.open(foto).convert("RGB")
            im.thumbnail((500, 500))
            im.save(destino, quality=85)
        lista.append({"nome": p["nome"], "preco": p.get("preco", ""), "links": p["links"], "foto": f"loja/{sku}.jpg"})
    (radar / "config" / "loja.json").write_text(json.dumps(lista, ensure_ascii=False, indent=1), encoding="utf-8")


def publicar_site() -> str:
    """Alimenta o RADAR NA NUVEM (repo bellalucce/bellalucce.github.io, GitHub Actions a cada 20 min — quem monta e
    publica o site é a nuvem, mesmo com o PC desligado). O PC só envia, quando mudam: o código deste módulo, a config,
    os códigos de afiliado (públicos por natureza) e as ofertas do ML da última coleta (radar/dados/ml.json)."""
    import shutil
    import subprocess
    from pathlib import Path
    pasta = config.DADOS / "achadinhos" / "pages"
    if not (pasta / ".git").exists():
        return "sem clone do site"
    radar = pasta / "radar"
    for sub in ("vendas", "config", "dados"):
        (radar / sub).mkdir(parents=True, exist_ok=True)
    shutil.copyfile(Path(__file__), radar / "vendas" / "achadinhos.py")
    shutil.copyfile(Path(__file__).with_name("ganchos.py"), radar / "vendas" / "ganchos.py")  # achadinhos importa (30/09)
    shutil.copyfile(config.CONFIG / "achadinhos.json", radar / "config" / "achadinhos.json")
    af = config.segredos().get("afiliados") or {}
    publicos = {k: af[k] for k in ("amazon_tag", "magalu_loja", "ml_tool", "ml_etiqueta") if k in af}
    (radar / "config" / "afiliados.json").write_text(json.dumps(publicos, indent=1), encoding="utf-8")
    # vai para a nuvem: TUDO do ML da última coleta + as aprovadas das outras lojas (a nuvem começa o banco do zero a cada
    # execução e só veria a Promobit daquele instante — 30/09: 12 Amazon no site × 88 no PC). No máx. 1×/hora: cada
    # troca vira um commit de ~2 MB no repositório público.
    arq = radar / "dados" / "ml.json"
    if not arq.exists() or time.time() - arq.stat().st_mtime > 55 * 60:
        ofs = db.consultar("SELECT * FROM ofertas WHERE atualizado_em >= datetime('now', 'localtime', '-36 hours') "
                           "AND (fonte = 'ml_afiliados' OR (aprovada = 1 AND link_loja IS NOT NULL)) ORDER BY id")
        for r in ofs:
            r["publicado_em"] = None
        arq.write_text(json.dumps(ofs, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        # resumo do histórico de preço (só quem tem 7+ dias) → selo "menor preço" também no site montado na nuvem
        (radar / "dados" / "historico.json").write_text(json.dumps(minimos(), separators=(",", ":")), encoding="utf-8")
    _publicar_loja(pasta, radar)
    for origem, destino in (("logo_256.png", "logo.png"), ("favicon.png", "favicon.png")):  # logo da marca (dados/marca)
        if (config.DADOS / "marca" / origem).exists():
            shutil.copyfile(config.DADOS / "marca" / origem, pasta / destino)
    git = ["git", "-C", str(pasta), "-c", "user.name=bellalucce", "-c", "user.email=bellalucce@users.noreply.github.com",
           "-c", "credential.helper=", "-c", "credential.helper=manager"]
    subprocess.run(git + ["add", "-A"], check=True)
    if subprocess.run(git + ["diff", "--cached", "--quiet"]).returncode == 0:
        return "radar na nuvem: nada novo"
    subprocess.run(git + ["commit", "-qm", "Radar: dados/código do PC"], check=True)
    subprocess.run(git + ["pull", "-q", "--rebase", "origin", "main"], capture_output=True, text=True, timeout=120)
    r = subprocess.run(git + ["push", "-q", "origin", "main"], capture_output=True, text=True, timeout=120)
    return "radar na nuvem atualizado" if r.returncode == 0 else f"push falhou: {r.stderr.strip()[:200]}"
