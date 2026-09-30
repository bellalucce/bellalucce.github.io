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
from datetime import datetime

import httpx

from vendas import config, db

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0 Safari/537.36", "Accept-Language": "pt-BR,pt;q=0.9"}
PROMOBIT = "https://www.promobit.com.br"
PAGINAS_PROMOBIT = ["/promocoes/em-alta/", "/promocoes/recentes/", "/promocoes/perfumes-e-beleza/",
                    "/promocoes/moda-e-calcados-femininos/", "/promocoes/saude-e-higiene/",
                    "/promocoes/utensilios-domesticos/", "/promocoes/casa-e-construcao/", "/promocoes/menor-preco/",
                    "/promocoes/loja/magazine-luiza/"]  # Magalu (pedido do usuário; o site da Magalu bloqueia robôs)

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
    "eletronicos": r"smart ?tv|notebook|celular|smartphone|fone|headset|monitor|tablet|carregador|ssd|mouse|teclado|caixa de som|câmera|camera",
    "mercado": r"café|cafe|leite|cerveja|vinho|chocolate|sabão|detergente|papel higiênico|fralda",
    "esporte": r"creatina|whey|pré-treino|halter|esteira|bicicleta|academia|suplemento|colágeno",
}
PALAVRAS["eletronicos"] += r"|motorola|samsung|iphone|xiaomi|redmi|galaxy|kindle|echo dot|alexa|smartwatch|playstation|xbox"
NOSSO_FOCO = ("beleza", "moda", "casa")  # o público do grupo: mulheres que amam comprar (pedido do usuário)
# Linha do grupo (usuário, 29/09 noite): beleza/cabelo/perfume primeiro; fitness, casa viral e bebê; moda; pet só de
# marca popular. Peso somado ao score — segue uma linha em vez de "aleatório".
PESO_GRUPO = {"beleza": 10, "cabelo": 10, "perfume": 9, "esporte": 7, "casa": 6, "infantil": 6, "moda": 5, "pet": 3,
              "eletronicos": 0}
CABELO = re.compile(r"shampoo|xampu|condicionador|m[áa]scara capilar|capilar|cabelo|secador|chapinha|prancha|babyliss|"
                    r"escova (secadora|alisadora|rotativa)|modelador de cachos|finalizador|leave-?in|[óo]leo capilar|"
                    r"t[ôo]nico capilar|progressiva|tintura|coloraç", re.I)
FITNESS = re.compile(r"bicicleta ergom|esteira|legging|top fitness|conjunto fitness|academia|halter|anilha|el[áa]stico de "
                     r"exerc|colchonete|yoga|pilates|whey|creatina|pr[ée]-?treino|squeeze|coqueteleira|corda de pular|"
                     r"suplemento em p|hipercal|carboidrat|albumina|bioimped", re.I)
BEBE = re.compile(r"fralda|len[çc]o umedecido|beb[êe]|infantil|mamadeira|chupeta|carrinho de beb|body infantil|"
                  r"banheira|trocador|kit ber[çc]o|brinquedo", re.I)
# pet: só o que vende muito e serve para qualquer bicho (nada de remédio nem ração específica)
PET_POPULAR = re.compile(r"whiskas|pedigree|golden|premier|gran plus|special (dog|cat)|friskies|dog chow|cat chow|"
                         r"areia (sanit|higi)|tapete higi|arranhador|caminha|cama pet|comedouro|bebedouro|fonte para gato|"
                         r"brinquedo (pet|para (c[ãa]es|gatos))|coleira|peitoral", re.I)
FORA = re.compile(  # fora da linha do grupo (pedido do usuário): automotivo, remédios, peças, industrial
    r"automotiv|para-?brisa|palheta|taramps|m[óo]dulo (amplificador|de pot[êe]ncia)|som automotivo|alto-?falante automot|"
    r"pneu|[óo]leo (de )?motor|aditivo|farol|retrovisor|som para carro|"
    r"simparic|bravecto|nexgard|credeli|verm[íi]fugo|antipulgas|medicamento|rem[ée]dio|comprimidos? de|"
    r"ra[çc][ãa]o .*(renal|urin|gastro|hipoalerg|obes|hep[áa]t|terap|veterin|diet)|"
    r"placa de v[íi]deo|processador|placa-?m[ãa]e|mem[óo]ria ram|fonte atx|gabinete gamer|"
    r"rolamento|parafuso|disjuntor|cabo flex|fio el[ée]tric|v[áa]lvula|mangueira de press|motor el[ée]tric|"
    r"livro|apostila|camiseta de time|uniforme|"
    r"inalador|nebulizador|ox[íi]metro|medidor de press|aparelho de press|term[ôo]metro cl[íi]nic|glicos|palmilha ortop", re.I)
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
CAMA_BANHO = re.compile(r"travesseiro|almofada|len[çc]ol|edredom|toalha|cobertor|manta de sof|tapete", re.I)


def _grupo_final(g: str, titulo: str) -> str:
    """Refina a categoria pelo título: perfume vira aba própria; categoria genérica tenta pelas palavras."""
    if PERFUME.search(titulo or "") and not re.search(r"expositor|organizador|porta[- ]", titulo or "", re.I):
        return "perfume"
    if CABELO.search(titulo or ""):
        return "cabelo"
    if FITNESS.search(titulo or ""):
        return "esporte"
    if g in ("mercado", "outros", "beleza", "casa") and BEBE.search(titulo or ""):
        return "infantil"
    if BEM_ESTAR.search(titulo or ""):
        return "beleza"   # bem-estar fica junto de beleza (a loja da Promobit às vezes classifica errado)
    if CAMA_BANHO.search(titulo or ""):
        return "casa"
    if g in ("outros", "mercado", "esporte", "pet"):
        g2 = _grupo(titulo)
        return g2 if g2 in GRUPOS_OK else g
    return g


# ---------------- fontes ----------------
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
            "titulo": _html.unescape(o.get("offerTitle") or "").strip(),
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
    titulo = o.get("titulo") or ""
    p -= 18 if MASCULINO.search(titulo) else 0             # público do grupo é principalmente feminino
    p += 4 if re.search(r"feminin|mulher", titulo, re.I) else 0
    return round(max(0.0, min(100.0, p)), 1)


MASCULINO = re.compile(r"masculin|\bmen\b|\bhomem\b|cueca|boxer|barbear|\bbarba|p[óo]s[- ]barba", re.I)


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
    if not (o.get("preco") and 1 <= o["preco"] <= teto):
        return False
    link = o.get("link_loja")
    if link is not None and (tem_afiliado_terceiro(link) or not any(re.search(p, link) for p in PAGINA_PRODUTO)):
        return False
    return True


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
    if o["fonte"] == "ml_afiliados":  # lista oficial do ML: sem votos da comunidade → desconto + nota + vendas
        nota, vend = o.get("nota") or 0, s.get("vendidos_num", 0)
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


def limpar_link(url: str, cli: httpx.Client | None = None) -> str | None:
    """Endereço real do produto, SEM afiliado de terceiros (senão a comissão vai pra eles):
    abre encurtadores (meli.la, s.shopee…), extrai o destino de deeplinks (linksynergy murl/u) e tira tag/utm."""
    from urllib.parse import parse_qs, unquote, urlparse
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
    url = re.sub(r"([?&])(tag|ref_|matt_[a-z_]+|utm_[a-z]+|af_[a-z_]+|smtt|sp_atk|xptdk|mmp_pid|uls_trackid|"
                 r"gads_t_sig|lp|c|pid|clickid|tracking_id|forceInApp)=[^&#]*", r"\1", url)
    url = re.sub(r"[?&]+(#|$)", r"\1", re.sub(r"[?&]{2,}", "&", url.replace("?&", "?")))
    url = re.sub(r"([?&])(promoter_id|partner_id|seller_id_divulgador)=[^&#]*", r"\1", url)  # Magalu divulgador
    url = re.sub(r"/divulgador/oferta/(\w+)/", r"/p/\1/", url)
    url = re.sub(r"[?&]+(#|$)", r"\1", url)
    m = re.search(r"shopee\.com\.br/(?:opaanlp|product)/(\d+)/(\d+)", url)
    if m:
        url = f"https://shopee.com.br/product/{m.group(1)}/{m.group(2)}"
    if "/social/" in url:  # meli.la de terceiros abre uma vitrine deles, não o produto → sem link (não posta)
        return None
    return url


def link_afiliado(url: str | None) -> str | None:
    """Troca pelo NOSSO código de afiliado quando existir (config/segredos.json → afiliados)."""
    if not url:
        return None
    af = config.segredos().get("afiliados") or {}
    m = re.search(r"amazon\.com\.br/(?:.*/)?dp/([A-Z0-9]{10})", url)
    if m and af.get("amazon_tag"):
        return f"https://www.amazon.com.br/dp/{m.group(1)}?tag={af['amazon_tag']}"
    m = re.search(r"magazineluiza\.com\.br/(.+/p/\w+/.*)$", url)
    if m and af.get("magalu_loja"):  # Parceiro Magalu: a lojinha do usuário no magazinevoce
        return f"https://www.magazinevoce.com.br/magazine{af['magalu_loja']}/{m.group(1)}"
    if re.search(r"mercadolivre\.com\.br/", url) and af.get("ml_tool"):  # ML Afiliados: mesmos parâmetros do meli.la oficial
        base = url.split("#")[0]
        return f"{base}{'&' if '?' in base else '?'}matt_word={af.get('ml_etiqueta', 'bellalucce')}&matt_tool={af['ml_tool']}"
    return url  # ML/Shopee: gerar link curto pela ferramenta/API de afiliados quando o cadastro existir


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
        with db.conectar() as con:
            for o in {x["id"]: x for x in vistos}.values():
                o["score"], ok = pontuar(o), aprovada(o)
                ant = con.execute("SELECT link_loja, visto_em FROM ofertas WHERE id = ?", (o["id"],)).fetchone()
                link = (ant["link_loja"] if ant else None) or o.get("link_loja")
                if ok and not link and o.get("link_fonte", "").startswith(PROMOBIT):
                    try:
                        link = _link_loja(o["link_fonte"], cli)
                        time.sleep(1)
                    except Exception as e:
                        erros.append(f"link {o['id']}: {e}")
                if link:  # confere de novo com o link real: página de UM produto e sem afiliado de terceiros
                    o["link_loja"] = link
                    ok = aprovada(o)
                con.execute("""INSERT INTO ofertas (id, fonte, loja, titulo, grupo, preco, preco_antigo, desconto, cupom, foto,
                                 link_fonte, link_loja, nota, vendidos, sinais, score, aprovada, visto_em, atualizado_em)
                               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                               ON CONFLICT(id) DO UPDATE SET preco=excluded.preco, preco_antigo=excluded.preco_antigo,
                                 desconto=excluded.desconto, cupom=excluded.cupom, sinais=excluded.sinais, score=excluded.score,
                                 aprovada=excluded.aprovada, link_loja=COALESCE(excluded.link_loja, link_loja),
                                 atualizado_em=excluded.atualizado_em""",
                            (o["id"], o["fonte"], o.get("loja"), o["titulo"], o["grupo"], o.get("preco"), o.get("preco_antigo"),
                             o.get("desconto"), o.get("cupom"), o.get("foto"), o.get("link_fonte"), link, o.get("nota"),
                             o.get("vendidos"), json.dumps(o.get("sinais"), ensure_ascii=False), o["score"], int(ok),
                             agora, agora))
                novos += ant is None
                aprov += ok
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
                 "cupom": None, "foto": it.get("foto"), "link_fonte": None, "link_loja": limpar_link(it.get("url")),
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
            novos += ant is None
            aprov += ok
    problemas = auditar()
    return {"salvos": len(itens), "novos": novos, "aprovados": aprov, "auditoria": len(problemas)}


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


def vitrine(horas: int = 36) -> str:
    """Gera dados/achadinhos/site/index.html (celular primeiro). Só ofertas aprovadas e com link."""
    # todas as ofertas ativas (o que saiu de promoção não é revisto na coleta e some em até `horas`), o melhor de cada tipo
    ofs = sem_repetidos([o for o in melhores(horas, 20000) if o["link_loja"] and o.get("grupo") in NOMES])
    grupos = [g for g in NOMES if any(o["grupo"] == g for o in ofs)]
    e = _html.escape
    dados = [{"g": o["grupo"], "l": o.get("loja") or "", "t": o["titulo"][:90], "p": _brl(o["preco"]),
              "a": _brl(o["preco_antigo"]) if o.get("preco_antigo") else "", "d": o.get("desconto") or 0,
              "c": o.get("cupom") or "", "f": o.get("foto") or "", "u": link_afiliado(o["link_loja"]) or ""} for o in ofs]
    (SITE).mkdir(parents=True, exist_ok=True)
    (SITE / "ofertas.json").write_text(json.dumps(dados, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    cards = []  # a página desenha os cards a partir de ofertas.json (60 por vez)
    abas = "".join(f'<button data-g="{g}">{NOMES[g]}</button>' for g in grupos)
    agora = datetime.now().strftime("%d/%m %H:%M")
    grupo = canais().get("grupo_whatsapp")
    entrar = (f'<a class="entrar" href="{e(grupo)}" target="_blank" rel="noopener">💬 Entrar no grupo de achadinhos do WhatsApp</a>'
              if grupo else "")
    pagina = f"""<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Achadinhos Bella Lucce</title>
<link rel="icon" href="favicon.png"><meta property="og:image" content="https://bellalucce.github.io/logo.png">
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
.cupom{{margin:6px 10px 0;font-size:12px;background:#fff0f5;border:1px dashed var(--rosa);border-radius:8px;padding:4px 6px}}
.btn{{margin:10px;background:var(--rosa);color:#fff;text-align:center;border-radius:10px;padding:9px;font-weight:700}}
footer{{text-align:center;font-size:11px;color:#999;padding:0 16px 24px}}
.logo{{width:72px;height:72px;border-radius:50%;border:2px solid #fff;display:block;margin:0 auto 8px}}
.busca{{display:flex;gap:10px;align-items:center;padding:0 16px 12px}}.busca input{{flex:1;border:1px solid #f1c6d6;border-radius:20px;padding:9px 14px;font-size:14px}}.busca span{{font-size:12px;color:#888;white-space:nowrap}}
#mais{{display:block;margin:0 auto 24px;border:0;background:var(--rosa);color:#fff;font-weight:700;border-radius:22px;padding:12px 22px;font-size:15px}}
.entrar{{display:block;margin:12px auto 0;max-width:420px;background:#25d366;color:#fff;text-decoration:none;font-weight:700;border-radius:24px;padding:11px 16px}}
</style></head><body>
<header><img class="logo" src="logo.png" alt="bella lucce"><h1>Achadinhos Bella Lucce ✨</h1><p>As melhores promoções do dia, conferidas a cada 20 minutos · atualizado {agora}</p>{entrar}</header>
<nav><button class="on" data-g="">Tudo</button>{abas}</nav>
<div class="busca"><input id="q" type="search" placeholder="Buscar oferta (ex.: sérum, legging, fralda)"><span id="n"></span></div>
<main id="lista"><p>Carregando ofertas…</p></main>
<button id="mais">Carregar mais ofertas</button>
<footer>Preços e cupons podem mudar a qualquer momento. Links de afiliado: a loja pode nos pagar uma comissão, sem custo para você.</footer>
<script>
let T=[],F=[],N=0,G='',Q='';const P=60,E=s=>String(s).replace(/[&<>"]/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}})[c]);
const card=o=>`<a class="card" href="${{E(o.u)}}" target="_blank" rel="nofollow sponsored noopener"><div class="img">${{o.f?`<img loading="lazy" src="${{E(o.f)}}" alt="">`:''}}${{o.d?`<span class="selo">-${{o.d}}%</span>`:''}}</div><div class="loja">${{E(o.l)}}</div><div class="tit">${{E(o.t)}}</div><div class="preco">${{o.a?`<s>${{o.a}}</s>`:''}}<b>${{o.p}}</b></div>${{o.c?`<div class="cupom">Cupom: <b>${{E(o.c)}}</b></div>`:''}}<div class="btn">Pegar oferta</div></a>`;
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
GANCHOS = {
    "beleza": ["AMIGAS NÃO GUARDAM SEGREDO! 💄", "ACHADINHO DE BELEZA ✨", "CORRE QUE TÁ BARATINHO 💖"],
    "moda": ["LOOK NOVO GASTANDO POUCO 👗", "PREÇÃO NA MODA 🛍️"],
    "casa": ["CASA ARRUMADINHA GASTANDO POUCO 🏠", "ACHADINHO PRA CASA ✨"],
    "eletronicos": ["PREÇÃO NESSE AQUI 📱", "TECNOLOGIA COM DESCONTO ⚡"],
}


def legenda_post(o: dict, n: int = 0) -> str:
    """Legenda no formato dos grupos: GANCHO → loja → produto → De/Por → cupom → link → aviso."""
    d = o.get("desconto") or 0
    if d >= 60:
        gancho = f"{d}% OFF, NÃO É ERRO! 😱"
    elif o.get("cupom"):
        gancho = "CUPOM LIBERADO 🎟️"
    elif (json.loads(o["sinais"]) if isinstance(o.get("sinais"), str) else (o.get("sinais") or {})).get("top"):
        gancho = "TÁ BOMBANDO! 🔥"
    else:
        opcoes = GANCHOS.get(o.get("grupo"), ["ACHADINHO DO DIA ✨", "PROMO NO AR, NÃO PERDE! 🏃‍♀️"])
        gancho = opcoes[n % len(opcoes)]
    preco = (f"De {_brl(o['preco_antigo'])} por *{_brl(o['preco'])}*" if o.get("preco_antigo")
             else f"Por *{_brl(o['preco'])}*")
    linhas = [f"*{gancho}*", "", o["titulo"][:100], f"🏬 {o.get('loja') or ''}", preco]
    if o.get("cupom"):
        linhas.append(f"🎟️ Cupom: *{o['cupom']}*")
    linhas += ["", f"👉 {link_afiliado(o['link_loja'])}", "", "_Preço e cupom podem mudar a qualquer momento._"]
    if n % 5 == 4:  # como os grupos grandes: de vez em quando pede indicação (crescimento sem pegar número de ninguém)
        linhas += ["", f"💌 Indique pra uma amiga: {canais().get('site', SITE_URL)}"]
    return "\n".join(linhas)


SITE_URL = "https://bellalucce.github.io"


def canais() -> dict:
    """Links públicos dos canais (config/achadinhos.json): grupo_whatsapp, telegram, site."""
    arq = config.CONFIG / "achadinhos.json"
    return {"site": SITE_URL, **(json.loads(arq.read_text(encoding="utf-8")) if arq.exists() else {})}


def fila_posts(n: int = 5, horas: int = 30) -> list[dict]:
    """Próximas ofertas para o grupo: aprovadas, com link e foto, ainda não postadas; o melhor de cada tipo de produto
    (sem repetir tipo postado nas últimas 24 h) e variando categoria (sem 3 iguais seguidas)."""
    recentes = {chave_produto(r["titulo"]) for r in db.consultar(
        "SELECT titulo FROM ofertas WHERE publicado_em >= datetime('now','localtime','-24 hours')")}
    cand = [o for o in sem_repetidos(melhores(horas, 3000)) if o["link_loja"] and o.get("foto") and not o["publicado_em"]
            and chave_produto(o["titulo"]) not in recentes]
    out, ultimo = [], []
    for o in cand:
        if len(ultimo) >= 2 and ultimo[-1] == ultimo[-2] == o["grupo"]:
            continue
        out.append(o)
        ultimo.append(o["grupo"])
        if len(out) >= n:
            break
    return out


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
            ruim = tem_afiliado_terceiro(link) or (link and not any(re.search(p, link) for p in PAGINA_PRODUTO))
            nosso = config.segredos().get("afiliados", {}).get("amazon_tag")
            if final and "tag=" in final and (not nosso or f"tag={nosso}" not in final):
                ruim = True  # tag de Amazon que não é a nossa
            if ruim:
                problemas.append(f"{r['id']} {link}")
                con.execute("UPDATE ofertas SET aprovada = 0 WHERE id = ?", (r["id"],))
    if problemas:
        with open(config.DADOS / "achadinhos" / "auditoria.log", "a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M} derrubadas: {problemas}\n")
    return problemas


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
    shutil.copyfile(config.CONFIG / "achadinhos.json", radar / "config" / "achadinhos.json")
    af = config.segredos().get("afiliados") or {}
    publicos = {k: af[k] for k in ("amazon_tag", "magalu_loja", "ml_tool", "ml_etiqueta") if k in af}
    (radar / "config" / "afiliados.json").write_text(json.dumps(publicos, indent=1), encoding="utf-8")
    ml = db.consultar("SELECT * FROM ofertas WHERE fonte = 'ml_afiliados' "
                      "AND atualizado_em >= datetime('now', 'localtime', '-36 hours') ORDER BY id")
    for r in ml:
        r["publicado_em"] = None
    (radar / "dados" / "ml.json").write_text(json.dumps(ml, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
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
