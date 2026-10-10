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
import functools
import html as _html
import json
import os
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path

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
                    "/promocoes/loja/farfetch/",
                    # 01/10: The Beauty Box (grupo Boticário) = perfume importado de grife (teste: 8 de 32 eram grife)
                    "/promocoes/loja/the-beauty-box/"]

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
              r"skincare|máscara facial|mascara facial|esmalte|secador|chapinha|escova|delineador|paleta|blush|colônia|body splash|"
              r"p[óo] (?:compacto|facial|solto)|powder|\bpact\b|cushion",  # 01/10: "innisfree… powder pact" caía em outros
    "moda": r"tênis|tenis|camiseta|blusa|vestido|calça|calca|bolsa|sandália|sandalia|jaqueta|moletom|relógio|relogio|óculos|mochila|biquíni|"
            r"blazer|saia|regata|cropped|shorts?|bermuda|macacão|cardigan|sapatilha|rasteir|"  # 01/10 (Rafa): blazer caía em outros
            # 06/10 (Eva, vitrine fiel): joia/bijuteria e calçado fofo da divulgação caíam em "outros" e nunca entravam no grupo
            r"\bbrincos?\b|\bcolar(?:es)?\b|bijuteria|semijoia|gargantilha|choker|pulseira|bracelete|\btiara\b|presilha|"
            r"\bmules?\b|scarpin|tamanco|mary ?jane|"
            # 09/10 (dona: "bolsa, sapato, acessório, pijama e tal"): pijama/roupa de dormir, cinto e calçado
            r"\bpijamas?\b|\bcamisolas?\b|baby[- ]?doll|short[- ]?doll|\bcintos?\b|papete|anabela",
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
CABELO = re.compile(r"shampoo|xampu|condicionador|m[áa]scara capilar|capilar|cabelo|secador(?! de (?:lou[çc]a|pratos?))|chapinha|prancha(?! abdominal)|babyliss|"
                    r"escova (secadora|alisadora|rotativa)|modelador de cachos|finalizador|leave-?in|[óo]leo capilar|"
                    r"t[ôo]nico capilar|progressiva|tintura|coloraç|anti-?queda|acidificante|bif[áa]sic|"
                   r"k[ée]rastase", re.I)  # 01/10: Lola Rapunzel; 03/10: bifásico Dove = cabelo; 06/10: Kérastase Masque
FITNESS = re.compile(r"bicicleta ergom|esteira|legging|top fitness|conjunto fitness|academia|halter|anilha|el[áa]stico de "
                     r"exerc|colchonete|yoga|pilates|whey|creatina|pr[ée]-?treino|squeeze|coqueteleira|corda de pular|"
                     r"suplemento em p|hipercal|carboidrat|albumina|bioimped|pasta de amendoim|barra de prote|"
                     r"termog[êe]nic|probi[óo]tic|\bprotein\b|isolate", re.I)
BEBE = re.compile(r"fralda|len[çc]o umedecido|\bbeb[êe]s?\b|infantil|mamadeira|chupeta|carrinho de beb|body infantil|"
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
    # 09/10 (mix com 3 de moda em 10): "Bolsa Feminina Transversal…" e "Bolsa Feminina Tote…" eram o MESMO tipo
    # ("bolsa feminina") → só 1 bolsa a cada 48 h. Na moda o "feminina/casual/elegante" não diz o tipo — sai da chave
    if pal and pal[0] in NOME_MODA:
        pal = [p for p in pal if p not in GENERICO_MODA] or pal
    return " ".join(pal[:2])


NOME_MODA = {"bolsa", "bolsas", "sandalia", "sandalias", "tenis", "sapato", "sapatos", "sapatilha", "rasteirinha",
             "rasteira", "mule", "tamanco", "scarpin", "relogio", "oculos", "colar", "brinco", "brincos", "pijama",
             "camisola", "cinto", "cintos", "presilha", "chinelo", "papete", "pulseira", "corrente", "conjunto"}
GENERICO_MODA = {"feminino", "feminina", "femininos", "femininas", "mulher", "moda", "casual", "elegante", "luxo",
                 "adulto", "blogueira", "confortavel", "leve", "lancamento", "promocao", "barato", "estiloso"}


_MERCADO: dict = {}  # cache por processo: [(tokens, preço)] das ofertas vistas nos últimos 7 dias


def _tokens_produto(titulo: str) -> set[str]:
    """Todas as palavras relevantes do título + tamanho colado ("100 ml" → "100ml")."""
    import unicodedata
    t = unicodedata.normalize("NFKD", (titulo or "").lower()).encode("ascii", "ignore").decode()
    t = re.sub(r"(\d+)[.,](\d+)\s*(ml|g|kg|l)\b", r"\1x\2\3", t)  # "1,5 L" → "1x5l" (uma palavra só)
    t = re.sub(r"(\d+)\s*(ml|g|kg|l)\b", r"\1\2", t)
    return {p for p in re.findall(r"[a-z0-9]+", t) if p not in STOP and len(p) > 2 and not p.isdigit()}


def acima_do_mercado(o: dict, limite: float = 1.4) -> bool:
    """02/10 (Beto): loja OFICIAL não garante preço honesto — Yara Lattafa a R$ 342 na loja oficial da Shopee, contra
    R$ 155–210 no ML (5–10 mil vendidos). Produto = as 2 palavras MAIS RARAS do título no banco ("yara", "lattafa") +
    o tamanho; "por" mais de 40% acima da MEDIANA das ≥ 3 outras ofertas com essas palavras = não vai para o grupo."""
    med = mediana_mercado(o)
    return med is not None and (o.get("preco") or 0) > limite * med


def mediana_mercado(o: dict) -> float | None:
    """Mediana do preço do MESMO produto (mesmas palavras raras + mesmo tamanho, sem kit) nas outras ofertas dos
    últimos 7 dias; None se não dá para comparar (< 3 outras)."""
    from collections import Counter
    from statistics import median
    if not o.get("preco"):
        return None
    if "lista" not in _MERCADO:
        _MERCADO["lista"] = [(_tokens_produto(r["titulo"]), r["preco"]) for r in db.consultar(
            "SELECT titulo, preco FROM ofertas WHERE preco > 0 AND visto_em >= datetime('now','localtime','-7 days')")]
        _MERCADO["df"] = Counter(p for tk, _ in _MERCADO["lista"] for p in tk)
        idx: dict[str, list[int]] = {}
        for i, (t2, _) in enumerate(_MERCADO["lista"]):
            for p in t2:
                idx.setdefault(p, []).append(i)
        _MERCADO["idx"] = idx  # palavra → ofertas (busca pela palavra mais rara: instantâneo)
    if re.search(r"\bkit\d*\b|\bcombo\b|\bconjunto\b|\b\d+\s*(?:un|unid|p[çc]s|pe[çc]as)\b", o.get("titulo") or "", re.I):
        return None  # kit/quantidade: não dá para comparar com o avulso
    tk = _tokens_produto(o.get("titulo") or "")
    tam = {p for p in tk if re.fullmatch(r"\d+(?:x\d+)?(?:ml|g|kg|l)", p)}
    if not tam:
        return None  # sem tamanho (roupa, acessório…): "curto" × "longo" confunde; só compara o que tem ml/g/L
    raras = sorted((p for p in tk - tam if not re.search(r"\d", p) and _MERCADO["df"].get(p, 0) >= 3),
                   key=lambda p: _MERCADO["df"][p])[:2]
    if len(raras) < 2:
        return None  # sem palavras que identifiquem o produto
    chave = set(raras) | tam
    lista = _MERCADO["lista"]
    outros = [lista[i][1] for i in _MERCADO["idx"].get(raras[0], []) if chave <= lista[i][0] and lista[i][1] != o["preco"]]
    return median(outros) if len(outros) >= 3 else None


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
        # 05/10: a tabela passa de dezenas de milhares de linhas (feed da Shopee) e Início/fila/servidor consultam
        # por data de post, aprovação e fonte a toda hora — sem índice era varredura completa em cada consulta
        con.execute("CREATE INDEX IF NOT EXISTS ix_ofertas_publicado ON ofertas(publicado_em)")
        con.execute("CREATE INDEX IF NOT EXISTS ix_ofertas_aprovada ON ofertas(aprovada, atualizado_em)")
        con.execute("CREATE INDEX IF NOT EXISTS ix_ofertas_fonte ON ofertas(fonte)")


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


PERFUME = re.compile(r"perfume|\bcol[ôo]nias?\b|body splash|"
                     r"(?<!desodorante )(?<!antitranspirante )body spray|eau de|parfum|deo col|\bed[pt]\b",
                     re.I)  # 06/10: "212 NYC Body Spray" (perfume) caía em beleza; desodorante body spray não


BEM_ESTAR = re.compile(r"vitamin|multivitam|suplement|col[áa]geno|whey|creatina|[ôo]mega ?3", re.I)
PET = re.compile(r"para (c[ãa]es|cachorros?|gatos?|pets?|felinos?|caninos?)|\bpet\b|\bra[çc][ãa]o\b|arranhador|"
                 r"caixa de areia|areia sanit|coleira|comedouro|cama de cachorro|casinha de cachorro", re.I)
CAMA_BANHO = re.compile(r"travesseiro|almofada|len[çc]ol|edredom|toalha|cobertor|manta de sof|tapete|"
                        r"papel higi[êe]nico|umidificador|balan[çc]a", re.I)  # casa (antes caíam em "beleza")


def _grupo_final(g: str, titulo: str) -> str:
    """Refina a categoria pelo título: perfume vira aba própria; categoria genérica tenta pelas palavras."""
    if PERFUME.search(titulo or "") and not re.search(r"expositor|organizador|porta[- ]|sem (?:perfume|fragr[âa]ncia)|"
                                                       r"lan[çc]a[- ]perfume",  # 01/10: Lança Perfume = marca de roupa
                                                       titulo or "", re.I):  # 01/10 (Marcos): "Loção… Sem Perfume"
        return "perfume"
    if g == "perfume":  # gravado como perfume por engano (Lança Perfume, "sem perfume") → refaz pelas palavras
        g2 = _grupo(re.sub(r"(?i)lan[çc]a[- ]perfume|sem perfume", " ", titulo or ""))
        g = g2 if g2 in GRUPOS_OK else "outros"
    if re.search(r"smart ?watch|smart ?band|mi ?band|rel[óo]gio inteligente", titulo or "", re.I):
        return "eletronicos"  # 01/10 (Marcos): smartwatch/Mi Band ocupavam vaga de moda
    if re.search(r"cushion|tocobo", titulo or "", re.I):
        return "beleza"  # 01/10 (Marcos): "Almofada TOCOBO" é base cushion, não almofada de casa
    if re.search(r"porta[- ]?joias?|caixa de joias|porta[- ]?br?inco|porta[- ]?bijuteria", titulo or "", re.I):
        return "moda"  # 06/10 (Eva): porta-joias é acessório da vitrine, não "casa" (a regra feminina só deixa moda passar)
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
    if g in ("outros", "mercado", "esporte", "pet", "casa") and MARCAS_SKINCARE.search(titulo or ""):
        return "beleza"  # 01/10 (Rafa): "Principia Kit Essencial GL-03…" caía em outros
    if g in ("outros", "mercado", "esporte", "pet"):
        g2 = _grupo(titulo)
        return g2 if g2 in GRUPOS_OK else g
    return g


# ---------------- fontes ----------------
LINK_OU_FONE = re.compile(r"(?i)\b(?:https?://|www\.)\S+|\b[\w-]+(?:\.[\w-]+)*\.(?:com|net|org|ly|io|me|to|br|co|gl|app|link|"
                          r"site|xyz|info|shop|store)(?:\.br)?(?:/\S*)?\b|(?<![\w.])(?:\+?55\s?)?\(?\d{2}\)?\s?9?\d{4}[-\s]\d{4}(?!\d)|"
                          r"[\w.+-]+@[\w-]+\.[\w.]+")
# 06/10 (revisora da nuvem): "212 VIP Black … De: R$ 1048,90 Por" (sem o preço do "Por") ficava no título
PRECO_NO_TITULO = re.compile(r"\s+De:?\s*R\$\s*[\d.,]+(?:\s*Por:?.*)?$|\s+Selo:?\s*\d*\s*$", re.I)


CORES = (r"(?:preto|preta|branco|branca|marrom|nude|bege|rosa|azul|vermelho|vermelha|verde|amarelo|cinza|caramelo|"
         r"vinho|lil[áa]s|roxo|off ?white|chocolate|creme|dourado|dourada|prata|prateado|prateada|ouro|grafite|granito)")
# 01/10 (Nina): variação sem a palavra "Cor" no fim ("Bege Liso 40", "Médio Preto Liso Ouro", "Prateado Azul")
VARIACAO_FIM = re.compile(rf"(?:\s+(?:{CORES}|lis[oa]|estampad[oa]|m[ée]dio|pequen[oa]|grande|PP|GG|XG|\d{{2}}))+\s*$",
                          re.I)


def limpar_titulo(t: str) -> str:
    """30/09: títulos da Beleza na Web vêm com o preço colado ("... 200ml De: R$ 481,90 Por: R$ 279,90 Selo: 4") →
    no post saía o preço duas vezes e um "Selo: 4" sem sentido."""
    t = PRECO_NO_TITULO.sub("", (t or "").strip()).strip(" -–|")
    # 30/09 segurança: título vem da comunidade (Promobit) → link/telefone no título viraria link clicável no grupo
    # (golpe). O único link do post é o da loja, conferido por pagina_de_produto().
    t = re.sub(r"\s{2,}", " ", LINK_OU_FONE.sub(" ", t)).strip(" -–|:")
    # 01/10 (revisão do grupo): "[OFFICIAL] Epais…", "…Original Blogueira Promoção" — rótulo/palavra de anúncio no nome
    t = re.sub(r"^\s*[\[(【](?:official|oficial|original|hot|new|novo|promo\w*|sale)[\])】]\s*", "", t, flags=re.I)
    # 01/10 (Marcos): "…Original Blogueira Promoção Envio Da Coreia" — palavra de anúncio no MEIO também
    t = re.sub(r"\s+(?:blogueira|promo[çc][ãa]o|envio (?:da|do|de) (?:coreia|china|brasil)|envio r[áa]pido|"
               r"envio imediato|pronta entrega|frete gr[áa]tis|marca de luxo|top de linha|"
               r"top marca(?: de)? luxo|marca de topo)\b", "", t, flags=re.I)  # 02/10 (Marcos): CURREN "Top Marca Luxo"
    t = re.sub(r"\s+[Bb]y\s+[A-Z][\w']+\s*$", "", t).strip(" -–|:,")  # 02/10 (Marcos): "… Body Splash By Amaxxon" (loja)
    # 06/10 (Rita): código de variação no começo/fim ("006 Gloss…", "… Vult 1/2/3") e letra solta de tradução ("g Fosco…")
    t = re.sub(r"^(?:0\d{1,2}|[a-z])\s+(?=[A-Za-zÀ-ÿ])", "", t)
    # 06/10 noite (Rita): código da loja/SKU no começo ("ANA1108 Kit…", "ANA1108-12 Kits…", "42741 Creme…"), medida
    # americana colada ("30g/1.0FL.OZ| Clareamento…", "150mL/5.07 Fl.Oz") e palavra cortada pela fonte ("Aço Ino")
    t = re.sub(r"^(?:[A-Z]{2,5}\d{3,}(?:\s*-\s*\d{1,3})?|\d{5,})\s+(?=[A-Za-zÀ-ÿ])", "", t)
    t = re.sub(r"\s*[/,]?\s*\d+(?:[.,]\d+)?\s*fl\.?\s*oz\b\.?\s*\|?", " ", t, flags=re.I)
    t = re.sub(r"\s+([,)])", r"\1", re.sub(r"\s{2,}", " ", t)).strip(" -–|:,")
    t = re.sub(r"(?i)\b(a[çc]o) ino\b", r"\1 Inox", t)
    t = re.sub(r"\s+\d{1,2}(?:/\d{1,2})+\s*$", "", t).strip(" -–|:,")
    # 08/10 (Beto: Yara Moi "… 100ml Lipx" vetado e de volta): "Lipx"/"Com Nf" = etiqueta do vendedor do ML, não nome
    t = re.sub(r"(?:\s+(?:lan[çc]amento|original|oferta|barato|top|lipx|(?:com|c/)\s*nf(?:-?e)?|nota fiscal))+\s*$", "", t,
               flags=re.I).strip(" -–|:")
    # 01/10 (Nina): ML cola atributos da variação no fim ("… Cor-1 M", "… Padrão", "- Cor Preto Tamanho M")
    t = re.sub(r"(?:\s*[-|/]?\s*(?:\b(?:cor|tamanho|tam|voltagem)\b[\s:\-]+[\w-]+|\bmodelo\b[\s:\-]+\w*\d[\w-]*)"
               r"(?:\s+(?:PP|P|M|G|GG|XG|\d{2}))?)+\s*$", "", t, flags=re.I)  # "Modelo Ciganinha" fica; "Modelo X12" sai
    t = re.sub(r"(?:\s+(?:padr[ãa]o|[A-Z]{1,2}\d{2,}[-\w]*|cor-?\d+(?:\s+[PMG]{1,2})?))+\s*$", "", t, flags=re.I).strip(" -–|:/")
    # 01/10 (Rita): cores da variação ligadas por "+" com o tamanho solto no fim ("… Comfy Preto+marrom+nude M")
    t = re.sub(rf"\s*[-–]?\s*(?:{CORES}\+)+{CORES}(?:\s+(?:PP|P|M|G|GG|XG|\d{{2}}))?\s*$", "", t, flags=re.I).strip(" -–|:/")
    t = re.sub(r"\s+\bcor\b[\s:\-]+(?:[A-Za-zÀ-ÿ-]+\s*){1,2}$", "", t, flags=re.I).strip(" -–|:/")  # "Cor Preto Granito"
    m = VARIACAO_FIM.search(t)
    if m and len(m.group(0).split()) >= 2 and re.search(CORES, m.group(0), re.I) and len(t[:m.start()].split()) >= 3:
        t = t[:m.start()].strip(" -–|:/")
    # SKU antes da marca ("… Corporal SKB001 - Koasis") e ficha técnica entre barras ("… RPM | 5 Modos | USB | IPX7")
    t = re.sub(r"\s+\b[A-Z]{2,4}\d{3,}[A-Z\d]*\b\s*(?=[-–]\s)", " ", t).strip()
    if t.count("|") >= 2 and len(t.split("|")[0].split()) >= 3:
        t = t.split("|")[0].strip(" -–|:/")
    t = sem_lista_de_palavras(t)
    # marca repetida colada ("Crrju Crju"): tira a 2ª palavra quase igual à anterior — 06/10 (Rita): a IGUAL com
    # maiúscula é parte do nome ("Amor Amor" virava "Amor Cacharel"; "Bora Bora") → fica
    ps = t.split()
    t = " ".join(p for i, p in enumerate(ps) if i == 0 or (p == ps[i - 1] and p[:1].isupper())
                 or re.sub(r"(.)\1", r"\1", p.lower()) != re.sub(r"(.)\1", r"\1", ps[i - 1].lower()))
    letras = [c for c in t if c.isalpha()]
    if len(t) > 20 and letras and sum(c.isupper() for c in letras) / len(letras) > 0.85:
        t = _sem_gritar(t)  # título TODO EM MAIÚSCULAS parece spam no grupo
    return t


def cortar(t: str, n: int) -> str:
    """01/10 (Marcos): "…Natu", "…Cravej" — corta no fim da palavra (e sem "de/com/para" sobrando no fim)."""
    if len(t) <= n:
        return t
    # 08/10 (Rita: Eucerin "… 200ml, Anti-Pigment, Clareador Joelho, Coxa…"): título longo com LISTA de atributos depois
    # de vírgula (Amazon/ML) → fica o nome até a 1ª vírgula que fecha o produto (≥ 4 palavras; "Lancôme, La Vie est
    # Belle EDP, …" junta a marca), + o tamanho se ele só aparece depois
    partes = re.split(r"\s*,\s+", t)
    if len(partes) >= 2:
        nome = partes[0]
        for p in partes[1:-1]:
            if len(nome.split()) >= 4:
                break
            nome += ", " + p
        if len(nome.split()) >= 4 and len(nome) >= 25 and len(nome) <= n:
            if not TAMANHO_TITULO.search(nome) and (tam := TAMANHO_TITULO.search(t[len(nome):])) \
                    and len(nome) + len(tam.group(0)) < n:
                nome += " " + tam.group(0)
            return _fim_limpo(nome)
    t = t[:n + 1].rsplit(" ", 1)[0] if " " in t[:n + 1] else t[:n]
    if t.count("(") > t.count(")"):  # 06/10 (Rita): "… Kit (3" — parêntese aberto pelo corte
        t = t[:t.rfind("(")]
    # 06/10 noite (Rita): cortar onde a frase fecha — no último separador (", " " - " " | " " + ") se ele está na 2ª
    # metade ("… Para Mulheres Meninas , Conjunto" → "… Para Mulheres Meninas")
    seps = [m.start() for m in re.finditer(r"\s*(?:,|\s[-–|+])\s", t)]
    if seps and seps[-1] >= n * 0.5:
        t = t[:seps[-1]]
    else:
        # 08/10 (Rita: óleo de banho "… 200ml Nutrição Intensa Rosa Mosqueta Vitamina E Pele"): título-sopa sem
        # separador → fecha logo depois do TAMANHO ("… Beleza Brasileira 200ml"), se ele não está no comecinho
        tams = [m for m in TAMANHO_TITULO.finditer(t) if m.end() >= n * 0.35]
        if tams and len(t[tams[-1].end():].split()) >= 2:
            t = t[:tams[-1].end()]
    return _fim_limpo(t)


TAMANHO_TITULO = re.compile(r"(?i)(?<![\w,.])\d+(?:[.,]\d+)?\s?(?:ml|g|kg|l|oz)(?!\w)")


def _fim_limpo(t: str) -> str:
    """Sem palavra que pede continuação no fim ("Delicado Não" [Escurece], "… Sem" [Acetona], "… Tipo")."""
    if re.search(r"(?i)\bvit(?:amina|\.)?\s?E$", t):  # "… com Vitamina E": o "E" é o nome da vitamina
        return t.strip(" ,;-–|:/(")
    return re.sub(r"(?:\s+(?:de|da|do|das|dos|e|com|para|pra|em|no|na|nos|nas|ao|aos|à|às|a|o|os|as|um|uma|que|n[ãa]o|"
                  r"sem|muito|mais|super|tipo|estilo|ou|p/|c/|\+|-|–|/))+\s*$", "", t, flags=re.I).strip(" ,;-–|:/(")


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
    # público principal é feminino; 01/10 (dona) masculino também entra (2 vagas em 10 na fila do grupo) → só um ajuste leve
    p -= 6 if MASCULINO.search(titulo) else 0
    p += 4 if re.search(r"feminin|mulher", titulo, re.I) else 0
    p += 10 if EM_ALTA.search(titulo) else 0               # 01/10 (dona): "mais promoção de coisas que estão em alta"
    return round(max(0.0, min(100.0, p)), 1)


# 01/10 (dona: "quero mais promoção de coisas que estão em alta"): o que está viral em beleza no Brasil agora
# (TikTok Shop/ELLE/Beleza na Web, out/2026) — revisar 1×/mês: cerebro/10-Mercado/2026-10-01-Em-alta-beleza.md
EM_ALTA = re.compile(
    r"vitamina c|[áa]cido hialur[ôo]nico|baba de caracol|snail|mucin|protetor solar com cor|protetor com cor|"
    r"m[áa]scara de argila|argila|patch(?:es)? (?:de |para )?espinha|pimple|lip ?oil|[óo]leo labial|gloss|"
    r"blush (?:l[íi]quido|cremoso|em bast[ãa]o)|body splash|body mist|hair mist|perfume [áa]rabe|lattafa|"
    r"escova secadora|escova alisadora|l[âa]mina (?:facial|de sobrancelha)|dermaplan|rolo de (?:jade|quartzo)|"
    r"gua ?sha|niacinamida|retinol|rare beauty|sol de janeiro|glow recipe|e\.?l\.?f\.? cosmetics|\belf\b|missha|"
    r"forever liss|wepink|boca rosa|sallve|principia|\bcreamy\b|cosrx|\banua\b|skin ?1004|beauty of joseon|medicube|tirtir",
    re.I)


MASCULINO = re.compile(r"masculin|\bmen\b|\bhomem\b|cueca|\bboxer\b|barbear|\bbarbas?\b|p[óo]s[- ]barba|"
                       r"m[áa]quina de (acabamento|corte|cortar)|aparador de pelos|barbeador|testosteron|"
                       r"cortador de cabelo|groom|trimmer|clipper|"
                       # 30/09: perfumes masculinos famosos que não dizem "masculino" no título (Lattafa Asad caiu no vídeo)
                       r"\basad\b|fakhar black|club de nuit intense man|\bsauvage\b|bleu de chanel|\b1 million\b|"
                       r"\binvictus\b|\beros\b|\bstronger with you\b|\bpour homme\b|\bfor him\b|\bhomme\b|"
                       # 02/10 (Mila): Azzaro Wanted (masculino; "Wanted Girl" é feminino) passava como grife
                       r"azzaro wanted(?! girl)|the most wanted|acqua di gi[oò]|\ble male\b|"
                       # 05/10 (dona: "corrija todas as frases"): Habit Rouge saiu "CHEIROSA O DIA INTEIRO, AMIGA"
                       r"habit rouge|\buomo\b|boss bottled|\bphantom\b|\bbad boy\b|\bpolo (black|blue|red|sport)\b|"
                       # 08/10 noite (Rita/Beto: Ralph's Club, Malbec e Eudora H saíam com "CHEIROSA O DIA INTEIRO"):
                       # LINHAS masculinas nacionais e de grife que não dizem "masculino" no título. Kaiak tem versão
                       # feminina ("Kaiak Sonar Feminino") — o "feminino" no título libera (eh_masculino)
                       r"ralph'?s club|\bmalbec\b|\beudora h\b|\bkaiak\b|\bquasar\b|\bzaad\b|\barbo\b|\buomini\b|"
                       r"\bclub 6\b|\bportinari\b|\bthe blend\b|coffee man|\bman\b|\bmale\b|\bgentleman\b|luna rossa|"
                       r"\bfahrenheit\b|terre d.?herm[èe]s|spicebomb|\btoy boy\b|dylan blue|\baventus\b|\bhawas\b|"
                       r"bharara king|\b9 ?pm\b|"
                       # 09/10 (treino): "Paco Rabanne One Million" saiu no grupo — só "1 Million" era pego.
                       # "Lady Million"/"Million Gold for Her" são femininos (não entram)
                       r"\bone million\b(?! (lucky )?(for her|woman))|\bmillion (parfum|prive|priv[ée]|elixir|royal)\b", re.I)


def cupom_valido(c: str | None) -> str | None:
    """Cupom só se parecer código de verdade (a fonte às vezes manda texto como 'do anúncio')."""
    c = (c or "").strip()
    # 08/10: o NOSSO cupom de programa vem com o valor ("BELLALUCCE (10% OFF)", Océane) — o reavaliar() apagava
    cod = re.sub(r"\s+\(\d{1,2}% OFF\)$", "", c)
    return c if re.fullmatch(r"[A-Za-z0-9_-]{3,24}", cod) and not re.fullmatch(r"(?i)cupom|desconto|anuncio", cod) else None


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
# 01/10 (Rafa): "Maleta de Maquiagem… de Viagem" era barrada — viagem só é spam quando é PACOTE/destino, não produto
SPAM = re.compile(r"cupo(m|ns)|pacote|pacote de viagem|viagens? (?:para|pra|nacional|internacional|com)|passage|hotel|"
                  r"\bvoo|assinatura|receb(a|er) |grupo d|whatsapp|telegram|"
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
    r"belezanaweb\.com\.br/[a-z0-9-]+/?$", r"beautybox\.com\.br/[a-z0-9-]+/?$",  # The Beauty Box: grife (01/10)
    r"dafiti\.com\.br/.+-\d+\.html", r"renner\.com\.br/.+/p/\d+",
    r"boticario\.com\.br/[a-z0-9-]+/?$", r"natura\.com\.br/p/", r"epocacosmeticos\.com\.br/[a-z0-9-]+/p",
    r"vivara\.com\.br/[a-z0-9-]+/p", r"pandora\.(?:com\.br|net)/.+\.html",  # joias (sem comissão até o cadastro na Awin)
    r"farfetch\.com/br/shopping/[a-z]+/[a-z0-9-]+-item-\d+\.aspx",  # luxo (30/09)
    r"docebeleza\.com\.br/products/[a-z0-9-]+/?$",  # 05/10: Doce Beleza (Awin, aprovada) — link_afiliado põe o link Awin
    # 08/10: Océane (#SQUAD/Flip) — VTEX "/<slug>/p"; linha Mariana Saad NUNCA (robots.txt /*saad* e fora do programa)
    r"oceane\.com\.br/(?![a-z0-9-]*saad)[a-z0-9-]+/p(?:[?#]|$)",
    # 09/10: Shein pela Lomadee (fonte lomadee_shein) — só aprovada com o NOSSO lmdee.link em sinais (aprovada())
    r"br\.shein\.com/(?:[^?#]*?-)?p-\d+(?:-cat-\d+)?\.html",
]
AFILIADO_TERCEIRO = re.compile(r"[?&](tag|promoter_id|partner_id|matt_tool|matt_word|utm_[a-z]+|aff[a-z_]*|affiliate|"
                               r"clickid|smtt|pid|lp|ref|sp_atk|mmp_pid)=|divulgador|meli\.la|s\.shopee|shope\.ee|amzn\.to|"
                               r"linksynergy|awin|/social/|afiliad|onelink|bit\.ly|tidd\.ly|promobit", re.I)


def tem_afiliado_terceiro(url: str | None) -> bool:
    """True se o link (antes de pormos o NOSSO código) ainda carrega qualquer sinal de afiliado/rastreio de outros."""
    return bool(url) and bool(AFILIADO_TERCEIRO.search(url))


# 01/10 (Rita): hospitalar/íntimo/promessa de tratamento não combina com o grupo de achadinhos
SENSIVEL = re.compile(r"(?i)cadeira de rodas|hospitalar|ortop[ée]dic|pulseira (?:m[ée]dica|de identifica)|autis|"
                      r"fralda geri[áa]trica|sonda|bolsa de colostomia|preservativo|lubrificante [íi]ntimo|vibrador|"
                      r"sex ?shop|redutor (?:de )?(?:celulite|medidas|gordura|barriga|abd[ôo]men)|emagrec|"
                      r"queima de gordura|antiacne|anti-acne|empina|balaclava|touca ninja|"  # (Nina 01/10)
                      # cinta/roupa modeladora sim; "escova modeladora", "pasta modeladora" de barba não (verificador 01/10)
                      r"(?:cinta|calcinha|body|bermuda|shorts?|camiseta|regata|faixa|meia|cueca|macac[ãa]o|"
                      r"cal[çc]a|legging|lingerie)\s+(?:\w+\s+)?modeladora|modeladora\s+(?:de\s+)?(?:barriga|cintura|abd)|"
                      r"clareador (?:[íi]ntimo|de virilha)|calv[íi]cie|disfun[çc]")
# 04/10 (revisora da nuvem: 33 vetos em 2 rodadas; dona: "o grupo não pode cair a qualidade") — o que ela mais vetava
# vira regra no código, que roda no servidor 24 h sem gastar token e sem depender do PC: suplemento de academia
# (creatina/whey/barra de proteína) e item técnico/esquisito não é achadinho para "mulher que ama comprar".
# Cosmético com o nome (tônico capilar "Whey Amino") passa — COSMETICO é conferido em fora_do_perfil.
FORA_PERFIL = re.compile(r"(?i)creatina|\bwhey\b|pr[ée]-?treino|\bbcaa\b|termog[êe]nic|hipercal[óo]ric|albumina|"
                         r"barra (?:de )?prote[íi]na|protein bar|bioimped|faqueiro|desafio da corda|"
                         # 04/10 (dona: "isso não quero" — kit de pentes "+ aleatório"): brinde/cor ALEATÓRIA = produto
                         # genérico de marketplace; pente/kit de penteado não é achadinho
                         r"aleat[óo]ri|sortid|\bpentes?\b|penteados?\b")


def fora_do_perfil(titulo: str) -> bool:
    return bool(FORA_PERFIL.search(titulo or "")) and not COSMETICO.search(titulo or "")


DE_MAX = 2.0  # 04/10 (revisora da nuvem): "De" acima de 2× o "Por" = desconto de vitrine (era 4×)


def sinais_de(o: dict) -> dict:
    """Sinais da oferta como dict (no banco é texto JSON). 08/10 (revisão): vazio, "null", lista ou JSON quebrado = {} —
    antes o json.loads solto derrubava a legenda e, com ela, a rodada inteira do grupo (o robô não tem try em volta)."""
    s = o.get("sinais")
    if isinstance(s, str):
        try:
            s = json.loads(s) if s.strip() else {}
        except ValueError:
            s = {}
    return s if isinstance(s, dict) else {}


def de_inflado(o: dict) -> bool:
    """"De" mais de 2× o preço, fora de loja oficial (a oficial tem o "De" de tabela da marca)."""
    s = sinais_de(o)
    return (o.get("preco_antigo") or 0) > DE_MAX * (o.get("preco") or 0) > 0 and not s.get("oficial")


INGLES =re.compile(r"(?i)\b(?:for|with|and|women|woman|men|lady|girls?|waterproof|long[- ]lasting|makeup|lipstick|"
                    r"set|pcs|color|natural|matte|face|eye|lip|lips|new|hot|sale|fashion|style|quality|high|"
                    r"portable|wireless|mini|cute|luxury|brand|original|"
                    # 01/10 (verificador): "Boncept Waterproof Eyeliner 2 Colors" saiu às 21h15
                    r"colors|eyeliner|eyeshadow|tint|liquid|cream|types?|ea|\d+(?:types?|ea|colors?))\b")
LIXO_TRADUCAO = re.compile(r"(?i)[，、【】]|[一-鿿]|\bdos homens\b|\bdas mulheres\b|portas rel[óo]gios|"
                           r"marca de luxo|\bnovo estilo\b|\bmoda nova\b|\w+waterproof|prova d\W?water|"
                           r"cosm[ée]ticos \d+ cores|para deslocamento|\bde arte de\b")  # (Nina/Rita 01/10)
# 01/10 (Nina): peça vendida com o nome do aparelho na frente ("Cortador De Cabelo Kemei 2299 Pentes Guia" por R$ 29,99
# = só os pentes) → o gancho e a foto vendem o aparelho. Aparelho nas 4 primeiras palavras + peça/refil depois = fora.
APARELHO = re.compile(r"(?i)^(?:\S+\s+){0,3}?(?:cortador|m[áa]quina de (?:cortar|corte|barbear)|aparador|barbeador|"
                      r"secador|chapinha|prancha|escova (?:rotativa|secadora|alisadora)|babyliss|depilador|"
                      r"liquidificador|aspirador|cafeteira|air ?fryer|fritadeira|purificador|umidificador)\b")
PECA = re.compile(r"(?i)\b(?:pentes? (?:guias?|de encaixe|limitadores?)|(?:kit|jogo) (?:de )?pentes|refil|refis|"
                  r"l[âa]minas? (?:de reposi[çc][ãa]o|extras?|sobressalentes?)|cabe[çc]a de reposi[çc][ãa]o|"
                  r"pe[çc]as? de reposi[çc][ãa]o|s[óo] (?:o |a )?(?:pente|l[âa]mina|base|capa))\b")


def so_peca(titulo: str) -> bool:
    m = APARELHO.search(titulo or "") and PECA.search(titulo or "")
    # "Máquina Kemei Com 4 Pentes Guia" = aparelho que ACOMPANHA os pentes → passa
    return bool(m) and not re.search(r"(?i)(?:\bcom|\bacompanha|\+|\bmais|\be)(?:\s+\d+)?\s*$", titulo[:m.start()])


def titulo_ruim(titulo: str) -> bool:
    """01/10 (Rita): título de marketplace chinês com lixo de tradução, caractere chinês ou quase todo em inglês."""
    if LIXO_TRADUCAO.search(titulo or ""):
        return True
    palavras = re.findall(r"[A-Za-zÀ-ÿ]{3,}", titulo or "")
    if re.search(r"(?i)\b\d+(?:ea|types?|pcs)\b", titulo or ""):  # "dermask 1ea 11types": unidade em inglês colada
        return True
    ingl = [w for w in INGLES.findall(titulo or "") if not (w.lower() == "cream" and re.search(r"(?i)\bcreme\b", titulo))]
    return len(palavras) >= 4 and len(ingl) / len(palavras) >= 0.4


def sem_lista_de_palavras(t: str) -> str:
    """01/10 (Rita): título-lista de palavra-chave ("Mochila Térmica… Almoço, Mochila De Trabalho…, Mochila Para
    Notebook"; "CURREN Relógio Dourado Feminino Relógio com Pulseira… Relógio Feminino") → corta antes da 2ª vez que a
    palavra (3+ vezes no título) começa uma frase nova. "Gel de Limpeza", "Ativador de Cachos" (depois de de/para/com)
    é descrição, não lista — fica."""
    ps = t.split()
    chaves = [re.sub(r"\W", "", p).lower() for p in ps]
    repetidas = [c for c in dict.fromkeys(chaves) if len(c) >= 4 and c not in MIUDAS
                 and c not in ("para", "kit", "unidades") and chaves.count(c) >= 3 and chaves.index(c) <= 3]
    if not repetidas:
        return t
    w = repetidas[0]  # a palavra principal (aparece primeiro no título)
    i2 = [i for i, c in enumerate(chaves) if c == w][1]
    if i2 < 4 or chaves[i2 - 1] in MIUDAS:
        return t
    return re.sub(r"(?:\s+(?:de|da|do|e|com|para|em|\+|-|–|/))+\s*$", "", " ".join(ps[:i2]), flags=re.I).strip(" ,;-–|:/(")


CALCADO = re.compile(r"(?i)sand[áa]lia|rasteir|tamanco|chinelo|sapat|t[êe]nis|\bbotas?\b|\bmules?\b|scarpin|papete|"
                     r"anabela|\bslides?\b|babuche|pantufa|mocassim")


def fora_da_linha(titulo: str) -> bool:
    """FORA, menos a "palmilha" que só DESCREVE um calçado ("Rasteirinha Feminina Confortável Palmilha Fofinha" —
    09/10 noite: 6 sandálias boas da busca de moda da Shopee eram reprovadas); palmilha avulsa continua fora."""
    t = titulo or ""
    return any(not (m.group(0).lower() == "palmilha" and CALCADO.search(t[:m.start()])) for m in FORA.finditer(t))


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
    if o.get("grupo") not in GRUPOS_OK or SPAM.search(titulo) or VOLUMOSOS.search(titulo) or fora_da_linha(titulo):
        return False
    if SENSIVEL.search(titulo) or titulo_ruim(titulo) or so_peca(titulo):  # 01/10 (Rita/Nina: 16 de 40 vetadas)
        return False
    if fora_do_perfil(titulo):
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


# 30/09: marcas muito falsificadas no marketplace. 07/10: + marcas do Acervo da dona — "Rare Beauty" a R$ 25 em loja comum
# da Shopee foi ao grupo 06/10 (réplica: o original só vende na Sephora, Soft Pinch R$ 149+); SHEGLAM e rhode idem
# 09/10 noite: "Novo Batom Matte Mac 3.5g… Matte Mac Kiss" (R$ 55, loja comum) veio na busca por tipo — maquiagem de grife
# que o marketplace copia entra na mesma regra (só loja oficial / nota e vendas altas)
FALSIFICAVEL = re.compile(rf"k[ée]rastase|rare\s*beauty|\brhode\b|she\s?glam|{ganchos.KB}|"
                          r"(?<![\w.])m\.?a\.?c\b(?! ?(?:book|mini|os\b))|\bnars\b|fenty|huda beauty|urban decay|"
                          r"charlotte tilbury|anastasia beverly", re.I)


VALIDADE = re.compile(r"(?i)(?:exp|val(?:idade)?|venc\w*)\.?\s*[:.]?\s*(?:(20\d\d)[./-](\d{1,2})[./-](\d{1,2})|"
                      r"(\d{1,2})[./-](\d{1,2})[./-](20\d\d)|(\d{1,2})[./-](20\d\d))")


def vencendo(titulo: str, folga_dias: int = 60) -> bool:
    """01/10 (Mila): saiu sérum "(exp . 2026.09.04)" — título com validade já passada ou a menos de 2 meses = não posta."""
    m = VALIDADE.search(titulo or "")
    if not m:
        return False
    g = m.groups()
    try:
        if g[0]:
            v = date(int(g[0]), int(g[1]), int(g[2]))
        elif g[5]:
            v = date(int(g[5]), int(g[4]), int(g[3]))
        else:
            v = date(int(g[7]), int(g[6]), 28)
    except ValueError:
        return False
    return (v - date.today()).days < folga_dias


def aprovada(o: dict) -> bool:
    s, d = o.get("sinais") or {}, o.get("desconto") or 0
    if not eh_produto(o) or vencendo(o.get("titulo") or ""):
        return False
    # 01/10 (relógio Curren "De R$ 670 por R$ 109,99"; Rita: secador/cinto/cadeira > 4×): "De" muito acima do preço é
    # vitrine inflada, não desconto real (04/10: limite 2×, DE_MAX)
    if de_inflado(o):
        return False
    if s.get("ruim", 0) > s.get("otima", 0):
        return False
    if o.get("grupo") in ("beleza", "cabelo", "perfume") and o["fonte"] == "promobit":  # foco: mais permissivo
        return s.get("top") or d >= 30 or s.get("otima", 0) >= 3 or s.get("curtidas", 0) >= 5
    # "só promoções muito boas": desconto alto + algum sinal de qualidade (loja oficial, nota, voto da comunidade)
    if o["fonte"] == "ml_ofertas":
        return d >= 55 or (d >= 40 and (s.get("oficial") or (o.get("nota") or 0) >= 4.6))
    if o["fonte"] == "awin_docebeleza":  # 05/10: conferida à mão (loja de dermo, "de" = tabela das marcas): ≥ 25% off
        return d >= 25 and o.get("grupo") in ("beleza", "cabelo", "perfume")
    if o["fonte"] == "magalu_epoca":  # 06/10 (dona): Época no Magalu, "de" conferido no site da própria Época
        return d >= 25 and o.get("grupo") in ("beleza", "cabelo", "perfume") and bool(s.get("de_conferido"))
    if o["fonte"] == "oceane_afiliados":  # 08/10 (dona): loja oficial da marca, "de" = ListPrice da própria loja (VTEX)
        # "fora" = a rodada seguinte viu o produto sem promoção/sem estoque (integracoes/oceane.py) → sai até voltar
        return d >= 15 and o.get("grupo") in ("beleza", "cabelo", "perfume") and not s.get("fora")
    # 09/10 (dona): Shein SÓ pela Lomadee, só moda e só com o NOSSO lmdee.link (sem ele o post sairia com link direto da
    # Shein, sem monetização) — Shein vinda de outra fonte (Promobit…) nunca
    if SHEIN_PRODUTO.match((o.get("link_loja") or "").split("?")[0]) or o["fonte"] == FONTE_SHEIN:
        return (o["fonte"] == FONTE_SHEIN and o.get("grupo") == "moda" and d >= 15 and not s.get("fora")
                and bool(LINK_LOMADEE.match(s.get("link_lomadee") or "")))
    if o["fonte"] in ("ml_afiliados", "shopee_afiliados", "amazon_ref"):  # sem votos → desconto + nota + vendas
        nota, vend = o.get("nota") or 0, s.get("vendidos_num", 0)
        # Shopee: coreano/Kérastase/maquiagem importada SÓ de loja oficial (Shopee Mall) — o marketplace é cheio de cópia
        if o["fonte"] == "shopee_afiliados" and (FALSIFICAVEL.search(o.get("titulo") or "")
                                                 or LUXO.search(o.get("titulo") or "")) and not s.get("oficial"):
            return False
        # Kérastase/coreana: muita falsificação no marketplace e o painel não diz se é loja oficial → só anúncio com
        # nota ≥ 4,7 e +1.000 vendidos (falsificado junta avaliação "não é original"; a loja oficial tem 4,8–4,9 e +10 mil)
        if FALSIFICAVEL.search(o.get("titulo") or "") and not (nota >= 4.7 and vend >= 1000) and not s.get("oficial"):
            return False  # loja OFICIAL (feed "Shopee Oficial BR") já garante o original
        if s.get("pedido_dona"):  # 09/10: a dona mandou (`achadinhos pedido`) → passa por cima do desconto mínimo
            return True
        if o["fonte"] == "shopee_afiliados" and s.get("origem") == "vitrine_fiel":
            # 06/10 (Eva): semelhante do que a divulgação MOSTRA — barato e bonito pesa mais que % de desconto; exige
            # confiança (nota ≥ 4,6 e 100+ vendidos, ou loja oficial). O Beto/Rita ainda revisam a fila antes de postar.
            return bool(s.get("oficial")) or (nota >= 4.6 and vend >= 30)
        if o["fonte"] == "shopee_afiliados" and s.get("origem") == "moda_boa" and o.get("grupo") == "moda":
            # 09/10 noite (funil: moda 1 na fila): a busca de moda feminina de 09/10 (shopee_afiliados.coletar_moda) só
            # traz o que passa em moda_boa — mas aqui a moda ainda era julgada pela régua antiga do "resto" (50% OFF, ou
            # 30% + 1.000 vendas, ou 20% + 10 mil): 85 de 330 boas eram coletadas e reprovadas a cada rodada. A moda do
            # mix 6/3/1 vale pela régua DELA (nota, vendas e desconto mínimos de moda_boa); "De" inflado (> 2×) já
            # saiu acima e o perfil feminino/"De" no post continuam conferidos na fila.
            from vendas.integracoes import shopee_afiliados as _sa
            return nota >= _sa.MODA_NOTA_MIN and vend >= _sa.MODA_VENDAS_MIN and d >= _sa.MODA_DESC_MIN
        bom, viral = nota >= 4.6 and vend >= 1000, nota >= 4.5 and vend >= 10000
        # 07/10 (dona: "cadê as maquiagens?" — Rafa: Maybelline Sky High da loja oficial, nota 5, -24%, barrada só por ter
        # menos de 1.000 vendidos): loja OFICIAL bem avaliada já é confiável → conta como "bom"
        bom = bom or (bool(s.get("oficial")) and nota >= 4.5)
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
# 08/10: utmi_pc/utmi_cp = código de afiliado de loja VTEX (Océane, Época…) — de TERCEIRO sai aqui; o nosso entra no
# link_afiliado (link_oceane)
RASTREIO = re.compile(r"tag|ref_|matt_[a-z_]+|utm_[a-z]+|utmi_[a-z]+|af_[a-z_]+|smtt|sp_atk|xptdk|mmp_pid|uls_trackid|gads_t_sig|lp|c|"
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
             and not (k == "q" and "belezanaweb" in partes.netloc)  # ?q= da Beleza na Web = rastreio de busca
             and "beautybox.com.br" not in partes.netloc]  # The Beauty Box: a página do produto não usa parâmetro
    url = urlunsplit(partes._replace(query=urlencode(query)))
    url = re.sub(r"/divulgador/oferta/(\w+)/", r"/p/\1/", url)  # Magalu divulgador → página normal do produto
    m = re.search(r"shopee\.com\.br/(?:opaanlp|product)/(\d+)/(\d+)", url)
    if m:
        url = f"https://shopee.com.br/product/{m.group(1)}/{m.group(2)}"
    if "/social/" in url:  # meli.la de terceiros abre uma vitrine deles, não o produto → sem link (não posta)
        return None
    return url


AWIN_DOCEBELEZA = "https://www.awin1.com/cread.php?awinmid=76888&awinaffid=3111704"  # + &ued=<produto codificado>
NAO_PERMITE_WHATSAPP = re.compile(r"mercadolivre\.com\.br/|mercadolibre\.com/", re.I)
# 07/10 (DONA, por escrito no chat, depois de avisada do risco: "Pode por, já deveria por em tudo… Aceito o risco"):
# o ML Afiliados proíbe WhatsApp/Telegram, mas os grupos de referência usam o link de afiliado do ML e ela decidiu fazer
# igual → o grupo passa a levar o NOSSO link do ML. Para voltar ao link comum (sem etiqueta): False.
ML_AFILIADO_NO_WHATSAPP = True
# 08/10 (dona): Océane — programa #SQUAD na Flip; o link de afiliada é a página do produto com utmi_pc=<nosso código>
OCEANE_UTMI = "BELLALUCCE"
OCEANE_PRODUTO = re.compile(r"https://(?:www\.)?oceane\.com\.br/([a-z0-9-]+)/p(?:[?#].*)?$")


def link_oceane(slug: str) -> str:
    """Link da Océane com o NOSSO código (é o link_loja da fonte oceane_afiliados — o link_afiliado o devolve igual)."""
    return f"https://www.oceane.com.br/{slug.strip('/')}/p?utmi_pc={OCEANE_UTMI}"


# 09/10 (dona): Shein pela Lomadee — o post leva o NOSSO link monetizado (https://lmdee.link/…, canal bellalucce.github.io),
# guardado em sinais["link_lomadee"] da oferta lmdshein:<goods_id> (integracoes/lomadee_shein.py). Sem ele: None (o post
# não sai com link direto da Shein — aprovada() também barra)
FONTE_SHEIN = "lomadee_shein"
SHEIN_PRODUTO = re.compile(r"https://(?:br|m)\.shein\.com/(?:[^?#]*?-)?p-(\d+)(?:-cat-\d+)?\.html$", re.I)
LINK_LOMADEE = re.compile(r"^https://lmdee\.link/[A-Za-z0-9]{4,20}$")


def link_shein(goods_id: str) -> str | None:
    r = db.consultar("SELECT sinais FROM ofertas WHERE id = ?", (f"lmdshein:{goods_id}",))
    lk = sinais_de(dict(r[0])).get("link_lomadee") if r else None
    return lk if lk and LINK_LOMADEE.match(lk) else None


def link_afiliado(url: str | None, canal: str = "site") -> str | None:
    """Troca pelo NOSSO código de afiliado quando existir (config/segredos.json → afiliados).
    canal="whatsapp": até 07/10 o ML (que PROÍBE WhatsApp/Telegram — mercadolivre.com.br/l/afiliados-pode-compartilhar)
    saía como link comum, SEM a nossa etiqueta; desde 07/10 a dona aceitou o risco (ML_AFILIADO_NO_WHATSAPP)."""
    if not url:
        return None
    if canal == "whatsapp" and NAO_PERMITE_WHATSAPP.search(url) and not ML_AFILIADO_NO_WHATSAPP:
        return url
    if re.match(r"https://(?:www\.)?docebeleza\.com\.br/products/", url):  # 05/10: Awin (mid 76888, nossa conta 3111704)
        from urllib.parse import quote
        return f"{AWIN_DOCEBELEZA}&ued={quote(url, safe='')}"
    if m := OCEANE_PRODUTO.match(url):  # 08/10: já vem com o nosso utmi_pc → sai igual (outro código → vira o nosso)
        return link_oceane(m.group(1))
    if m := SHEIN_PRODUTO.match(url.split("?")[0]):  # 09/10: Shein → o NOSSO lmdee.link (Lomadee) guardado na coleta
        return link_shein(m.group(1))
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
        try:  # 01/10: link curto oficial gerado no painel (integracoes/shopee_links.py)
            from vendas.integracoes import shopee_links
            if curto := shopee_links.curto(url):
                return curto
        except ImportError:  # radar na nuvem do GitHub não leva a integração
            pass
        if curto := _shopee_curto(url):  # produto da Shopee vindo de outra fonte (Promobit): gera o NOSSO link
            return curto
        if af.get("shopee_id"):  # 01/10: link de afiliada no formato da Shopee (testado: chega com an_<nosso id>)
            from urllib.parse import quote
            return (f"https://shope.ee/an_redir?origin_link={quote(url, safe='')}&affiliate_id={af['shopee_id']}"
                    f"&sub_id={'grupo' if canal == 'whatsapp' else canal}")
    return url  # ML: link curto só pela ferramenta do painel


def _shopee_curto(url: str) -> str | None:
    """Link de afiliada da Shopee pela Open API (só com AppID/Senha; cache em dados/achadinhos/shopee_links.json)."""
    try:
        from vendas.integracoes import shopee_afiliados as sa
    except ImportError:  # radar na nuvem do GitHub não leva a integração
        return None
    if not sa.credenciais():
        return None
    from vendas.integracoes import shopee_links
    # 06/10 (Beto): arquivo vazio/pela metade derrubou a prévia das 21h de 05/10 (JSONDecodeError) → cache() devolve {}
    cache = shopee_links.cache()
    if url not in cache:
        try:
            curto = sa.link_curto(url)
        except Exception:  # noqa: BLE001 — sem o link curto, vai o link comum
            return None
        if not (curto and re.match(r"https://s\.shopee\.com\.br/\w+$", curto)):
            return None
        # 07/10 (revisão): junta ao cache relido e grava atômico — antes o {} da leitura pela metade era gravado por
        # cima e o cache inteiro de links curtos sumia
        shopee_links.gravar_pares({url: curto})
        return curto
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
            grupo = _grupo_final(it.get("grupo") or _grupo(titulo), titulo)  # feed traz a categoria (Beauty → beleza)
            vend = int(it.get("vendidos") or 0)
            sinais = {"tipo": "NORMAL", "comissao": it.get("comissao") or 0, "vendidos_num": vend,
                      "oficial": bool(it.get("oficial")), "loja_nome": it.get("loja_nome") or "",
                      "offer_link": it.get("offer_link") if re.match(r"https://s\.shopee\.com\.br/\w+$",
                                                                     it.get("offer_link") or "") else None}
            if it.get("origem") and it["origem"] != "moda_boa":
                sinais["origem"] = it["origem"]  # "referencia" = achado do grupo Ofertas Entre Mulheres
            else:  # 06/10 (Eva): o ciclo da Open API reabre o mesmo produto — não perde a marca "vitrine_fiel"
                if it.get("origem"):  # 09/10 noite: "moda_boa" (coletar_moda) — a "vitrine_fiel" de antes ganha
                    sinais["origem"] = it["origem"]
                antes = con.execute("SELECT sinais FROM ofertas WHERE id = ?", (f"shpaf:{it['id']}",)).fetchone()
                try:
                    if antes and json.loads(antes[0] or "{}").get("origem") == "vitrine_fiel":
                        sinais["origem"] = "vitrine_fiel"
                except ValueError:
                    pass
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


def gravar_ofertas(ofertas: list[dict]) -> dict:
    """08/10: grava no radar ofertas JÁ MONTADAS por uma integração (Océane: integracoes/oceane.py) — pontua, aprova
    (mesmas regras de todas as fontes) e atualiza preço/cupom/foto/link/sinais; visto_em e publicado_em ficam.
    Põe o["score"] e o["aprovada"] em cada oferta. sinais = dict."""
    _tabela()
    agora = datetime.now().isoformat(sep=" ", timespec="seconds")
    novas = aprov = 0
    with db.conectar() as con:
        for o in ofertas:
            o["score"] = pontuar(o)
            o["aprovada"] = ok = bool(aprovada(o))
            ant = con.execute("SELECT 1 FROM ofertas WHERE id = ?", (o["id"],)).fetchone()
            con.execute("""INSERT INTO ofertas (id, fonte, loja, titulo, grupo, preco, preco_antigo, desconto, cupom, foto,
                             link_fonte, link_loja, nota, vendidos, sinais, score, aprovada, visto_em, atualizado_em)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                           ON CONFLICT(id) DO UPDATE SET loja=excluded.loja, titulo=excluded.titulo, grupo=excluded.grupo,
                             preco=excluded.preco, preco_antigo=excluded.preco_antigo, desconto=excluded.desconto,
                             cupom=excluded.cupom, foto=excluded.foto, link_loja=excluded.link_loja, nota=excluded.nota,
                             vendidos=excluded.vendidos, sinais=excluded.sinais, score=excluded.score,
                             aprovada=excluded.aprovada, atualizado_em=excluded.atualizado_em""",
                        (o["id"], o["fonte"], o.get("loja"), o["titulo"], o["grupo"], o["preco"], o.get("preco_antigo"),
                         o.get("desconto"), o.get("cupom"), o.get("foto"), o.get("link_fonte"), o.get("link_loja"),
                         o.get("nota"), o.get("vendidos"), json.dumps(o.get("sinais") or {}, ensure_ascii=False),
                         o["score"], int(ok), agora, agora))
            _guardar_precos(con, [(o["id"], o["preco"])])
            novas += ant is None
            aprov += ok
    return {"gravadas": len(ofertas), "novas": novas, "aprovadas": aprov}


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


# ---- 06/10 (dona: "muita oferta repetida, muita que não é do nosso nicho, mal enquadrada, mal otimizada"): a vitrine do
# site usa os MESMOS filtros do grupo (no_perfil_feminino, fora_do_perfil, de_inflado, acima_do_mercado, TETO_TIPO) +
# o que só fazia falta no site: higiene/casa disfarçada de beleza, 1 por produto de verdade, foto boa, ordem boa.
FORA_SITE = re.compile(
    r"(?i)escova dental|creme dental|pasta de dente|fio dental|irrigador|l[íi]ngua|bacia|lava[- ]?roupas|amaciante|"
    # 07/10 (revisão): "ração" sem fronteira tirava do site "Longa Duração", "Reparação", "Coloração" (70 em 72 h);
    # "desodorante" tirava o hidratante/splash da Natura ("Desodorante Hidratante Corporal", "Desodorante Perfume") →
    # só o de axila (aerosol/roll-on/bastão/masculino); "necessaire" de BRINDE ("Corretivo… + Necessaire") fica
    r"guaran[áa]|comprimidos?|c[áa]psulas?|aspirador|antitranspirante|"
    r"desodorante(?=.*(?:aeross?ol|roll[- ]?on|\bstick\b|bast[ãa]o|masculin|\bmen\b))|massageador|"
    r"ventilador|liquidificador|panela|cortina|tapete|\bpet\b|cachorro|\bgato\b|\bra[çc][ãa]o\b|celular|capinha|fones?\b|"
    r"carregador|lanterna|ferramenta|porta[- ]joias|organizador|(?<!\+ )(?<!com )(?<!brinde )necessaire|"
    r"porta[- ]maquiagem|expositor|"
    r"fertilizante|inseticida|detergente|desinfetante|sab[ãa]o em (?:p[óo]|barra)|papel higi[êe]nico|absorvente|fralda|"
    r"\b[íi]nt[íi]m[oa]s?\b|bebida|alimento|col[áa]geno (?:hidrolisado|em p[óo])|\bch[áa] (?:de|para)\b|penteadeira|"
    r"camarim|\bmesa\b|cadeira|arm[áa]rio|prateleira")
SAPATO_FEM = re.compile(r"(?i)\b(?:sapato|sapatilha|sand[áa]lia|rasteira|tamanco|mule|scarpin|papete|anabela|t[êe]nis|bota|"
                        r"mocassim|salto|slide|chinelo)s?\b")
SAPATO_DE_MULHER = re.compile(r"(?i)feminin|mulher|dama|moleca|vizzano|anacapri|arezzo|schutz|santa lolla|capodarte|"
                              r"piccadilly|bottero|beira rio|modare")
PIJAMA_SEDA = re.compile(r"(?i)pijama|camisola|\brobe\b")
JOIA_RX = re.compile(r"(?i)\bcorrentes?\b|pulseiras?|bracelete|\bcolar(?:es)?\b|gargantilha|choker|\ban(?:el|[ée]is)\b|"
                     r"brincos?\b|tornozeleira|pingente|semijoia|\bjoias?\b")
DERMO = re.compile(r"(?i)la roche|vichy|bioderma|cetaphil|av[èe]ne|eucerin|isdin|dermachem|cerave|uriage|ducray|sesderma|"
                   r"mantecorp|skinceuticals|neutrogena|dermage|adcos|dermatol|dermocosm|sensibio|effaclar|cicaplast|"
                   r"anthelios|dermo|biretix|episol|nupill")
PERFUME_RX = re.compile(r"(?i)(?<!sem )perfume|eau de|parfum|body splash|body mist|\bsplash\b|col[ôo]nia|deo col[ôo]nia|"
                        r"\bedp\b|\bedt\b|[áa]rabe|lattafa|armaf|fragr[âa]ncia")
CABELO_RX = re.compile(r"(?i)shampoo|condicionador|capilar|cabelo|leave-?in|secador|chapinha|prancha|escova (?:secadora|"
                       r"modeladora|rotativa|alisadora)|modelador|babyliss|cachos|\bfios\b|tintura|selagem|progressiva|"
                       r"finalizador|reconstru|ampola de tratamento|hair|\bkerastase\b|k[ée]rastase|wella|truss|cadiveu")
LIMPEZA_RX = re.compile(r"(?i)remov|demaquil|micelar|limpeza|sabonete|cleanser|cleansing|espuma de limpeza")
MAKE_RX = re.compile(r"(?i)batom|gloss|\bbase\b|corretivo|r[íi]mel|c[íi]lios|sombra|paleta|blush|iluminador|delineador|"
                     r"l[áa]pis|pinc[ée]is|pincel|esponja|primer|fixador|p[óo] (?:compacto|solto|transl|facial)|translúcido|"
                     r"esmalte|unhas?\b|sobrancelha|maquiagem|contorno(?! (?:d[oa]s? |de )?olhos)|bronzer|\blip\b|"
                     r"lip ?(?:oil|tint|balm)|labial|tint\b|cushion|"
                     r"m[áa]scara de c[íi]lios|make")
MARCA_EXTRA = re.compile(r"(?i)vnox|rommanel|bamoer|curren|bio extratus|cadiveu|acquaflora|jacques janine|truss|lowell|amend|"
                         r"revlon|bra[ée]\b|itallian|trivitt|laikou|natura|botic[áa]rio|vizzano|moleca|anacapri|"
                         r"granado|phebo|nyx|maybelline|vult|kafurux|isoi|lizz|britânia|philco|taiff|mondial")
JOIA_TIPO = [(r"brincos?\b", "brinco"), (r"\bcolar(?:es)?\b|gargantilha|choker", "colar"), (r"pulseira|bracelete", "pulseira"),
             (r"\ban(?:el|[ée]is)\b", "anel"), (r"tornozeleira", "tornozeleira"), (r"presilha|piranha|tiara", "presilha")]
ABAS_SITE = [("make", "Make"), ("skincare", "Skincare"), ("dermo", "Dermo"), ("perfume", "Perfume"), ("cabelo", "Cabelo"),
             ("bolsas", "Bolsas & acessórios"), ("sapatos", "Sapatos"), ("pijama", "Pijama de seda")]
BONUS_ABA = {"dermo": 6, "skincare": 4, "make": 3, "perfume": 3, "cabelo": 2, "pijama": 0, "bolsas": 0, "sapatos": 0}
SITE_MAX, SITE_MAX_MODA, SITE_MAX_FOTO_PEQUENA = 300, 66, 24
SITE_MARCA_MAX, SITE_TIPO_MAX = 4, 8
_FOTO_SITE_ARQ = config.DADOS / "achadinhos" / "foto_site_px.json"
FOTO_SITE_MIN = 280  # abaixo disso a foto fica borrada mesmo num card pequeno → a oferta nem entra


def no_nicho_site(o: dict) -> bool:
    """Nicho do site = o do grupo: beleza (make, skincare, dermo), cabelo, perfume, bolsa/joia/óculos/sapato feminino e
    pijama de seda. Casa, eletrônico, infantil, esporte, masculino, ferramenta e alimento ficam de fora."""
    t, g = o.get("titulo") or "", o.get("grupo")
    if FORA_SITE.search(t) or fora_do_perfil(t) or eh_masculino(o) or preco_proibido(o):  # 06/10: Vivara sem preço
        return False
    m = FORA_FEMININO.search(t)
    # vitamina C em sérum/creme é skincare (o filtro do grupo só queria barrar suplemento)
    if m and not (m.group(0).lower() == "vitamina" and re.search(r"(?i)s[ée]rum|facial|creme|ampola|booster|pele|rosto", t)):
        return False
    if g in LINHA_PRINCIPAL:
        return True
    if g == "moda":
        return bool(MODA_FEMININA.search(t) or (SAPATO_FEM.search(t) and SAPATO_DE_MULHER.search(t))) \
            and not MODA_FORA.search(t) and (tipo_moda(t) != "acessorio" or not ROUPA_RX.search(t))
    return False


def aba_site(o: dict) -> str:
    """Aba do site: make · skincare · dermo · perfume · cabelo · bolsas · sapatos · pijama."""
    t, g = o.get("titulo") or "", o.get("grupo")
    if g == "moda":
        if PIJAMA_SEDA.search(t):
            return "pijama"
        # 08/10 (Rita): "Corrente De Tênis"/"Pulseiras De Tênis" (joia tipo tênis) iam para Sapatos
        # (joia ANTES do calçado no título; "Sandália Rasteira com Pingente" continua sapato)
        sap, joia = SAPATO_FEM.search(t), JOIA_RX.search(t)
        if sap and not BOLSA.search(t) and not (joia and joia.start() < sap.start()):
            return "sapatos"
        return "bolsas"
    if kit_capilar(t):  # 08/10 (Rita): kit máscara + perfume capilar é de cabelo
        return "cabelo"
    if g == "perfume" or PERFUME_RX.search(t):
        return "perfume"
    if CABELO_RX.search(t) or g == "cabelo":
        return "cabelo"
    if DERMO.search(t):
        return "dermo"
    if LIMPEZA_RX.search(t):
        return "skincare"
    return "make" if MAKE_RX.search(t) else "skincare"


def marca_site(titulo: str) -> str | None:
    m = MARCAS.search(titulo) or MARCAS_SKINCARE.search(titulo) or MARCA_EXTRA.search(titulo)
    return re.sub(r"\W+", "", m.group(0).lower()) if m else None


def tipo_site(o: dict) -> str | None:
    """Tipo que não pode lotar o site: os do grupo (TIPO_BELEZA/TIPO_DIA) + joia (brinco, colar, pulseira, anel…)."""
    t = o.get("titulo") or ""
    return tipo_repetivel(o) or next((nome for rx, nome in JOIA_TIPO if re.search(rx, t, re.I)), None)


def _tokens_de_produto(titulo: str) -> set[str]:
    """Palavras que IDENTIFICAM o produto: sem tamanho, número, cor ou palavra de vitrine (para achar o mesmo produto em
    outra loja/anúncio com o título reescrito)."""
    ruido = {"feminino", "feminina", "mulher", "mulheres", "profissional", "premium", "original", "kit", "conjunto",
             "unidades", "unidade", "mini", "rosa", "preto", "preta", "branco", "nude", "dourado", "prata", "novo", "nova",
             "promocao", "oferta", "tamanho", "frete", "gratis", "pronta", "entrega", "importado", "cores", "varias"}
    return {p for p in _tokens_produto(titulo) if p not in ruido and not re.fullmatch(r"\d+(?:x\d+)?[a-z]{0,3}", p)}


def _mesmo_produto(a_: set[str], b_: set[str]) -> bool:
    inter = len(a_ & b_)
    if not inter:
        return False
    return inter / len(a_ | b_) >= 0.65 or (min(len(a_), len(b_)) >= 3 and inter / min(len(a_), len(b_)) >= 0.85)


def _dimensoes_imagem(b: bytes) -> tuple[int, int] | None:
    """(largura, altura) lendo só o cabeçalho de PNG/JPEG/WebP — sem Pillow (o radar da nuvem só tem o httpx)."""
    import struct
    if b[:8] == b"\x89PNG\r\n\x1a\n" and len(b) >= 24:
        return struct.unpack(">II", b[16:24])
    if b[:2] == b"\xff\xd8":
        i = 2
        while i + 9 < len(b):
            if b[i] != 0xFF:
                i += 1
                continue
            m = b[i + 1]
            if m == 0xFF or m in (0xD8, 0x01) or 0xD0 <= m <= 0xD7:
                i += 1 if m == 0xFF else 2
                continue
            if 0xC0 <= m <= 0xCF and m not in (0xC4, 0xC8, 0xCC):
                h, w = struct.unpack(">HH", b[i + 5:i + 9])
                return w, h
            i += 2 + struct.unpack(">H", b[i + 2:i + 4])[0]
        return None
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        k = b[12:16]
        if k == b"VP8 " and len(b) >= 30:
            w, h = struct.unpack("<HH", b[26:30])
            return w & 0x3FFF, h & 0x3FFF
        if k == b"VP8L" and len(b) >= 25:
            bits = int.from_bytes(b[21:25], "little")
            return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
        if k == b"VP8X" and len(b) >= 30:
            return 1 + int.from_bytes(b[24:27], "little"), 1 + int.from_bytes(b[27:30], "little")
    return None


def _foto_maior(url: str) -> str:
    """Versão grande da MESMA foto (Promobit '/400/' → original; ML '-AB.webp' 448 px → '-F.webp' ~1200 px)."""
    if "i.promobit.com.br/400/" in url:
        return url.replace("/400/", "/", 1)
    return re.sub(r"(mlstatic\.com/)D_Q_NP_2X_(.+)-AB\.webp$", r"\1D_NQ_NP_2X_\2-F.webp", url)


def px_fotos(urls) -> dict[str, int | None]:
    """Maior lado, em px, de cada foto (0 = não abre / 404; None = não deu para saber agora, ex.: sem internet).
    Lê só os primeiros 64 KB; cache em dados/achadinhos/foto_site_px.json (a nuvem guarda no cache do Actions)."""
    urls = [u for u in dict.fromkeys(urls) if u and u.startswith("https://")]
    try:
        cache = json.loads(_FOTO_SITE_ARQ.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        cache = {}
    falta = [u for u in urls if u not in cache]
    if falta:
        from concurrent.futures import ThreadPoolExecutor

        def medir(cli, u):
            try:
                r = cli.get(u, headers={"Range": "bytes=0-65535"})
                if r.status_code in (403, 404, 410):
                    return 0
                if r.status_code not in (200, 206):
                    return None
                d = _dimensoes_imagem(r.content)
                if not d and r.status_code == 206:  # cabeçalho além dos 64 KB (JPEG com muito EXIF)
                    d = _dimensoes_imagem(cli.get(u).content)
                return max(d) if d else None
            except Exception:  # noqa: BLE001 — sem rede: fica "não sei", não "ruim"
                return None
        with httpx.Client(timeout=12, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"}) as cli, \
                ThreadPoolExecutor(12) as ex:
            for u, px in zip(falta, ex.map(lambda x: medir(cli, x), falta)):
                if px is not None:
                    cache[u] = px
        _FOTO_SITE_ARQ.parent.mkdir(parents=True, exist_ok=True)
        if len(cache) > 6000:  # o mais novo fica (dict mantém a ordem de entrada)
            cache = dict(list(cache.items())[-4500:])
        _FOTO_SITE_ARQ.write_text(json.dumps(cache), encoding="utf-8")
    return {u: cache.get(u) for u in urls}


def _escolher_foto(o: dict, px: dict) -> int | None:
    """Põe em o["foto"] a melhor versão da foto e devolve os px dela (None = não sei). Foto pequena da Promobit tenta a do
    mesmo anúncio no ML ou a da página da loja (og:image) antes de desistir."""
    f = o.get("foto") or ""
    achadas = [(px.get(u), u) for u in dict.fromkeys((_foto_maior(f), f)) if u]
    ok = next((x for x in achadas if x[0] and x[0] >= FOTO_MIN_PX), None)
    if ok:
        o["foto"] = ok[1]
        return ok[0]
    melhor = max(achadas, key=lambda x: x[0] or 0) if achadas else (None, f)
    o["foto"] = melhor[1]
    return melhor[0]


def _trocar_foto_pequena(o: dict, tentativas: list) -> int | None:
    """Foto < 600 px: procura a grande (mesmo anúncio no ML; og:image da loja). Devolve px da nova ou None."""
    m = re.search(r"MLB-?(\d{6,})", o.get("link_loja") or "")
    novas = []
    if m:
        novas += [r["foto"] for r in db.consultar("SELECT foto FROM ofertas WHERE id = ? AND foto IS NOT NULL",
                                                  (f"mlaf:MLB{m.group(1)}",))]
    if LOJAS_FOTO.search(o.get("link_loja") or "") and len(tentativas) < 40:
        tentativas.append(1)
        alt = foto_da_loja(o["link_loja"])
        if alt:
            novas.append(alt)
    if not novas:
        return None
    px = px_fotos(novas)
    for u in novas:
        if (px.get(u) or 0) >= FOTO_MIN_PX:
            o["foto"] = u
            return px[u]
    return None


def selecionar_vitrine(horas: int = 36) -> list[dict]:
    """As ofertas do site, já na ordem da página: só nicho, 1 por produto (a melhor), foto boa, beleza/dermo na frente."""
    base = [o for o in melhores(horas, 20000) if o["link_loja"] and o.get("foto") and no_nicho_site(o) and not de_inflado(o)]
    try:
        base = [o for o in base if not acima_do_mercado(o)]
    except Exception:  # noqa: BLE001 — sem base de preços para comparar: segue
        pass
    px = px_fotos(u for o in base for u in (_foto_maior(o["foto"]), o["foto"]))
    tentativas: list = []
    for o in base:
        o["_px"] = _escolher_foto(o, px)
        if o["_px"] is not None and o["_px"] < FOTO_MIN_PX and "promobit" in o["foto"]:
            o["_px"] = _trocar_foto_pequena(o, tentativas) or o["_px"]
    base = [o for o in base if o["_px"] is None or o["_px"] >= FOTO_SITE_MIN]  # foto borrada/quebrada: fora
    for o in base:
        o["_aba"] = aba_site(o)
        # rank = score do radar (desconto real, nota, vendidos, marca) + ajuste da aba; foto pequena desce
        o["_rank"] = (o.get("score") or 0) + BONUS_ABA[o["_aba"]] - (0 if o["_px"] is None or o["_px"] >= FOTO_MIN_PX else 40)
    base.sort(key=lambda o: -o["_rank"])
    sel, toks, marcas, tipos = [], [], {}, {}
    n_moda = n_peq = 0
    for o in base:
        if len(sel) >= SITE_MAX:
            break
        moda = o["grupo"] == "moda"
        pequena = o["_px"] is not None and o["_px"] < FOTO_MIN_PX
        marca, tipo, tk = marca_site(o["titulo"]), tipo_site(o), _tokens_de_produto(o["titulo"])
        teto_tipo = 20 if tipo == "bolsa" else SITE_TIPO_MAX
        if (moda and n_moda >= SITE_MAX_MODA) or (pequena and n_peq >= SITE_MAX_FOTO_PEQUENA):
            continue
        if marca and marcas.get(marca, 0) >= SITE_MARCA_MAX or tipo and tipos.get(tipo, 0) >= teto_tipo:
            continue
        if any(_mesmo_produto(tk, t2) for t2, a2 in toks if a2 == o["_aba"]):
            continue  # o mesmo produto (outra loja/anúncio): já entrou o melhor
        sel.append(o)
        toks.append((tk, o["_aba"]))
        n_moda += moda
        n_peq += pequena
        if marca:
            marcas[marca] = marcas.get(marca, 0) + 1
        if tipo:
            tipos[tipo] = tipos.get(tipo, 0) + 1
    # ordem da página: beleza/dermo/cabelo/perfume na frente (4 para cada 1 de bolsa/joia/sapato, para não virar só moda)
    bel = [o for o in sel if o["grupo"] != "moda"]
    mod = [o for o in sel if o["grupo"] == "moda"]
    out = []
    while bel or mod:
        out += bel[:4]
        bel = bel[4:]
        out += mod[:1]
        mod = mod[1:]
    return out


def vitrine(horas: int = 36) -> str:
    """Gera dados/achadinhos/site/index.html (celular primeiro). Só ofertas aprovadas, com link, do nosso nicho."""
    ofs = selecionar_vitrine(horas)
    abas_ok = [(k, n) for k, n in ABAS_SITE if any(o["_aba"] == k for o in ofs)]
    e = _html.escape
    dados = [{"g": o["grupo"], "k": o["_aba"], "l": o.get("loja") or "", "t": cortar(limpar_titulo(o["titulo"]), 90),
              "p": _brl(o["preco"]), "a": _brl(o["preco_antigo"]) if o.get("preco_antigo") else "",
              "d": o.get("desconto") or 0, "c": o.get("cupom") or "", "f": o.get("foto") or "",
              "u": link_afiliado(o["link_loja"]) or ""} for o in ofs]
    mins = minimos()
    for o, x in zip(ofs, dados):  # selo "menor preço em N dias" (só quando vale: 7+ dias e abaixo do menor anterior)
        if o["id"] in mins and o["preco"] < mins[o["id"]][1]:
            x["h"] = mins[o["id"]][0]
    (SITE).mkdir(parents=True, exist_ok=True)
    (SITE / "ofertas.json").write_text(json.dumps(dados, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    # a página desenha os cards a partir de ofertas.json (60 por vez); só abas do nicho e só as que têm oferta
    abas = "".join(f'<button data-g="{k}">{n} <i>{sum(1 for o in ofs if o["_aba"] == k)}</i></button>' for k, n in abas_ok)
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
    pagina = f"""<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'self'; img-src 'self' https: data:; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; script-src 'self' 'unsafe-inline'; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'"><meta name="referrer" content="strict-origin-when-cross-origin">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Achadinhos Bella Lucce: promoções de beleza hoje (maquiagem, skincare, dermo, perfume)</title>
<link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:wght@500;600;700&family=Josefin+Sans:wght@400;600;700&display=swap" rel="stylesheet">
<link rel="icon" href="favicon.png"><meta property="og:image" content="https://bellalucce.github.io/logo.png">
<meta name="lomadee" content="2324685">
<meta name="google-site-verification" content="_jXfCl5-PGQIyG-U4Y0IMq7SxGHMbmxN6cjvbJZOsJ4">
<link rel="canonical" href="https://bellalucce.github.io/">
<meta property="og:title" content="Achadinhos Bella Lucce ✨"><meta property="og:type" content="website">
<meta property="og:description" content="Promoções de maquiagem, skincare, dermo, perfume e cabelo com desconto de verdade.">
<meta name="description" content="Achadinhos e promoções de beleza de hoje: maquiagem, skincare, dermocosméticos, perfume e cabelo da Shopee, Amazon, Mercado Livre e mais. Entre no grupo de achadinhos grátis no WhatsApp.">
<style>
:root{{--cor:#B9683C;--cor2:#8E4A26;--fundo:#F4E8D2;--claro:#FBF4E6;--txt:#4A2C1A;--linha:#E2CBA6}}*{{box-sizing:border-box}}body{{margin:0;font-family:'Josefin Sans',system-ui,Segoe UI,Arial,sans-serif;background:var(--fundo);color:var(--txt)}}
header{{background:var(--fundo);color:var(--cor2);padding:22px 16px 18px;text-align:center;border-bottom:3px double var(--cor)}}header .sobre{{font-size:11px;letter-spacing:.25em;font-weight:700;color:var(--cor);margin:0 0 2px}}header h1{{margin:0;font-size:34px;font-family:'Cormorant Garamond',Georgia,serif;font-weight:700;line-height:1.05}}header p{{margin:8px 0 0;font-size:13px;color:#7A5A44}}
nav{{display:flex;gap:8px;overflow-x:auto;padding:12px 16px;position:sticky;top:0;background:var(--fundo);z-index:2}}
nav button{{border:1px solid var(--cor);background:var(--claro);color:var(--cor2);border-radius:20px;padding:8px 14px 6px;font-weight:600;white-space:nowrap;font-family:inherit}}
nav button.on{{background:var(--cor);color:#fff}}
main{{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:12px;padding:0 16px 30px}}
.card{{background:#fff;border-radius:14px;overflow:hidden;text-decoration:none;color:inherit;box-shadow:0 1px 4px #4a2c1a1a;border:1px solid var(--linha);display:flex;flex-direction:column}}
.img{{position:relative;aspect-ratio:1;background:#fff;padding:10px}}.img img{{display:block;width:100%;height:100%;object-fit:contain;background:#fff}}
nav button i{{font-style:normal;font-size:11px;opacity:.7;margin-left:3px}}
.tit{{display:-webkit-box;-webkit-line-clamp:2;line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;min-height:2.6em}}
.selo{{position:absolute;top:8px;left:8px;background:var(--cor);color:#fff;font-weight:700;font-size:13px;padding:3px 8px;border-radius:10px}}
.loja{{font-size:11px;color:#8A7A66;padding:8px 10px 0}}.tit{{font-size:13px;padding:4px 10px;line-height:1.3;flex:1}}
.preco{{padding:0 10px;font-size:17px;font-weight:700}}.preco s{{color:#8A7A66;font-size:12px;font-weight:400;margin-right:6px}}.preco b{{color:#3F6B38}}
.hist{{margin:4px 10px 0;font-size:12px;font-weight:600;color:#4E7A45}}
.cupom{{margin:6px 10px 0;font-size:12px;background:#FBF3E6;border:1px dashed var(--cor);border-radius:8px;padding:4px 6px}}
.btn{{margin:10px;background:var(--cor);color:#fff;text-align:center;border-radius:10px;padding:9px;font-weight:700}}
.lnk{{display:flex;flex-direction:column;flex:1;text-decoration:none;color:inherit}}
.share{{margin:-4px 10px 10px;text-align:center;font-size:12px;font-weight:600;color:#1f9d55;text-decoration:none}}
footer{{text-align:center;font-size:11px;color:#999;padding:0 16px 24px}}
.logo{{width:72px;height:72px;border-radius:50%;border:1px solid var(--linha);display:block;margin:0 auto 8px}}
.busca{{display:flex;gap:10px;align-items:center;padding:0 16px 12px}}.busca input{{flex:1;border:1px solid var(--linha);border-radius:20px;padding:9px 14px;font-size:14px;background:var(--claro);font-family:inherit}}.busca span{{font-size:12px;color:#8A7A66;white-space:nowrap}}
#mais{{display:block;margin:0 auto 24px;border:0;background:var(--cor);color:#fff;font-weight:700;border-radius:22px;padding:12px 22px;font-size:15px}}
.entrar{{display:block;margin:12px auto 0;max-width:420px;background:#25d366;color:#fff;text-decoration:none;font-weight:700;border-radius:24px;padding:11px 16px}}
.oce{{display:block;margin:10px auto 0;max-width:420px;background:#fff;color:var(--cor2);text-decoration:none;font-weight:700;border:2px dashed var(--cor);border-radius:24px;padding:10px 16px}}
.nossa{{padding:12px 16px 0}}.nossa h2{{font-size:24px;margin:4px 0 10px;color:var(--cor2);font-family:'Cormorant Garamond',Georgia,serif;font-weight:700}}
.trilho{{display:grid;grid-auto-flow:column;grid-auto-columns:minmax(150px,170px);gap:12px;overflow-x:auto;padding-bottom:8px}}
</style></head><body>
<header><img class="logo" src="logo.png" alt="bella lucce"><p class="sobre">BELLA LUCCE</p><h1>Achadinhos de Beleza</h1><p>Make, skincare, dermo, perfume, cabelo, bolsas e acessórios com desconto de verdade · atualizado {agora}</p>{entrar}<a class="oce" href="oceane/">🎟️ Área Océane · cupom BELLALUCCE 10% OFF</a></header>
{faixa}
<nav><button class="on" data-g="">Tudo</button>{abas}</nav>
<div class="busca"><input id="q" type="search" placeholder="Buscar oferta (ex.: sérum, perfume, chapinha)"><span id="n"></span></div>
<main id="lista"><p>Carregando ofertas…</p></main>
<button id="mais">Carregar mais ofertas</button>
<p style="text-align:center;margin:0 0 14px"><a href="https://www.instagram.com/abella.lucce/" target="_blank" rel="noopener" style="color:var(--cor);font-weight:700;text-decoration:none">✨ Siga a gente no Instagram: @abella.lucce</a></p>
<footer>#publi · Preços e cupons podem mudar a qualquer momento (conferidos na data da oferta). Links de afiliado: a loja pode nos pagar uma comissão, sem custo para você. Como Associado da Amazon, a Bella Lucce recebe por compras qualificadas.</footer>
<script>
let T=[],F=[],N=0,G='',Q='';const P=60,E=s=>String(s).replace(/[&<>"]/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}})[c]);
const semAf=u=>u;  // 07/10: a dona aceitou o risco do link de afiliado do ML no WhatsApp (ML_AFILIADO_NO_WHATSAPP)
const zap=o=>'https://wa.me/?text='+encodeURIComponent(o.t+' por '+o.p+' 👉 '+semAf(o.u)+'\\n\\nMais achadinhos: {SITE_URL}');
const card=(o,i)=>`<div class="card"><a class="lnk" href="${{E(o.u)}}" target="_blank" rel="nofollow sponsored noopener"><div class="img">${{o.f?`<img loading="${{i<8?'eager':'lazy'}}" decoding="async" width="300" height="300" src="${{E(o.f)}}" alt="${{E(o.t)}}" onerror="this.style.visibility='hidden'">`:''}}${{o.d?`<span class="selo">-${{o.d}}%</span>`:''}}</div><div class="loja">${{E(o.l)}}</div><div class="tit">${{E(o.t)}}</div><div class="preco">${{o.a?`<s>${{o.a}}</s>`:''}}<b>${{o.p}}</b></div>${{o.h?`<div class="hist">📉 menor preço em ${{o.h}} dias</div>`:''}}${{o.c?`<div class="cupom">Cupom: <b>${{E(o.c)}}</b></div>`:''}}<div class="btn">Pegar oferta</div></a><a class="share" href="${{E(zap(o))}}" target="_blank" rel="noopener">Compartilhar no WhatsApp</a></div>`;
const filtrar=()=>{{const q=Q.normalize('NFD').replace(/[\\u0300-\\u036f]/g,'').toLowerCase();F=T.filter(o=>(!G||o.k===G)&&(!q||o.t.normalize('NFD').replace(/[\\u0300-\\u036f]/g,'').toLowerCase().includes(q)));N=0;document.getElementById('lista').innerHTML='';mais();document.getElementById('n').textContent=F.length+' ofertas'}};
const mais=()=>{{document.getElementById('lista').insertAdjacentHTML('beforeend',F.slice(N,N+P).map((o,j)=>card(o,N+j)).join('')||(N?'':'<p>Nenhuma oferta aqui agora.</p>'));N+=P;document.getElementById('mais').style.display=N<F.length?'':'none'}};
document.getElementById('mais').onclick=mais;document.getElementById('q').oninput=e=>{{Q=e.target.value;filtrar()}};
document.querySelectorAll('nav button').forEach(b=>b.onclick=()=>{{document.querySelectorAll('nav button').forEach(x=>x.classList.toggle('on',x===b));G=b.dataset.g;filtrar()}});
fetch('ofertas.json?v='+Date.now()).then(r=>r.json()).then(d=>{{T=d;filtrar()}});
</script>
</body></html>"""
    SITE.mkdir(parents=True, exist_ok=True)
    (SITE / "index.html").write_text(pagina, encoding="utf-8")
    try:  # 09/10: área só da Océane (/oceane/) — erro nela não pode derrubar a vitrine
        pagina_oceane()
    except Exception as ex:  # noqa: BLE001
        print("área Océane falhou:", ex)
    return str(SITE / "index.html")


# ---------------- área Océane do site (/oceane/) ----------------
# 09/10 (dona: "no site a gente podia criar uma área só da Océane, porque agora eu tenho um cupom"): página só com as
# ofertas da Océane que estão valendo (fonte oceane_afiliados, preço OFICIAL visto há até OCEANE_VALIDADE_H h), Por
# ≤ R$ 90 primeiro (pedido da dona), De/Por da própria loja e o NOSSO link (utmi_pc=BELLALUCCE); no topo o cupom
# BELLALUCCE (10% OFF) com botão de copiar. Fora: Mariana Saad (fora do programa e do robots.txt), validade próxima e
# a linha infantil/teen 4YOU. Regras da marca: sem promessa de resultado, nunca em mídia paga, preço só o oficial.
OCEANE_CUPOM_SITE = "BELLALUCCE"
OCEANE_PRIORIDADE_SITE = 90.0  # = integracoes/oceane.PRECO_PRIORIDADE (a nuvem não tem o pacote integracoes)
OCEANE_FORA_SITE = re.compile(r"(?i)4\s*-?\s*you\b|\bsaad\b|mariana[\s-]*saad|infantil|\bkids?\b|crian[çc]as?|\bbaby\b|"
                              r"\bbeb[êe]s?\b|validade|vencimento")
OCEANE_JS = """<script>
document.querySelectorAll('[data-copiar]').forEach(function (b) {
  b.onclick = function () {
    var c = b.getAttribute('data-copiar'), ok = function () { b.textContent = 'Cupom copiado ✓'; };
    var velho = function () {
      var t = document.createElement('textarea'); t.value = c; document.body.appendChild(t); t.select();
      try { document.execCommand('copy'); ok(); } catch (e) { b.textContent = c; }
      t.remove();
    };
    if (navigator.clipboard) { navigator.clipboard.writeText(c).then(ok, velho); } else { velho(); }
  };
});
</script>"""


def ofertas_oceane(agora: datetime | None = None) -> list[dict]:
    """Ofertas da Océane valendo AGORA para a área do site, já na ordem: Por ≤ R$ 90 primeiro, depois maior desconto."""
    _tabela()
    agora = agora or datetime.now()
    limite = (agora - timedelta(hours=OCEANE_VALIDADE_H)).isoformat(sep=" ", timespec="seconds")
    out = []
    for o in db.consultar("SELECT * FROM ofertas WHERE fonte = 'oceane_afiliados' AND aprovada = 1 "
                          "AND link_loja IS NOT NULL AND atualizado_em >= ?", (limite,)):
        s = sinais_de(o)
        txt = " ".join(str(x or "") for x in (o.get("titulo"), o.get("link_loja"), o.get("foto"), s.get("nome_loja"),
                                              s.get("categoria")))
        if s.get("fora") or OCEANE_FORA_SITE.search(txt) or validade_na_foto(o) or vencendo(o.get("titulo") or ""):
            continue
        por, de = o.get("preco") or 0, o.get("preco_antigo") or 0
        link = link_afiliado(o["link_loja"]) or ""
        if not (por > 0 and de > por and o.get("foto") and f"utmi_pc={OCEANE_UTMI}" in link):
            continue
        out.append({**o, "_link": link})
    out.sort(key=lambda o: (o["preco"] > OCEANE_PRIORIDADE_SITE, -(o.get("desconto") or 0), o["preco"]))
    return out


def pagina_oceane(agora: datetime | None = None) -> str:
    """Gera SITE/oceane/index.html (a nuvem copia para /oceane/). Sem oferta valendo, a página fica só com o cupom."""
    agora = agora or datetime.now()
    ofs = ofertas_oceane(agora)
    e = _html.escape

    def card(o: dict) -> str:
        t = cortar(limpar_titulo(o["titulo"]), 90)
        selo = f'<span class="selo">-{int(o["desconto"])}%</span>' if o.get("desconto") else ""
        return (f'<a class="card" href="{e(o["_link"])}" target="_blank" rel="nofollow sponsored noopener">'
                f'<div class="img"><img loading="lazy" decoding="async" width="300" height="300" src="{e(o["foto"])}" '
                f'alt="{e(t)}">{selo}</div><div class="loja">Océane · loja oficial</div><div class="tit">{e(t)}</div>'
                f'<div class="preco"><s>De {_brl(o["preco_antigo"])}</s><b>Por {_brl(o["preco"])}</b></div>'
                f'<div class="btn">Ver na Océane</div></a>')

    ate = [o for o in ofs if o["preco"] <= OCEANE_PRIORIDADE_SITE]
    acima = [o for o in ofs if o["preco"] > OCEANE_PRIORIDADE_SITE]
    blocos = ""
    if ate:
        blocos += f'<h2>Até R$ 90 <i>{len(ate)}</i></h2><main>{"".join(card(o) for o in ate)}</main>'
    if acima:
        blocos += f'<h2>Mais ofertas da Océane <i>{len(acima)}</i></h2><main>{"".join(card(o) for o in acima)}</main>'
    if not ofs:
        blocos = ('<p class="vazio">As ofertas da Océane entram aqui toda manhã, com o preço da loja oficial. '
                  'O cupom já vale no site da Océane 👇</p>')
    visto = max((str(o.get("atualizado_em") or "") for o in ofs), default="")
    conferido = (f" · preços da loja conferidos às {visto[11:16]} de {visto[8:10]}/{visto[5:7]}" if len(visto) >= 16
                 else "")
    loja = f"https://www.oceane.com.br/?utmi_pc={OCEANE_UTMI}"
    pagina = f"""<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'self'; img-src 'self' https: data:; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; script-src 'self' 'unsafe-inline'; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'"><meta name="referrer" content="strict-origin-when-cross-origin">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Área Océane · cupom BELLALUCCE 10% OFF · Bella Lucce</title>
<link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:wght@500;600;700&family=Josefin+Sans:wght@400;600;700&display=swap" rel="stylesheet">
<link rel="icon" href="../favicon.png"><meta property="og:image" content="https://bellalucce.github.io/logo.png">
<link rel="canonical" href="{SITE_URL}/oceane/">
<meta property="og:title" content="Área Océane · cupom BELLALUCCE 10% OFF"><meta property="og:type" content="website">
<meta name="description" content="Ofertas da loja oficial da Océane (maquiagem, skincare e cabelo) e o cupom BELLALUCCE: 10% OFF no site da Océane.">
<style>
:root{{--cor:#B9683C;--cor2:#8E4A26;--fundo:#F4E8D2;--claro:#FBF4E6;--txt:#4A2C1A;--linha:#E2CBA6}}*{{box-sizing:border-box}}body{{margin:0;font-family:'Josefin Sans',system-ui,Segoe UI,Arial,sans-serif;background:var(--fundo);color:var(--txt)}}
.voltar{{display:inline-block;margin:12px 16px 0;color:var(--cor2);font-weight:600;text-decoration:none;font-size:14px}}
header{{padding:10px 16px 18px;text-align:center;border-bottom:3px double var(--cor)}}header .sobre{{font-size:11px;letter-spacing:.25em;font-weight:700;color:var(--cor);margin:0 0 2px}}header h1{{margin:0;font-size:36px;font-family:'Cormorant Garamond',Georgia,serif;font-weight:700;color:var(--cor2);line-height:1.05}}header p{{margin:8px 0 0;font-size:13px;color:#7A5A44}}
.logo{{width:72px;height:72px;border-radius:50%;border:1px solid var(--linha);display:block;margin:0 auto 8px}}
.cupom{{max-width:460px;margin:16px auto 4px;background:#fff;border:2px dashed var(--cor);border-radius:16px;padding:16px;text-align:center}}
.cupom b{{display:block;font-family:'Cormorant Garamond',Georgia,serif;font-size:26px;color:var(--cor2);line-height:1.15}}.cupom small{{display:block;font-size:12px;color:#7A5A44;margin-top:6px}}
.cupom button{{margin-top:12px;border:0;background:var(--cor);color:#fff;font-weight:700;border-radius:22px;padding:11px 22px;font-size:15px;font-family:inherit;cursor:pointer}}
.cupom a{{display:inline-block;margin:10px 0 0 8px;color:var(--cor2);font-weight:600;font-size:14px}}
h2{{font-family:'Cormorant Garamond',Georgia,serif;font-size:26px;color:var(--cor2);margin:22px 16px 10px}}h2 i{{font-family:'Josefin Sans',sans-serif;font-style:normal;font-size:13px;opacity:.7}}
main{{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:12px;padding:0 16px 10px}}
.card{{background:#fff;border-radius:14px;overflow:hidden;text-decoration:none;color:inherit;box-shadow:0 1px 4px #4a2c1a1a;border:1px solid var(--linha);display:flex;flex-direction:column}}
.img{{position:relative;aspect-ratio:1;background:#fff;padding:10px}}.img img{{display:block;width:100%;height:100%;object-fit:contain;background:#fff}}
.selo{{position:absolute;top:8px;left:8px;background:var(--cor);color:#fff;font-weight:700;font-size:13px;padding:3px 8px;border-radius:10px}}
.loja{{font-size:11px;color:#8A7A66;padding:8px 10px 0}}.tit{{font-size:13px;padding:4px 10px;line-height:1.3;flex:1;display:-webkit-box;-webkit-line-clamp:2;line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;min-height:2.6em}}
.preco{{padding:0 10px;font-size:16px;font-weight:700}}.preco s{{display:block;color:#8A7A66;font-size:12px;font-weight:400}}.preco b{{color:#3F6B38}}
.btn{{margin:10px;background:var(--cor);color:#fff;text-align:center;border-radius:10px;padding:9px;font-weight:700}}
.vazio{{text-align:center;padding:20px 16px;font-size:15px}}
footer{{text-align:center;font-size:11px;color:#999;padding:16px 16px 24px}}
</style></head><body>
<a class="voltar" href="../">← Todas as ofertas</a>
<header><img class="logo" src="../logo.png" alt="bella lucce"><p class="sobre">BELLA LUCCE</p><h1>Área Océane</h1><p>Maquiagem, skincare e cabelo da loja oficial da Océane · atualizado {agora:%d/%m %H:%M}{conferido}</p></header>
<section class="cupom"><b>Cupom {OCEANE_CUPOM_SITE}: 10% OFF no site da Océane</b><small>Copie e cole o cupom no carrinho do site da Océane.</small><button type="button" data-copiar="{OCEANE_CUPOM_SITE}">Copiar cupom</button><a href="{e(loja)}" target="_blank" rel="nofollow sponsored noopener">Ir para a Océane →</a></section>
{blocos}
<footer>#publi · Links de afiliada da Bella Lucce: a Océane pode nos pagar uma comissão, sem custo a mais para você. Preços da loja oficial; podem mudar a qualquer momento — confira no site antes de comprar.</footer>
{OCEANE_JS}
</body></html>"""
    pasta = SITE / "oceane"
    pasta.mkdir(parents=True, exist_ok=True)
    (pasta / "index.html").write_text(pagina, encoding="utf-8")
    return str(pasta / "index.html")


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
     r"[óo]leo de limpeza|"  # "Creamy Skincare" = marca
     # 07/10 (Beto): "Contorno dos Olhos Revitalift" (creme) saía "ROSTO MAIS DESENHADO NA MAKE" — é skincare
     r"contorno (?:d[oa]s? |de )?olhos|creme (?:para |de )?(?:a )?(?:[áa]rea d[oa]s )?olhos|eye cream|[áa]rea dos olhos",
     "PELE LISINHA E COM VIÇO, AMIGA ✨"),
    (r"base|corretivo|concealer|p[óo] compacto|primer|fixador de maquiagem|bb cream|cc cream|blush|iluminador|"
     r"contorno(?! (?:d[oa]s? |de )?olhos)|bronzer|cushion", "PELE DE FILTRO NA VIDA REAL 💄"),
    (r"paleta de sombras?|sombras?|delineador|l[áa]pis de olho|kajal", "OLHAR PODEROSO NO PRECINHO 👁️"),
    (r"gloss|batom|batons|lip ?tint|lip ?oil|lip ?balm|balm|hidratante labial|l[áa]bios|lips|tint|lip sleeping|"
     r"lip mask|m[áa]scara labial", "BOCA LINDA GASTANDO POUCO 💋"),
    (r"escova secadora|secador(?! de (?:lou[çc]a|pratos?))|secadora(?! de roupa)|escova rotativa|escova alisadora", "CABELO LINDO E SECO RAPIDINHO 💨"),
    (r"chapinha|prancha", "LISO PERFEITO EM MINUTOS ✨"),
    # 01/10 (Rita): "Modelador De Ondas" saiu "CACHOS DE SALÃO" — onda não é cacho
    (r"modelador(?:a)? de ondas|ondulador|modelador(?= ondas)|ondas perfeitas|babyliss de ondas",
     "CABELO COM ONDAS DE SALÃO 🌊"),
    (r"cachos|cacheador|cachead\w*|fitagem|babyliss|modelador de cachos|modelador(?= curves)",
     "CACHOS DE SALÃO EM CASA 🌀"),
    (r"m[áa]scara capilar|[óo]leo capilar|s[ée]rum capilar|t[ôo]nico capilar|capilar|antiqueda|couro cabeludo|scalp|hair|"
     # 01/10 (Mila): "ampola" sozinha pegava sérum de PELE (goodal retinol, IOPE) → só ampola capilar
     r"shampoo|condicionador|ampolas? capilar(?:es)?|leave-?in|elseve|"
     r"k[ée]rastase|wella|truss|lola cosmetics|salon line|pantene|tresemm[ée]|cadiveu|si[àa]ge|keune|braé|matrix",
     "CABELO MACIO DE SALÃO EM CASA 💆‍♀️"),
    (r"lat+af+a|armaf|al wataniah|maison alhambra|[áa]r[áa]be|asad|yara|fakhar|khamrah|club de nuit|french avenue|"
     r"al wesal|durrat al aroos|sabah al ward", "CHEIRO DE GRIFE, PREÇO DE ÁRABE 🔥"),
    (r"(?<!sem )perfume|eau de|parfum|body splash|splash|col[ôo]nia|deo col[ôo]nia|edp|edt", "CHEIROSA O DIA INTEIRO, AMIGA 🌸"),
    (r"legging|^top|top fitness|conjunto fitness|short fitness|cal[çc]a fitness|macaquinho|roupa de academia",
     "LOOK DE TREINO QUE VALORIZA TUDO 🍑"),
    # 01/10 (dona): "Tônico Capilar Yenzah Whey Amino" virou "PRA QUEM LEVA O TREINO A SÉRIO" — whey/colágeno em
    # cosmético (capilar, creme, facial…) é ingrediente, não suplemento
    (r"(?:whey|creatina|pr[ée]-?treino|col[áa]geno)(?!.*(?:capilar|cabelo|shampoo|condicionador|creme|facial|lo[çc][ãa]o|"
     r"s[ée]rum|sabonete|m[áa]scara))",
     "SUPLEMENTO BOM COM PREÇO DE AMIGA 💪"),
    (r"bicicleta ergom\w*|esteira", "ACADEMIA EM CASA NO PRECINHO 🚴‍♀️"),
    (r"jogo de cama|len[çc]ol|len[çc][óo]is|edredom|travesseiro|colcha", "CAMA DE HOTEL NA SUA CASA 🛏️"),
    # 01/10: "Curren Portas Relógios Dos Homens" (tradução ruim de "relógios masculinos") virou "ORGANIZE SUA
    # BAGUNÇA" → "portas" (plural) não é porta-objetos
    (r"organizador|caixa organizadora|cesto|porta(?!s\b)(?! beb[êe])|expositor|sacos? (?:de )?armazenamento|"
     r"saco a v[áa]cuo",
     "ORGANIZE SUA BAGUNÇA 🧺"),
    (r"toalha|toalh[ãa]o", "TOALHA FOFINHA DE HOTEL 🛁"),
    (r"panela(?! (?:de )?cera)|frigideira|air ?fryer|fritadeira|mixer|processador de alimentos|liquidificador|cafeteira|"
     r"sanduicheira|batedeira|potes? herm[ée]ticos?|formas? de silicone|travessas?", "COZINHA LINDA GASTANDO POUCO 🍳"),
    (r"fralda|len[çc]os? umedecidos?|beb[êe]|baby|porta beb[êe]", "MAMÃE, CORRE QUE TÁ BARATO 👶"),  # "canguru": moletom
    (r"bolsa(?:s)?(?! (?:de )?(?:t[ée]rmica|isot[ée]rmica|ferramentas?|maternidade|marmita|lancheira))",
     "A BOLSA QUE COMBINA COM TUDO 👜"),
    # 01/10: scarpin de salto alto saía "PÉ LINDO E CONFORTÁVEL 👟" (a variação do salto já tinha saído) → tipo próprio
    (r"scarpin|salto alto|salto fino|salto agulha|meia pata", "SALTO LINDO PRO LOOK 👠"),
    (r"t[êe]nis|sand[áa]lia|chinelo|tamanco|rasteira|pantufa|sapatilha|bota|mocassim", "PÉ LINDO E CONFORTÁVEL 👟"),
    (r"vivara|pandora|life by vivara", "JOIA DE MARCA COM DESCONTO 💎"),  # R$ 500 não é "sem gastar muito"
    (r"brinco|colar|colares|anel|an[ée]is|pulseira|rel[óo]gio|semijoia|conjunto de joias|alian[çc]a|pingente", "BRILHO NO LOOK SEM GASTAR MUITO ✨"),
    (r"cal[çc]a|vestido|blusa|saia|macac[ãa]o|cropped|pijama|suti[ãa]|calcinha|camiseta|camisa|blazer|moletom|jaqueta|"
     # 01/10 (Tati): "Body Gua Sha Bar" não é roupa; "Kit Meias… Sapatilha" é meia, não calçado
     r"casaco|regata|shorts?|bermuda|conjunto feminino|"
     r"body(?! splash| lotion| oil| bar| scrub| mist| butter| cream| wash| gua| spray| clarifying| pad|\s*-)|"
     r"cardig[ãa]|meia(?! pata)",
     "LOOK NOVO GASTANDO POUCO 👗"),
    (r"whiskas|pedigree|golden|premier|ra[çc][ãa]o|areia", "O PET AGRADECE E O BOLSO TAMBÉM 🐾"),
    (r"creme hidratante|hidratante corporal|lo[çc][ãa]o hidratante|lo[çc][ãa]o corporal|body lotion|[óo]leo corporal|"
     r"bio oil|manteiga corporal|hidratante desodorante", "PELE MACIA O DIA INTEIRO 🧴"),
    # 30/09 (dona: "além de correto, tem que ser humano"): tipos que ficavam com frase genérica agora têm a sua
    (r"mai[ôo]|biqu[íi]ni|sa[íi]da de praia|canga", "PRONTA PRO VERÃO 👙"),
    (r"brinquedo|boneca|pel[úu]cia|carrinho de controle|carrinhos|pista|caminh[ãa]o (?:de )?controle|patinete|lego|blocos de montar|"
     r"quebra-?cabe[çc]a|massinha|maquiagem (?:infantil|crian[çc]a)|reborn|hama beads", "PRESENTE PROS PEQUENOS 🧸"),
    (r"roupa infantil|conjunto infantil|menin[oa]|(?:camisa|camiseta|blusa|vestido|short|bermuda|cal[çc]a|pijama|"
     r"jaqueta|moletom|macac[ãa]o)s? infant(?:il|is)", "ROUPINHA FOFA PROS PEQUENOS 🧸"),  # 01/10: "Camisa Infantil"
    (r"garrafa t[ée]rmica|copo t[ée]rmico|caneca t[ée]rmica|squeeze|tumbler|stanley", "GELADINHO OU QUENTINHO O DIA TODO 🧊"),
    (r"massageador|gua sha", "ALÍVIO PRO CORPO CANSADO 💆‍♀️"),
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
             TREINO, "PÉ LINDO E CONFORTÁVEL 👟", "SALTO LINDO PRO LOOK 👠", "A BOLSA QUE COMBINA COM TUDO 👜"}
BENEFICIOS_BELEZA = 12  # as 12 primeiras frases são de beleza/cabelo/perfume
# 30/09: frases variadas e por característica moram em vendas/ganchos.py (repertório próprio, sem repetir)
SEGREDO = "AMIGAS NÃO GUARDAM SEGREDO! 🤫"  # frase de DICA de cuidado (retinol, protetor, tratamento) — dona, 30/09
DICAS = ("PELE PROTEGIDA", "CÍLIOS", "PELE LISINHA", "CABELO MACIO")
# "peças" fora: "Jogo de Lençol 2 Peças"/"Conjunto de Panelas 10 Peças" são partes de UM jogo, não itens → "SÓ R$ X CADA" enganava
KIT = re.compile(r"\b(?:kit|combo|pack)\s*(?:com\s*|c/\s*)?(\d{1,3})\b|\b(\d{1,3})\s*(?:pares|unidades|un\b|rolos)", re.I)
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


# 06/10 noite (Rita): perfume "Una Blush" saiu com "BOCHECHA CORADINHA" — o tipo vem da CATEGORIA da oferta (perfume) e
# do que o título diz que o produto É (Deo Parfum, EDP…), não de uma palavra do nome. Make nunca em perfume e vice-versa.
FRASES_MAKE = {"PELE DE FILTRO NA VIDA REAL 💄", "OLHAR PODEROSO NO PRECINHO 👁️", "BOCA LINDA GASTANDO POUCO 💋",
               "CÍLIOS DE BONECA POR ESSE PREÇO? 👀"}
GANCHO_MAKE = re.compile(r"BOCHECHA|CORADINH|BLUSH|\bBOCA\b|BATO[MN]|💋|💄|OLHAR|C[ÍI]LIOS|PELE DE FILTRO|\bMAKE\b|"
                         r"L[ÁA]BIO|GLOSS|DELINEAD|SOMBRA|ILUMINAD|BRILHO DE PELE|PELE UNIFORME|ACABAMENTO", re.I)
GANCHO_PERFUME = re.compile(r"CHEIR|PERFUM|[ÁA]RABE|FRAGR|FIXA DE VERDADE", re.I)
PERFUME_E = re.compile(r"(?i)(?<!sem )perfume\b|eau de (?:parfum|toilette|cologne)|\bparfum\b|deo ?col[ôo]nia|"
                       r"\bcol[ôo]nia\b|body splash|body mist|\bedp\b|\bedt\b|perfume [áa]rabe")


def eh_perfume(titulo: str, grupo: str | None) -> bool:
    return (grupo == "perfume" or bool(PERFUME_E.search(titulo or ""))) and not kit_capilar(titulo)


# 08/10 (Rita): "Primer Antifrizz Plástica dos Fios - Cadiveu" (cabelo) saiu "A MAKE DURA MUITO MAIS"; "Kit Máscara
# Lamelar e Perfume Capilar Forever Liss" saiu "CHEIROSA O DIA INTEIRO" → a palavra solta (primer, perfume) não decide o
# tipo quando o título é de CABELO; make nunca em cabelo, e kit de tratamento capilar não ganha frase de perfume
CABELO_CONTEXTO = re.compile(r"(?i)capilar|cabelos?\b|\bfios\b|anti-?frizz|\bfrizz|shampoo|condicionador|leave-?in|"
                             r"progressiva|selagem|cronograma|\bhair\b|couro cabeludo|cadiveu|k[ée]rastase|forever liss|"
                             r"\bwella\b|\btruss\b")
MAKE_DE_VERDADE = re.compile(r"(?i)batom|gloss|\bbase\b|corretivo|r[íi]mel|c[íi]lios|sombra|paleta|blush|iluminador|"
                             r"delineador|maquiagem|\bmake\b|\blip\b|labial|p[óo] (?:compacto|solto|facial)")
CUIDADO_CAPILAR = re.compile(r"(?i)m[áa]scara|shampoo|condicionador|leave-?in|\b[óo]leos?\b|ampola|creme de pentear|tratamento|"
                             r"hidrata[çc][ãa]o|nutri[çc][ãa]o|reconstru|lamelar|s[ée]rum")
SKINCARE_OLHOS = re.compile(r"(?i)contorno (?:d[oa]s? |de )?olhos|eye cream|[áa]rea dos olhos|creme (?:para |de )?olhos")
FRASES_PERFUME = {"CHEIROSA O DIA INTEIRO, AMIGA 🌸", "CHEIRO DE GRIFE, PREÇO DE ÁRABE 🔥"}


def eh_cabelo(titulo: str, grupo: str | None) -> bool:
    """Produto de cabelo (categoria ou o título fala de fios/capilar) sem ser maquiagem de verdade."""
    t = titulo or ""
    return (grupo == "cabelo" or bool(CABELO_CONTEXTO.search(t))) and not MAKE_DE_VERDADE.search(t)


def kit_capilar(titulo: str) -> bool:
    """Perfume capilar/hair mist JUNTO de tratamento (máscara, shampoo…): o produto é de cabelo, não perfume."""
    t = titulo or ""
    return bool(re.search(r"(?i)perfume capilar|perfume (?:para|pra) (?:o )?cabelo|hair mist|hair perfume", t)) and bool(
        CUIDADO_CAPILAR.search(re.sub(r"(?i)perfume capilar|perfume (?:para|pra) (?:o )?cabelo|hair mist|hair perfume",
                                      "", t)))


def beneficio(titulo: str, grupo: str | None) -> str | None:
    """Frase de benefício do TIPO do produto (ou None): o termo que aparece primeiro na cabeça do título vence."""
    cabeca = cabeca_do_titulo(titulo)
    melhor: tuple[int, int, str | None] | None = None  # (posição, ordem na lista, frase)
    perfume = eh_perfume(titulo, grupo)
    cabelo, kit_cab = eh_cabelo(titulo, grupo), kit_capilar(titulo)
    for i, (rx, frase) in enumerate(_BENEF):
        if i < BENEFICIOS_BELEZA and grupo not in (None, "beleza", "cabelo", "perfume"):
            continue  # frase de maquiagem/cabelo só em produto de beleza (cama de cachorro "com base" virava "PELE DE FILTRO")
        if perfume and frase in FRASES_MAKE:
            continue  # 06/10 (Rita): perfume "Una Blush" virava frase de blush pelo NOME
        if (cabelo and frase in FRASES_MAKE) or (kit_cab and frase in FRASES_PERFUME):
            continue  # 08/10 (Rita): "Primer Antifrizz" (cabelo) e kit máscara + perfume capilar
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
        if kit_cab and fino in FRASES_PERFUME:
            continue
        if any(rx.search(cabeca) for rx, f in _BENEF if f == fino):
            frase = fino
            break
    if frase == "BRILHO NO LOOK SEM GASTAR MUITO ✨" and TECNOLOGIA.search(cabeca):
        return "TECNOLOGIA NO PRECINHO 📱"  # smartwatch não é joia
    # 01/10 (Rita): kit "Casal Body Splash Bold Homme E My Sweet Delight" saiu "PERFUME PRA ELE" — kit de casal é dos dois
    if frase in FEMININAS and MASCULINO.search(titulo) and not re.search(
            r"feminin|unissex|mulher|calcinha|\bcasal\b|ele e ela|dele e dela|\bdupla\b", titulo, re.I):
        return PRA_ELE  # perfume/relógio/tênis masculino não é "amiga"
    return frase


# ---- conferência da legenda ANTES de enviar (01/10, dona: "se atentar na legenda sempre antes de mandar… só pq o
# nome diz whey não quer dizer que seja de treino") — a frase do gancho tem que ser do MESMO tipo do produto
CAT_GANCHO = [("fitness", r"TREINO|SUPLEMENT|ACADEMIA|MALHA|SHAPE|PROTE[ÍI]NA"),
              ("beleza", r"CABELO|(?<!\d )FIOS|CACHO|\bLISO\b|CAPILAR|CHEIR|PERFUM|\bPELE\b|ROSTO|\bMAKE\b|BOCA|"
                         r"C[ÍI]LIOS|L[ÁA]BIO|\bUNHA|SKINCARE|GLOW|VI[ÇC]O"),
              ("pet", r"\bPET\b|DOGUINHO|GATINHO|AU ?AU")]
# produto que é claramente de OUTRO tipo (conflito) — só o óbvio, para não trocar frase boa à toa
COSMETICO = re.compile(r"capilar|cabelo|shampoo|condicionador|creme|facial|lo[çc][ãa]o|s[ée]rum|sabonete|t[ôo]nico|"
                       r"m[áa]scara (?:capilar|facial)|hidratante|perfume|maquiag|batom|gloss|esmalte", re.I)
ROUPA_TREINO = re.compile(r"legging|\btop\b|short|cal[çc]a|conjunto|academia|fitness|bicicleta|esteira", re.I)
SUPLEMENTO = re.compile(r"\bwhey\b|creatina|pr[ée]-?treino|suplemento|col[áa]geno em p[óo]|albumina|termog[êe]nic|"
                        r"esteira|bicicleta ergom|halter|anilha", re.I)


# 01/10 (dona, relógio com "ORGANIZE SUA BAGUNÇA"): a família da FRASE tem que bater com a categoria do PRODUTO.
# Frase → família (regex na frase) → categorias aceitas. Frase neutra (achado do dia, % OFF, pra ele…) passa sempre.
FAMILIAS = [  # (frase, categorias aceitas, palavra que o TÍTULO precisa ter — ou None)
    (r"ORGANIZ|BAGUN[ÇC]A", {"casa", "infantil", "beleza", "moda"},
     r"organiz|porta[- ](?!rel[óo]gio)|expositor|cesto|caixa|armazenamento|v[áa]cuo|necessaire|gaveta|prateleira"),
    # \bLAR\b: "CELULAR NA BOLSA" caía em casa (verificador 01/10); QUENTINHO vale para jaqueta/pantufa/moletom (moda)
    (r"COZINHA|CAMA DE HOTEL|TOALHA|\bLAR\b|CASA (?:LINDA|ARRUMADA)", {"casa", "infantil"}, None),
    (r"GELADINHO|QUENTINHO", {"casa", "esporte", "infantil", "moda"}, None),
    (r"TECNOLOGIA|GADGET|NOTIFICA[ÇC]", {"eletronicos", "moda", "esporte"}, None),
    (r"\bPET\b|DOGUINHO|GATINHO|BOLSO TAMB[ÉE]M", {"pet"}, None),
    (r"PEQUENOS|MAM[ÃA]E|CRIAN[ÇC]A|BEB[ÊE]", {"infantil", "moda", "casa", "beleza", "cabelo", "perfume"}, None),
    (r"LOOK|P[ÉE] LINDO|SALTO|BOLSA|JOIA|BRILHO NO LOOK|PROTE[ÇC][ÃA]O COM ESTILO|VER[ÃA]O|SAND[ÁA]LIA|T[ÊE]NIS",
     {"moda", "esporte", "infantil"}, None),
    (r"TREINO|SUPLEMENT|ACADEMIA|SHAPE", {"esporte", "beleza", "moda", "eletronicos"}, None),  # smartwatch esportivo
    (r"CABELO|CACHO|\bLISO\b|CAPILAR|(?<!\d )FIOS|PELE|CHEIR|PERFUM|[ÁA]RABE|BOCA|OLHAR|MAKE|FILTRO|DEPILA[ÇC]|C[ÍI]LIOS|"
     r"UNHA|SKINCARE|GLOW|VI[ÇC]O|AUTOCUIDADO|MASSAGEM|CORPO CANSADO", {"beleza", "cabelo", "perfume", "infantil"}, None),
]


def familia_confere(frase: str, grupo: str | None, titulo: str = "") -> bool:
    """A frase é da família da categoria do produto (e, para "organize", o título fala de organizador mesmo)?"""
    for rx, aceitas, precisa in FAMILIAS:
        if re.search(rx, frase or "", re.I):
            if precisa and titulo and not re.search(precisa, titulo, re.I):
                return False
            # "esporte"/"outros" a fonte erra muito (lençol, mixer, patinete em esporte) → não serve de prova contra
            return not grupo or grupo in GRUPOS_RUIDOSOS or grupo in aceitas
    return True


GRUPOS_RUIDOSOS = {"esporte", "outros", "mercado"}
BRINQUEDO = re.compile(r"(?i)brinquedo|boneca|reborn|bonec[oa]s?\b|pel[úu]cia|triciclo|motoca|motoquinha|velotrol|"
                       r"quadriciclo|carrinho (?:de )?(?:controle|brinquedo|passeio)|patinete|bicicleta|jogo\b|"
                       r"quebra-?cabe[çc]a|lego\b|blocos de montar")
ROUPA_BEBE = re.compile(r"(?i)\bbody\b|macac[ãa]o|roupa|conjunto|pijama|vestido|camis|cal[çc]a|short|meia|sapat|t[êe]nis|"
                        r"fralda|len[çc]o")


# frase que NOMEIA o tipo de calçado só vale se o título for desse tipo (gancho, título)
NOME_NO_GANCHO = [
    (r"\bT[ÊE]NIS\b", r"t[êe]nis|sneaker"),
    (r"\bSAND[ÁA]LIA", r"sand[áa]lia|rasteira|papete|slide"),
    (r"\bSALTO\b", r"salto|scarpin|tamanco|anabela"),
    (r"\bCHINELO", r"chinelo|slide"),
    (r"\bSAPATO\b", r"sapato|mocassim|loafer|oxford|sapatilha|scarpin"),
    # 06/10 (dona, print 12h31: "BATOM COM CARA DE GRIFE" num tênis "Slip On") — produto citado no gancho tem de estar
    # no título (palavra inteira: "Slip" não é "lip")
    (r"\bBATO(M|NS)\b", r"batom|batons|\blip(?:stick)?\b|lip ?tint|labial"),
    (r"\bGLOSS\b", r"gloss|\blip\b|labial|brilho"),
    (r"\bBLUSH\b", r"blush|blush|rouge|bochecha"),
    (r"\bPERFUME\b", r"perfume|parfum|eau de|col[ôo]nia|body splash|fragr"),
    (r"\bBOLSA\b", r"bolsa|clutch|necessaire|mochila|carteira"),
    (r"[ÓO]CULOS", r"[óo]culos"),
]


def gancho_confere(gancho: str, titulo: str, grupo: str | None, novas: bool = True) -> bool:
    """False quando a frase do gancho é de um tipo e o produto é claramente de outro (whey de tônico capilar com
    frase de treino; suplemento com frase de pele; frase de pet em produto que não é pet). Frase neutra passa.
    `novas=False`: sem as checagens de 08/10 noite (o verificador relendo post publicado antes delas)."""
    if not familia_confere(gancho, grupo, titulo):
        return False
    # 06/10 noite (Rita): frase de make em perfume ("Una Blush" → "BOCHECHA CORADINHA") e de perfume em make, não
    perfume = eh_perfume(titulo, grupo)
    if perfume and GANCHO_MAKE.search(gancho or ""):
        return False
    if not perfume and GANCHO_PERFUME.search(gancho or "") and MAKE_RX.search(titulo or ""):
        return False
    # 08/10 (Rita/Beto): a categoria manda, não a palavra solta — make em cabelo ("Primer Antifrizz… Fios") ou em creme
    # de "Contorno dos Olhos", e perfume em kit de tratamento capilar ("Máscara + Perfume Capilar"), não
    if novas and GANCHO_MAKE.search(gancho or "") and "DESCANSAD" not in (gancho or "").upper() and (
            eh_cabelo(titulo, grupo) or (SKINCARE_OLHOS.search(titulo or "") and not MAKE_DE_VERDADE.search(titulo or ""))):
        return False
    if novas and GANCHO_PERFUME.search(gancho or "") and kit_capilar(titulo):
        return False
    # 08/10 noite (Beto): grife/dupe/"parece caro" em tiara, organizador, flor de plástico não (perfume é outra conversa:
    # "Body Splash Floral" pode ter "CHEIRO DE GRIFE")
    if novas and ganchos.GANCHO_GRIFE.search(gancho or "") and not perfume and not ganchos.dupe_cabe(titulo):
        return False
    # 08/10 noite (Rita: corretivo Mari Maria com "SEM BRILHO NA TESTA"): gancho de oleosidade/brilho só em pó, primer
    # e base — corretivo disfarça olheira
    if novas and ganchos.GANCHO_OLEOSIDADE.search(gancho or "") and ganchos.so_corretivo(titulo):
        return False
    # 03/10 (dona): "Óleo e Sérum Bifásico Dove" (cabelo) saiu "PELE LISINHA" → frase de pele só se o título não for de cabelo
    if re.search(r"PELE|SKINCARE|ROSTO", gancho or "", re.I) and CABELO.search(titulo or "") and not re.search(
            r"rosto|facial|\bpele\b|face\b", titulo or "", re.I):
        return False
    # 06/10 (Beto): "Spray Gloss" de pontas (cabelo) saiu com frase de boca 💋 — "gloss" em cabelo não é lábio
    if re.search(r"BOCA|BATOM|TINT|💋", gancho or "") and (CABELO.search(titulo or "") or re.search(
            r"pontas (?:duplas|secas)|\bfios\b", titulo or "", re.I)) and not re.search(
            r"l[áa]bi|\bboca\b|batom|\blip", titulo or "", re.I):
        return False
    # 06/10 (Rita): lava-roupas "Perfume" com frase de cheirinho — frase de perfume só em perfume de gente
    if re.search(r"CHEIROS|PERFUME|CHEIRO DE GRIFE|FRAGR", gancho or "", re.I) and fora_do_nicho_grupo(titulo or ""):
        return False
    # 04/10 (revisora da nuvem): triciclo/motoquinha com "LOOK FOFO PROS PEQUENOS"; boneca reborn com "ACHADINHO PRA
    # MAMÃE" → frase de roupa/bebê não vai em brinquedo
    if re.search(r"LOOK|ROUPINHA|MAM[ÃA]E|BEB[ÊE]", gancho or "", re.I) and BRINQUEDO.search(titulo or "") \
            and not ROUPA_BEBE.search(titulo or ""):
        return False
    for no_gancho, no_titulo in NOME_NO_GANCHO:  # 03/10 (dona): mocassim saiu como "TÊNIS DE MARCA"
        if re.search(no_gancho, gancho or "", re.I) and not re.search(no_titulo, titulo or "", re.I):
            return False
    cat = next((c for c, rx in CAT_GANCHO if re.search(rx, gancho or "", re.I)), None)
    t = re.split(r"\bsabor\b", titulo or "", flags=re.I)[0]  # "Whey… Sabor Creme de Avelã": sabor não é cosmético
    if cat == "fitness":
        return not (COSMETICO.search(t) and not ROUPA_TREINO.search(t))
    if cat == "beleza":
        return not (SUPLEMENTO.search(t) and not COSMETICO.search(t)) and not PET.search(t)
    if cat == "pet":
        return bool(PET.search(t)) or grupo == "pet"
    return True


# 08/10 noite (verificador do servidor 96% → 71% às 17h30: 26 "sem preço 'De' no post"): regra nova de conferência só
# vale para post publicado DEPOIS que ela chegou ao robô do servidor — o verificador relê o dia inteiro, e os posts da
# manhã/tarde saíram (certos pela regra da hora) com o "De" escondido. 1ª rodada do servidor com o código novo: 17h30.
REGRA_DE_NO_POST_DESDE = "2026-10-08 17:30:00"   # "sem preço 'De' no post" (furos da fila, 08/10 tarde)
# ganchos novos de 08/10: make em cabelo/contorno dos olhos e perfume em kit capilar (tarde); grife/dupe em enfeite e
# oleosidade em corretivo (noite)
REGRAS_GANCHO_NOITE_DESDE = "2026-10-08 17:30:00"


def conferir_legenda(o: dict, texto: str, quando: str = "") -> list[str]:
    """Checagem final de CADA post antes do envio (rodada do grupo): lista de problemas (vazia = pode mandar).
    `quando` = hora em que o post SAIU (só o verificador passa): regra criada depois disso não conta contra ele."""
    probs = []
    quando = (quando or "").replace("T", " ")
    o = com_tipo(o)  # 08/10 noite: confere a frase contra o título COM o tipo (o mesmo que sai no post)
    linhas = (texto or "").replace("⁠", "").split("\n")
    if not gancho_confere(linhas[0].strip("* "), o.get("titulo") or "", o.get("grupo"),
                          novas=not quando or quando >= REGRAS_GANCHO_NOITE_DESDE):
        probs.append(f"frase não combina com o produto: {linhas[0][:40]}")
    if not ganchos.economia_ok(linhas[0].strip("* "), o.get("preco")):  # 06/10 (Rita)
        probs.append(f"frase de economia em produto acima de R$ {ganchos.PRECO_ECONOMIA}")
    if preco_proibido(o):  # 06/10 (Beto): programa da Vivara proíbe preço
        probs.append("Vivara não pode sair com preço")
    if o.get("preco") and _brl(o["preco"]) not in "\n".join(linhas):
        probs.append("preço do texto diferente do preço da oferta")
    if o.get("preco_antigo") and o.get("preco") and o["preco_antigo"] <= o["preco"]:
        probs.append("'de' menor ou igual ao 'por'")
    # 08/10 (Rita, 5ª rodada seguida): post de oferta sem a linha "De:" riscada não vai (regra da dona) — última trava
    if ("*Por:*" in (texto or "") and not any(ln.startswith("De: ~") for ln in linhas)
            and (not quando or quando >= REGRA_DE_NO_POST_DESDE)):
        probs.append("sem preço 'De' no post")
    # 08/10 (revisão): post com 2 links (💌 indique / 🎟️ cupons do dia) — o link do PRODUTO é o do "Compre aqui"; o do
    # site/cupom não pode esconder um "Compre aqui" sem link (texto sem "Compre aqui" = regra antiga, qualquer https)
    compre = re.search(r"Compre aqui:?\**[ \t]*(\S*)", texto or "")
    if not (compre.group(1).startswith("https://") if compre else "https://" in (texto or "")):
        probs.append("sem link")
    # 01/10 (dona): link da Shopee sempre curto — exceto na emergência de 02/10 (fila sem link curto: postar com o link
    # comum é melhor que o grupo parado; o vigia avisa)
    if "shope.ee/an_redir" in texto and not o.get("_emergencia"):
        probs.append("link da Shopee sem encurtar")
    if re.search(r"\bNone\b|\{[a-z]\}", texto):
        probs.append("texto com campo vazio")
    # 03/10 (dona apagou o post): "O pato protetor solar muda de cor quando exposto ao sol, mas não muda…" — título que é
    # FRASE traduzida (não nome de produto) nunca vai para o grupo
    t = (o.get("titulo") or "").strip()
    if (re.search(r"\b(quando|mas n[ãa]o|porque|enquanto|se voc[êe]|voc[êe] (pode|vai)|isso|esse produto)\b", t, re.I)
            or re.match(r"^(O|A|Os|As|Um|Uma|Este|Esta|Esse|Essa)\s", t)
            or (t.endswith(".") and len(t.split()) > 8)):
        probs.append("título parece frase traduzida, não nome de produto")
    return probs


def gancho_post(o: dict, n: int = 0, recentes: list[str] | None = None) -> str:
    """Gancho conferido: se a frase escolhida não combina com o tipo do produto, vai uma frase neutra."""
    from vendas import ganchos
    o = com_tipo(o)  # 08/10 noite: "Eudora Diva Esplêndida Feminino 100ml" → frase de perfume, não "10 MIL VENDIDOS"
    g = _gancho_bruto(o, n, recentes)
    preco = o.get("preco") or 0
    # 06/10 (Rita): frase de economia ("SEM GASTAR MUITO", "GASTANDO POUCO") só até R$ 120
    if gancho_confere(g, o.get("titulo") or "", o.get("grupo")) and ganchos.economia_ok(g, preco):
        return g
    # 05/10: frase neutra também sem "PRECINHO"/"CENTAVO" em produto caro
    uni = _girar([f for f in ganchos.UNIVERSAL if ganchos.economia_ok(f, preco)], n)
    return ganchos.escolher(uni, n, recentes)


def _gancho_bruto(o: dict, n: int = 0, recentes: list[str] | None = None) -> str:
    """Gancho do post: desconto absurdo → preço por unidade em kit → frase do TIPO/CARACTERÍSTICA do produto
    (vendas/ganchos.py) → sinal real de venda / frase universal. `recentes` = ganchos dos últimos posts (não repetir)."""
    from vendas import ganchos
    d, titulo, preco = o.get("desconto") or 0, o.get("titulo") or "", o.get("preco") or 0
    s0 = sinais_de(o)  # 06/10: achado da Eva (parecido com a foto da divulgação) → gancho de DUPE ("QUEM VÊ JURA…")
    if g := (s0.get("gancho_dona") or "").strip():  # 09/10: `achadinhos pedido --texto` (o gancho_post ainda confere)
        return g
    if s0.get("origem") == "vitrine_fiel" and (frases := [f for f in ganchos.dupe(titulo) if ganchos.economia_ok(f, preco)]):
        return ganchos.escolher(frases, n, recentes)
    if not de_confiavel(o):
        d = 0  # 02/10: "De" inflado da loja → nada de gancho "XX% OFF"
    if d >= 70 and n % 3 == 0:  # nos grupos o "% OFF" é tempero, não regra — o que domina é o benefício
        return ganchos.escolher([f.format(d=d) for f in ganchos.OFF], n, recentes)
    m = KIT.search(titulo)
    qtd = int(next(g for g in m.groups() if g)) if m else 0
    if 2 <= qtd <= 12 and preco and preco / qtd <= 30:  # "60 unidades" de lenço/cápsula: "R$ 0,53 CADA" engana
        return ganchos.escolher([f.format(p=_brl(preco / qtd).upper()) for f in ganchos.KIT], n, recentes)
    tipo = beneficio(titulo, o.get("grupo"))
    if tipo and "PEQUENOS" in tipo and re.search(r"\bhen[êe]\b|capilar|cabelo|alisa", titulo, re.I):
        tipo = None  # 05/10: "Henê Pelúcia Forte" (cabelo) saiu "DIA DAS CRIANÇAS TÁ CHEGANDO" por causa de "Pelúcia"
    if tipo and not familia_confere(tipo, o.get("grupo"), titulo):
        tipo = None  # 01/10 (dona): "ORGANIZE SUA BAGUNÇA" num RELÓGIO ("Curren Portas Relógios…") → frase neutra
    if tipo and (frases := ganchos.candidatos(tipo, titulo, preco=preco)):  # vazia = só frase de economia em produto caro
        return ganchos.escolher(frases, n, recentes)
    if MASCULINO.search(titulo) and not re.search(r"feminin|unissex|mulher|calcinha", titulo, re.I):
        return ganchos.escolher(ganchos.REPERTORIO[PRA_ELE], n, recentes)
    sinais = s0
    opcoes = []
    vend = sinais.get("vendidos_num", 0) or 0
    if vend >= 10000:  # número real do anúncio, em vez de "tá bombando" genérico
        opcoes.append(f"MAIS DE {vend // 1000} MIL VENDIDOS 🔥")
    if sinais.get("top"):
        opcoes.append("A GALERA AMOU ESSA 🔥")
    if d >= 60:
        # 01/10 (dona): "PRECINHO DE BUG 🐞" — bug virou joaninha, sem sentido → frase clara
        opcoes.append("PREÇO QUE PARECE ERRO 😱")
    uni = _girar([f for f in ganchos.UNIVERSAL if ganchos.economia_ok(f, preco)], n)  # R$ 207 não é "precinho"
    return ganchos.escolher(opcoes + uni, n, recentes)


def _girar(lista: list, n: int) -> list:
    """08/10 (Tati: "ACHADO DO DIA" 12×, "VALE CADA CENTAVO" 11× em ~400 posts): o escolher pega a 1ª frase livre, e
    as universais saíam sempre na mesma ordem → começa a lista num ponto diferente a cada post."""
    if not lista:
        return lista
    k = (n * 7) % len(lista)
    return lista[k:] + lista[:k]


LUXO = re.compile(  # marcas "caras" que fazem a pessoa parar o dedo (vídeo de divulgação do grupo, 30/09)
    r"(?<!\w)(?:dior|chanel|carolina herrera|lanc[ôo]me|givenchy|ysl|yves saint|prada|michael kors|coach|guess|arezzo|"
    r"schutz|carmen steffens|lacoste|tommy hilfiger|calvin klein|ray-?ban|oakley|vivara|pandora|k[ée]rastase|clinique|"
    # "dolce" sozinho pegava Dolce Gusto (café) e Dolce Arome (cafeteira) como grife — 30/09
    r"victoria.?s secret|jean paul gaultier|paco rabanne|azzaro|montblanc|hugo boss|armani|versace|"
    r"dolce\s*(?:&|e|and)\s*gabbana|d&g|burberry|gucci|fendi|valentino|bvlgari|bulgari|tiffany|cartier|swarovski|"
    r"jimmy choo|marc jacobs|kate spade|longchamp|furla|victor hugo|mont blanc|"
    r"miu miu|rare beauty|rhode|"  # 07/10: marcas do Acervo da dona (vitrine/artes do anúncio)
    r"shiseido|too faced|laneige|fossil|mac cosmetics|kiko|la roche-?posay|sephora collection|lattafa|stanley)(?!\w)",
    re.I)


def ofertas_luxo(horas: int = 36, n: int = 8, preco_de_min: float = 200) -> list[dict]:
    """Ofertas de MARCA com desconto real e preço cheio alto (a vitrine do vídeo de divulgação): aprovadas, com foto e
    link, 'de' ≥ preco_de_min e ≥ 25% off; a melhor de cada tipo de produto, das mais impressionantes para as menos.
    07/10 (revisão): as MESMAS barreiras do grupo — vetada pelas revisoras (pelo produto), perfume de grife sem ml,
    "De" inflado e Vivara (sem preço). Antes o vídeo "destaque" diário (luxo_acervo --novas) podia anunciar "My Way COM
    46% OFF" que o Beto barrou no grupo."""
    from vendas import revisao_fila
    vet = revisao_fila.vetados()
    chaves_vet = revisao_fila.chaves_vetadas(vet)
    ofs = [o for o in sem_repetidos(melhores(horas, 20000)) if o["link_loja"] and o.get("foto")
           and LUXO.search(o["titulo"] or "") and (o.get("preco_antigo") or 0) >= preco_de_min
           and (o.get("desconto") or 0) >= 25 and not MASCULINO.search(o["titulo"] or "")
           and not barrada_na_hora(o) and not revisao_fila.vetada(o, vet, chaves_vet)]
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


# 02/10 (dona: "a gente não precisa mentir; não precisa barrar a marca, só quando o preço estiver ok"): lojas cujo
# preço "De" é inflado de propósito (koksara.kbeauty: "De" ~70% fixo, mini com o "De" do tamanho cheio) → a oferta
# pode sair, mas SEM "De" riscado e sem gancho de "% OFF"; a trava acima_do_mercado continua valendo.
LOJAS_DE_INFLADO = ("457864097",)


_DESC_LOJA: dict = {}


def _loja_desconto_fixo(link: str) -> bool:
    """02/10 (Beto): loja da Shopee em que quase TODAS as peças têm o mesmo desconto (Vizzela: 23–25% em 20 de 20;
    "De" = preço ÷ 0,75) → o "De" é inventado. ≥ 8 ofertas e ≥ 80% delas a ±2 pontos da mediana (≥ 15%)."""
    from statistics import median
    m = re.search(r"shopee\.com\.br/product/(\d+)/", link or "")
    if not m:
        return False
    if not _DESC_LOJA:
        por: dict[str, list[int]] = {}
        for r in db.consultar("SELECT link_loja, desconto FROM ofertas WHERE fonte = 'shopee_afiliados' AND desconto > 0 "
                              "AND visto_em >= datetime('now','localtime','-7 days')"):
            s = re.search(r"/product/(\d+)/", r["link_loja"] or "")
            if s:
                por.setdefault(s.group(1), []).append(r["desconto"])
        for s, ds in por.items():
            med = median(ds)
            _DESC_LOJA[s] = len(ds) >= 8 and med >= 15 and sum(abs(d - med) <= 2 for d in ds) >= 0.8 * len(ds)
        _DESC_LOJA.setdefault("_", False)
    return _DESC_LOJA.get(m.group(1), False)


def de_confiavel(o: dict) -> bool:
    """Loja normal → confia no "De". Loja da lista (ou com desconto igual em quase tudo) → o "De" só aparece quando é
    REAL (dona 02/10: "onde a oferta for real pode deixar o De"): até 20% acima da mediana do mesmo produto nas outras
    lojas (preço normal de mercado)."""
    link = o.get("link_loja") or ""
    if not any(f"/product/{s}/" in link for s in LOJAS_DE_INFLADO) and not _loja_desconto_fixo(link):
        # 08/10 (revisão + Rita/Beto: CeraVe 50 ml R$ 65,99 com "De" 119,98, mercado a R$ 54): em loja COMUM, quando o
        # "Por" já é o preço normal do mercado e o "De" passa de 1,5× a mediana, o desconto só existe pelo "De" inflado
        # → não risca o "De" (nem gancho de % OFF). Loja oficial fica de fora (o "De" dela é o de tabela da marca).
        if sinais_de(o).get("oficial") or not o.get("preco_antigo"):
            return True
        med = mediana_mercado(o)
        return not (med and o.get("preco", 0) >= med and o["preco_antigo"] > 1.5 * med)
    med = mediana_mercado(o)
    return bool(med and o.get("preco_antigo") and o["preco_antigo"] <= 1.2 * med and o.get("preco", 0) < med)


def legenda_post(o: dict, n: int = 0, recentes: list[str] | None = None) -> str:
    """Legenda no formato dos grupos: GANCHO → loja → produto → De/Por → cupom → link → aviso."""
    gancho = gancho_post(o, n, recentes)
    if recentes is not None:
        recentes.append(gancho)  # a própria rodada também não repete
    # 01/10 (dona, print do "Ofertas Entre Mulheres"): 🛍️ produto / "De:" riscado / 🗣️ *Por:* / 🛒 *Compre aqui:*
    por = f"🗣️ *Por:* {_brl_zap(o['preco'])}"
    # 09/10 (Claude): ML com desconto só no Pix (Too Faced 197,10 no Pix × 219 no cartão) — dizer "no Pix", senão
    # quem paga no cartão acha que o preço é mentira
    if (o.get("sinais") or {}).get("so_pix") if isinstance(o.get("sinais"), dict) else False:
        por += " no Pix"
    if hora := hora_do_preco(o):  # 30/09 (dona): Amazon exige data/hora junto do preço → "hora curtinha"
        por += f" _(às {hora})_"
    linhas = [f"*{gancho}*", "", f"🛍️ {titulo_do_post(o)}", ""]
    if o.get("preco_antigo") and de_confiavel(o):
        linhas.append(f"De: ~{_brl_zap(o['preco_antigo'])}~")
    linhas.append(por)
    if selo := selo_preco(o):
        linhas.append(selo)
    # 08/10 (Vitória, 20 líderes): "Loja oficial" escrito no post dá confiança para clicar — 08/10 noite (Rita): só se
    # a loja for oficial DA MARCA do produto (revendedora Shopee Mall, como a koksara, não)
    if loja_oficial_da_marca(o):
        linhas.append("✔️ Loja oficial")
    if o.get("cupom"):
        linhas.append(f"🎟️ Cupom: *{o['cupom']}*")
        # 09/10 (dona: "pode pôr"): no site da Océane, quem chega pelo NOSSO link vê a bolinha da Bella Lucce com o
        # cupom para copiar (recurso do programa #SQUAD) — avisar ajuda a pessoa a usar o cupom
        if o.get("fonte") == "oceane_afiliados":
            linhas.append("_Ao abrir o link, aparece a bolinha da Bella Lucce com o cupom pra copiar 💗_")
    # 30/09 (dona): SEM rodapé nas mensagens ("Preço de… pode mudar. #publi · Associado Amazon…") — o aviso de
    # afiliado fica no site e na descrição do grupo, não em cada post
    linhas += ["", f"🛒 *Compre aqui:* {link_afiliado(o['link_loja'], canal='whatsapp')}"]  # post do grupo = WhatsApp
    if n % 5 == 4:  # como os grupos grandes: de vez em quando pede indicação (crescimento sem pegar número de ninguém)
        linhas += ["", f"💌 Indique pra uma amiga: {canais().get('site', SITE_URL)}"]
    elif n % 5 == 2 and not o.get("cupom") and (c := _cupom_shopee_manha(o)):
        linhas += ["", f"🎟️ Resgate os cupons do dia: {c}"]
    return "\n".join(linhas)


def com_tipo(o: dict) -> dict:
    """A oferta com o título completado pelo tipo (tipo_completado) — para escolher e conferir o gancho."""
    t = tipo_completado(o)
    return {**o, "titulo": t} if t and t != (o.get("titulo") or "") else o


def titulo_do_post(o: dict) -> str:
    """Nome do produto no post: limpo, cortado e — 08/10 noite — com o tipo quando o título só diz marca + linha."""
    t = limpar_titulo(o.get("titulo") or "")
    return cortar(tipo_completado({**o, "titulo": t}) or t, 100)


def _cupom_shopee_manha(o: dict, agora: datetime | None = None) -> str | None:
    """08/10 (Vitória, 20 líderes: modelo Promos da Ca): de manhã, 1 a cada 5 posts da Shopee lembra de resgatar os
    cupons do dia — com o NOSSO link curto que a Cora conferiu em cupons_dia.json (sem cupom do dia, não sai)."""
    agora = agora or datetime.now()
    if agora.hour >= 12 or "shopee" not in (o.get("link_loja") or "").lower():
        return None
    from vendas import datas
    # 08/10 (revisão): "shopee" minúsculo da Cora também vale; o arquivo fora do formato já volta [] em cupons_do_dia
    return next((str(c["link"]) for c in datas.cupons_do_dia(agora.date())
                 if str(c.get("loja") or "").strip().lower() == "shopee" and "s.shopee.com.br/" in str(c["link"])), None)


SITE_URL = "https://bellalucce.github.io"
HORAS_LOJA = (10, 13, 16, 19)  # 4 posts/dia de produto NOSSO no grupo (exposição grátis; 30/09: 1 visita/semana no ML)


def post_da_loja(agora: datetime | None = None) -> tuple[str, str] | None:
    """(legenda, foto local) de um produto da PRÓPRIA loja com estoque, em rodízio, nas HORAS_LOJA (1ª rodada da hora).
    Produtos/links/legendas vêm de config/midia.json (os mesmos dos vídeos). None fora do horário ou sem estoque."""
    from vendas import midia
    agora = agora or datetime.now()
    if agora.hour not in HORAS_LOJA or agora.minute >= 15:
        return None
    cfg = json.loads((config.CONFIG / "midia.json").read_text(encoding="utf-8"))
    prods = cfg["produtos"]

    def _so_shopee(p: dict) -> dict:
        # 06/10 (Kátia): link do ML NUNCA no WhatsApp/Telegram → o grupo só leva o link da Shopee; 'foto_grupo' = foto
        # de UMA unidade (a 'foto' dos vídeos mostra todas as cores); preço só da Shopee
        links = {c: u for c, u in (p.get("links") or {}).items() if not NAO_PERMITE_WHATSAPP.search(u)}
        preco = " · ".join(x for x in str(p.get("preco") or "").split(" · ") if "ML" not in x and "Mercado" not in x)
        return {**p, "links": links, "foto": p.get("foto_grupo") or p.get("foto"), "preco": preco}

    def _tem(prefixos: list[str]) -> bool:
        from vendas import estoque
        itens = estoque.itens()
        return all(any(i["sku"].startswith(pre) and i["qtd"] > 0 for i in itens) for pre in prefixos)

    ok = [(k, q) for k, p in prods.items() if (q := _so_shopee(p))["links"] and midia.tem_estoque(k)
          and (config.RAIZ / q["foto"]).exists()]
    # 06/10 (Kátia, dona: "impulsiona os KITS"): kit montado na Shopee pelo Leve Mais por Menos entra no rodízio
    # (config/midia.json → kits_grupo; só com estoque de TODOS os componentes e a foto com as mesmas unidades do kit)
    ok += [(f"kit:{k.get('id', i)}", q) for i, k in enumerate(cfg.get("kits_grupo") or [])
           if k.get("ativo") and (q := _so_shopee(k))["links"] and _tem(k.get("estoque") or [])
           and (config.RAIZ / q["foto"]).exists()]
    if not ok:
        return None
    vez = agora.timetuple().tm_yday * len(HORAS_LOJA) + HORAS_LOJA.index(agora.hour)
    sku, p = ok[vez % len(ok)]
    legenda = p["legendas"][vez % len(p["legendas"])] if p.get("legendas") else p["nome"]
    cabeca = "*DA NOSSA LOJINHA, EM PROMOÇÃO 💖*"
    # 01/10 (dona: "o batom de ursinho é bom pro Dia das Crianças também"): data grande chegando com produto NOSSO
    # (agenda → chegando[].loja_sku) → nas 2 primeiras horas da loja do dia ele entra no lugar do rodízio
    from vendas import datas
    for c in datas.chegando(agora.date()):
        alvo = dict(ok).get(c.get("loja_sku"))
        if alvo and HORAS_LOJA.index(agora.hour) < 1:  # 06/10 (Kátia): 1×/dia — 2× repetia o MESMO texto às 10h e 13h
            sku, p = c["loja_sku"], alvo
            legenda = c.get("loja_legenda") or legenda
            cabeca = f"*{c.get('loja_chamada', 'PRESENTE DA NOSSA LOJINHA 🎁')}*"
            break
    pr = promo_valendo(p, agora) or {}
    if pr:
        # 01/10 (dona): "lança meus produtos com promoção, sem promoção fica nada a ver" → mesmo modelo das ofertas
        # (De riscado / Por), com a Promoção de Desconto REAL criada na Shopee (config/midia.json → promo)
        linhas = [cabeca, "", f"🛍️ {legenda}", "", f"De: ~{pr['de']}~",
                  f"🗣️ *Por:* {pr['por']} _(na {pr.get('canal', 'Shopee')}, até {pr['ate'][8:10]}/{pr['ate'][5:7]})_"]
        url = p["links"].get(pr.get("canal", "Shopee")) or next(iter(p["links"].values()))
        linhas += ["", f"🛒 *Compre aqui:* {url}"]
    else:
        linhas = [f"*{p['chamada']}*" if p.get("chamada") else "*DA NOSSA LOJINHA 💖*", "", f"🛍️ {legenda}"]
        if p.get("preco"):
            linhas += ["", f"💰 {p['preco']}"]
        linhas += ["", f"🛒 *Compre aqui:* {next(iter(p['links'].values()))}"]
    linhas += ["", "_Produto da loja Bella Lucce, notificado na ANVISA._"]
    return "\n".join(linhas), str(config.RAIZ / p["foto"])


LEMBRETE_HORAS = (12, 18)  # 30/09 (dona): "repete 2× por dia" — quem entrou depois não vê as ofertas da manhã
TEXTO_LEMBRETE = ("📌 *CHEGOU AGORA?*\n\nTodas as ofertas de hoje ficam no nosso site, separadinhas por categoria 👇\n{site}"
                  "\n\n_Aqui no grupo chegam ofertas novas o dia todo, das 8h às 22h._")  # 01/10 (dona): até as 22h


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


def shopee_sem_curto(o: dict) -> bool:
    """Produto da Shopee cujo link para o grupo ainda seria o comprido (shope.ee/an_redir?...)."""
    if not re.search(r"shopee\.com\.br/product/", o.get("link_loja") or ""):
        return False
    return not re.match(r"https://s\.shopee\.com\.br/", link_afiliado(o["link_loja"], canal="whatsapp") or "")


MAX_TIPO_DIA = 3
TIPO_DIA = [(r"rel[óo]gio", "relógio"), (r"creatina", "creatina"), (r"\bwhey\b", "whey"), (r"mochila", "mochila"),
            (r"t[êe]nis\b", "tênis"), (r"smart ?watch|smart ?band|mi ?band", "smartwatch"), (r"\bfones?\b|headset|earbuds?", "fone"),
            (r"camiseta|camisa\b", "camiseta"), (r"\bcal[çc]a\b", "calça"), (r"panela", "panela"),
            (r"garrafa|copo t[ée]rmico", "garrafa"), (r"[óo]culos", "óculos"), (r"sand[áa]lia|rasteir", "sandália"),
            (r"carregador|cabo usb", "carregador"), (r"\bbolsa\b(?! t[ée]rmica)", "bolsa"),
            # 09/10 (mix 6/3/1 com pijama e acessório): não deixar a vaga de moda virar só pijama ou só brinco
            (r"pijama|camisola|short[- ]?doll|baby[- ]?doll", "pijama"), (r"brincos?\b", "brinco"),
            (r"\bcolar(?:es)?\b|gargantilha|choker|\bcorrentes?\b", "colar"), (r"\bmules?\b|tamanco", "mule"),
            (r"presilha|piranha|scrunchie|xuxinha|tiara", "presilha"), (r"\bcintos?\b", "cinto")]


def tipo_repetivel(o: dict) -> str | None:
    """Tipo de produto que não pode se repetir muito no dia (04/10: a linha de beleza também — "cílios 900 vezes")."""
    t = o.get("titulo") or ""
    lista = TIPO_BELEZA if o.get("grupo") in LINHA_PRINCIPAL else TIPO_DIA
    return next((nome for rx, nome in lista if re.search(rx, t, re.I)), None)


# 04/10 (dona: "cílios postiços 900 vezes, tá muito repetitivo") — subtipos da beleza, no máx. TETO_TIPO por dia
TIPO_BELEZA = [(r"m[áa]scara (?:de |para )?c[íi]lios|r[íi]mel", "rímel"), (r"c[íi]lios", "cílios"),
               (r"unhas? posti[çc]|tips? de unha|press ?on", "unha postiça"), (r"esmalte", "esmalte"),
               (r"pinc[ée]is|pincel", "pincel"), (r"esponja", "esponja"), (r"delineador", "delineador"),
               (r"l[áa]pis", "lápis"), (r"batom", "batom"), (r"gloss|lip ?oil|lip ?tint|tinta labial", "boca"),
               (r"corretivo", "corretivo"), (r"\bbase\b", "base"), (r"\bp[óo]\b", "pó"), (r"blush", "blush"),
               (r"paleta", "paleta"), (r"s[ée]rum", "sérum"), (r"protetor solar|fps ?\d", "protetor"),
               (r"hidratante", "hidratante"), (r"sabonete", "sabonete"), (r"m[áa]scara (?:facial|de argila)|sheet mask", "máscara facial"),
               (r"shampoo|condicionador", "shampoo"), (r"m[áa]scara (?:capilar|de hidrata)", "máscara capilar"),
               (r"[óo]leo|finalizador|leave", "finalizador"),
               (r"secador|chapinha|prancha|escova (?:secadora|rotativa)|modelador|babyliss", "aparelho de cabelo"),
               (r"body splash", "body splash")]
TETO_TIPO = {"bolsa": 10, "sandália": 6, "pijama": 6, **{nome: 4 for _, nome in TIPO_BELEZA}}  # 09/10: +sandália/pijama

# 04/10 (dona: "hoje só o melhor: beleza, cabelo, perfume, bolsa, bem feminino" — saíam barbear, coisa de criança,
# suplemento, máquina de cortar cabelo, camiseta/regata, relógio masculino, caneta depiladora) → modo FEMININO
# (só até config/achadinhos.json "so_feminino_ate"; depois volta tudo: moda, calçado, temas, Dia das Crianças)
MODA_FEMININA = re.compile(r"(?i)\bbolsas?\b|\bclutch\b|\btote\b|transversal|tiracolo|baguete|brincos?\b|\bcolar(?:es)?\b|"
                           # 04/10 (dona tirou um "anel" ridículo): anel só se for JOIA (material/estilo no título)
                           r"\ban(?:el|[ée]is)\b.*\b(?:prata|ouro|banhad|zirc[ôo]nia|cristal|p[ée]rola|solit[áa]rio|semijoia|"
                           r"feminino|a[çc]o inox)|bijuteria|semijoia|\bjoias?\b|gargantilha|choker|tornozeleira|"
                           # 05/10 (dona): acessório de luxo e pijama bonito de seda/cetim (estilo Victoria's Secret)
                           r"[óo]culos de sol|pulseira|bracelete|piranha de cabelo|presilha|\btiara\b|len[çc]o de seda|"
                           r"pijama.*\b(?:seda|cetim|satin|renda|luxo)|\b(?:seda|cetim|satin)\b.*pijama|"
                           r"(?:robe|camisola) de (?:seda|cetim)|"
                           # 06/10 (Eva, dona: "mostramos sapato lindo e o grupo não tem"): calçado feminino fofo
                           r"\bsapatilhas?\b|\bsand[áa]lias?\b|\bmules?\b|\btamancos?\b|scarpin|mary ?jane|rasteirinhas?|"
                           # 09/10 (dona: "vamos dar uma variada, colocando bolsa, sapato, acessório, essas coisas,
                           # pijama e tal") → 3 vagas de moda em 10: pijama/roupa de dormir de qualquer tecido, tênis/
                           # slide/bota/papete/anabela FEMININOS, relógio/cinto/óculos/corrente femininos, scrunchie
                           r"\bpijamas?\b|\bcamisolas?\b|short[- ]?doll|baby[- ]?doll|\brobes?\b|roupa de dormir|"
                           r"conjunto (?:de )?dormir|\brasteiras?\b|\bpapetes?\b|\banabela\b|"
                           r"\b(?:t[êe]nis|slides?|botas?|chinelos?|sapatos?|rel[óo]gios?|cintos?|[óo]culos|correntes?|"
                           r"carteiras?)\b[^|]{0,60}\bfeminin|feminin\w*\b[^|]{0,60}\b(?:t[êe]nis|slides?|botas?|sapatos?|"
                           r"rel[óo]gios?|cintos?|[óo]culos|correntes?|carteiras?)\b|scrunchies?|xuxinhas?|"
                           r"la[çc]os? de cabelo")
# 09/10: moda que NÃO é do grupo, mesmo feminina — lingerie sensual explícita, fantasia, roupa/tênis de academia e
# corrida (genérico de esporte), fantasia/cosplay, scrub hospitalar, chuteira/skate/ciclismo, guarda-chuva
# (pijama de amamentação fica: a dona mandou um em 09/10)
MODA_FORA = re.compile(r"(?i)sensual|\bsexy\b|er[óo]tic|lingerie|transparente|cinta[- ]liga|fantasia|fio dental|"
                       r"calcinha|suti[ãa]|academia|fitness|\blegging|treino|corrida|running|caminhada|crossfit|"
                       r"cosplay|peruca|cir[úu]rgic|hospitalar|\bscrub|futsal|chuteira|"
                       r"\bskate|ciclismo|meia ponta|guarda[- ]chuva|sombrinha")
# 09/10: a moda do grupo é bolsa, sapato, acessório e pijama — ROUPA comum (calça, saia, short "com cinto", vestido,
# macacão, conjunto) não entra pela palavra "cinto"/"colar" do título (o pijama "Conjunto Short e Blusa" entra)
ROUPA_RX = re.compile(r"(?i)\bcal[çc]as?\b|\bsaias?\b|\bshorts?\b(?![- ]?doll)|macac[ãa]o|macaquinho|vestidos?\b|"
                      r"\bblusas?\b|\bbody\b|cropped|regata|camiseta|jaqueta|blazer|biqu[íi]nis?|mai[ôo]\b|"
                      r"\bconjunto (?!de j[óo]ias|de (?:brincos?|colar))(?:feminino )?(?:linh|body|alfaiat|social)")
PIJAMA_RX = re.compile(r"(?i)\bpijamas?\b|\bcamisolas?\b|short[- ]?doll|baby[- ]?doll|\brobes?\b|roupa de dormir|"
                       r"conjunto (?:de )?dormir")
TIPOS_MODA = ("bolsa", "sapato", "acessorio", "pijama")


def tipo_moda(titulo: str) -> str:
    """09/10: a vaga de moda varia entre bolsa, sapato, acessório (joia, óculos, relógio, presilha, cinto) e pijama."""
    t = titulo or ""
    if PIJAMA_RX.search(t):
        return "pijama"
    if BOLSA.search(t) and not re.search(r"(?i)t[ée]rmica|marmita|lancheira|mochila|escolar|maternidade", t):
        return "bolsa"  # bolsa "de viagem/trabalho" é bolsa (NAO_BOLSA só tira a preferência da vaga "Mb")
    sap = SAPATO_FEM.search(t) or re.search(r"(?i)rasteirinhas?|babuches?|sapat[êe]nis|slingback|\bmules?\b", t)
    joia = JOIA_RX.search(t)  # "Corrente De Tênis" é joia; "Sandália com Pingente", sapato
    if sap and not (joia and joia.start() < sap.start()):
        return "sapato"
    return "acessorio"
# 07/10 (dona: "pode incluir coisas de casa bonito"): casa só o que ENFEITA — aroma, penteadeira, decoração, cama e banho,
# mesa posta (nada de ferramenta, limpeza, eletro ou cozinha de trabalho)
CASA_BONITA = re.compile(
    r"(?i)\bvelas? (?:arom[áa]ticas?|perfumadas?|decorativas?)|difusor(?:es)? (?:de (?:ambiente|aromas?|varetas)|arom)|"
    r"aromatizador|home spray|sach[êe]s? perfumados?|porta[- ]?j[óo]ias|"
    r"organizador(?:es)? (?:de )?(?:maquiagem|acr[íi]lico|penteadeira|cosm[ée]ticos|j[óo]ias|bijuterias?)|"
    r"espelho (?:de (?:mesa|maquiagem|parede|camarim)|decorativo|com (?:led|luz))|lumin[áa]ria|abajur|cord[ãa]o de luz|"
    r"fio de fada|\bvasos? (?:decorativos?|de cer[âa]mica|de vidro)|flores? (?:artificia|permanente)|\balmofadas?\b|"
    r"\bmanta\b|jogo de (?:cama|len[çc]ol|banho|toalhas?|ta[çc]as|x[íi]caras)|len[çc]ol (?:de )?(?:cetim|seda|percal)|"
    r"toalhas? (?:de banho|felpudas?|de rosto)|roup[ãa]o|\bcanecas?\b|\bx[íi]caras?\b|\bta[çc]as?\b|"
    r"bandeja (?:espelhada|decorativa)|quadros? decorativ|porta[- ]?retratos?|tapete (?:felpudo|shaggy|decorativo)|"
    r"cabides? de veludo|kit lavabo")
FORA_FEMININO = re.compile(r"(?i)barbear|barbeir|\bwahl\b|m[áa]quinas (?:de )?(?:cortar|corte|acabamento)|barbeador|\bbarbas?\b|p[óo]s[- ]barba|aparador|cortador de (?:cabelo|pelos)|"
                           r"m[áa]quina de (?:cortar|corte|acabamento)|navalha|depilador|caneta depil|infantil|crian[çc]a|"
                           # 09/10 noite (funil: beleza e moda boas caíam aqui por engano): "baby doll" é pijama (liberado
                           # em 09/10 — a busca "baby doll feminino" da Shopee era toda barrada), "baby hair"/"baby
                           # liss" são de cabelo; "proteção térmica" é protetor térmico capilar, não bolsa térmica
                           r"\bkids?\b|\bbeb[êe]s?\b|\bbaby\b(?![- ]?(?:doll|hair|liss))|\bmenin[oa]s?\b|suplement|"
                           r"c[áa]psulas|rel[óo]gio|smart ?watch|"
                           r"(?<!prote[çc][ãa]o )t[ée]rmica|marmita|mochila|escolar|maternidade|peniano|vibrat|er[óo]tic|"
                           r"cervical|elizabetano|"
                           r"an(?:el|[ée]is) (?:de|para) (?:veda|silicone|borracha|cortina|guardanapo|pist|celular|chaveiro)|"
                           # 05/10 (dona: "as promoções estão péssimas"): piercing de mamilo, pescoceira de lavatório,
                           # cílios de atacado/fio a fio de salão, vitamina e aparador de nariz saíram no grupo
                           r"piercing|mamilo|pescoceira|lavat[óo]rio|atacado|premade|\bf[ãa]s\b|"
                           r"extens(?:[ãa]o|[õo]es) d[ae] (?:pestana|c[íi]lios)|vitamina|polivitam|nariz|brinquedo|boneca|pel[úu]cia|"
                           r"lego|massinha|slime")
# 06/10 (Beto, Rita e revisora da nuvem, 05–06/10): passavam no grupo com categoria "beleza"/"perfume" — suplemento e
# alimento (L-Arginina, pré-treino, ômega 3, fibras P&P FIT, bebida de amêndoa), higiene bucal (creme dental, escovas
# Colgate, enxaguante), lava-roupas/sabão "Perfume", desodorante masculino e PROMESSA de tratamento ("remove melasma").
FORA_NICHO_GRUPO = re.compile(
    # 09/10 (dona: "o que foi aquela cola de prótese capilar? não tem nada a ver com a nossa coisa"): prótese (capilar,
    # dentária) e cola/fita de fixação de peruca/mega hair/lace = produto técnico, fora do público "quem ama comprar"
    r"(?i)pr[óo]tese|\bcola\b[^|]{0,40}(?:mega ?hair|peruca|lace|wig)|fita adesiva[^|]{0,30}(?:peruca|pr[óo]tese|lace)|"
    # 09/10 noite (1ª rodada da busca por TIPO na Shopee trouxe, aprovados pelas regras de então): shampoo de piolho,
    # óleo de massagem "sensual tântrica", shampoo de pet shop na categoria perfume, broca/mandril de manicure (técnico,
    # como a cabine UV que a revisora vetou), lixador de calos/"creme de pedreiro" e clareador de axila/virilha
    r"piolhos?|l[êe]ndeas?|t[âa]ntric|massagem (?:sensual|er[óo]tica)|pet ?shop|para (?:c[ãa]es|cachorros?|gatos?|pets?)\b|"
    r"\bbrocas?\b|mandril|\bcalos?\b|calosidade|rachadur|\bpedreiro\b|"
    r"clareador\w* (?:de |para |das? |d[eo]s? )?(?:axilas?|virilha)|(?:axilas?|virilha)\b[^|]{0,45}\bclare|"
    r"escovas? (?:de )?dent|escova dental|creme dental|pasta de dente|fio dental|enxaguante|antiss[ée]ptico bucal|"
    r"clareamento dental|irrigador|lava[- ]?roupas?|sab[ãa]o (?:l[íi]quido|em p[óo]|em barra|de coco)|amaciante|"
    r"detergente|desinfetante|alvejante|tira[- ]?manchas|"
    # 07/10 (Rita): suplemento esportivo entrava como "beleza" (gel energético, beta alanina)
    r"gel energ[ée]tico|beta[- ]?alanina|creatina|\bwhey\b|pr[ée][- ]?treino|\bbcaa\b|termog[êe]nico|"
    # 06/10 (auditoria do crescimento): utilitário não é "achadinho de beleza" — cola de sapato, item de enfermagem,
    # firmador de seios
    # 07/10 (revisão): "Coca-Cola" (batom/hidratante labial da Carmed/Bruna Tavares) não é cola; cola de cílios/unha/
    # peruca escrita de outro jeito ("+ 1 Cola", "Cola Alongamento de Cílios") passa em fora_do_nicho_grupo()
    r"(?<!coca-)(?<!coca )\bcola\b(?! (?:de|para) (?:c[íi]lios|unhas?|peruca|lace))|adesivo (?:de|para) (?:reparo|sapat)|"
    r"reparo de sapat|enfermagem|firmador de seios|"
    # 06/10 tarde (Rita/Beto): passaram como beleza/cabelo — aspirador "antiqueda", estetoscópio, Gillette, fralda/Tena,
    # íntimo, foot spa, kit/cadeira de banho, cadeira de camping, massageador de corpo, paçoca, absorvente, magnésio
    r"aspirador|estetosc[óo]p|otosc[óo]p|gillette|\btena\b|fraldas?\b|sabonete [íi]ntimo|foot ?spa|kit (?:de )?banheiro|"
    # 09/10 noite: "Escova Massageadora Capilar"/"Massageadora Facial" caíam aqui (a exceção só valia para "massageador ")
    r"cadeira (?:de )?(?:banho|camping|praia)|"
    r"massageador(?!(?:a|as|es)? (?:facial|de rosto|gua ?sha|de couro cabeludo|capilar))|pa[çc]o(?:c|qu)|"
    r"absorvente|anabolic|magn[ée]sio|suportes? (?:de|para) shampoo|"
    r"[ôo]mega ?3|arginina|pr[ée][- ]?treino|creatina|\bwhey\b|termog[êe]nic|\bfibras? (?:sol[úu]vel|alimentar|em p[óo])|"
    r"p&p fit|\bbebida\b|leite (?:de am[êe]ndoa|em p[óo])|\d+ ?mg\b|\bcaps\b|softgel|comprimidos?\b|"
    r"col[áa]geno (?:hidrolisado|em p[óo]|verisol)|old spice|\baxe\b|for men\b|\bmen\b|"
    # 06/10 noite (Rita/Beto): ainda passavam — lancheira, gel/creme dental e escova interdental, pomada de assadura,
    # lâmina de barbear/de reposição, íntimo (sabonete, protetor diário Intimus) e kit de REVENDA ("+ tag Mimo para cliente")
    r"lancheira|bolsa t[ée]rmica|dental|dentek|assaduras?|"
    r"l[âa]minas? (?:de )?(?:barbear|reposi[çc][ãa]o|corte|acabamento|depila\w*)|l[âa]minas? (?:philips|wahl|kemei|\d+ ?mm)|"
    r"oneblade|(?<!moda )\b[íi]ntim[oa]s?\b|intimus|protetor di[áa]rio|"
    r"mimos? (?:para|pra|p/) (?:clientes?|revenda)|\brevenda\b|\btags? (?:mimo|para (?:clientes?|bijuteria|brincos?))|"
    r"\bkit \d+\s*,\s*\d+|"
    r"\b(?:remove|remover|removedor de|remo[çc][ãa]o d[eao]s?|elimina|eliminar|acaba com|some com|cura|curar)\b\s+"
    r"(?:\w+\s+){0,3}?"
    r"(?:melasma|manchas?|cicatriz(?:es)?|estrias?|acne|espinhas?|celulite|olheiras?|verrugas?|pintas?|rugas?|poros?)|"
    r"clareamento (?:intenso|de manchas|[íi]ntimo|de axila|de virilha)|"
    # 08/10 noite (Beto/Rita 07/10, 4 dias seguidos): ainda entravam como "beleza" — suplemento (glutamina, colágeno
    # em pó/pote/sachê, "Colágenos Verisol"), bandagem/atadura, corretor postural e móvel/carrinho de salão
    r"glutamina|maltodextrina|\btaurina\b|col[áa]genos? (?:hidrolisado|em p[óo]|verisol|tipo|tri plus|pura vida|profit)|"
    r"bandage[mn]s?|ataduras?|kinesio|thumb tape|esparadrapo|corretor(?:es)? (?:de )?postur|colete (?:postural|corretor)|"
    r"\bcarrinhos? (?:auxiliar|de sal[ãa]o|para sal[ãa]o|de cabel|de manicure|de est[ée]tica|organizador)|"
    r"m[óo]veis (?:bonatto|para sal[ãa]o)|\bmacas?\b (?:de |para )?(?:est[ée]tica|massagem)|"
    r"cadeiras? (?:de |para )?(?:cabel|barb|est[ée]t|manicure|sal[ãa]o)|lavat[óo]rio|autoclave|"
    r"suporte (?:de parede|para (?:secador|chapinha|prancha))|organizador de sal[ãa]o")
# colágeno de BEBER sem dizer o tipo ("Colágeno Pura Vida em pó sabor Chocolate 450g", "1 Pote Colágeno…") — o de
# creme/sérum/máscara ("Gota de Colágeno Kokeshi", "Creme… com Colágeno 350g", "Elseve Collagen") é cosmético e fica
COLAGENO_SUPLEMENTO = re.compile(r"(?i)col[áa]genos?\b.*?(?:\bsabor\b|\bem p[óo]\b|\bpotes?\b|sach[êe]s?\b|c[áa]psulas?|"
                                 r"comprimidos?|\bcaps\b)|\bpotes?\b.*?col[áa]geno")
COLAGENO_COSMETICO = re.compile(r"(?i)creme|s[ée]rum|loção|lo[çc][ãa]o|gel\b|m[áa]scara|shampoo|condicionador|leave|"
                                r"hidratante|facial|gota|ampola|t[ôo]nico")
# vitamina C/E em sérum, creme ou ampola é skincare (dermo = nicho); "vitamina" em cápsula/goma é suplemento (fica fora)
VITAMINA_SKINCARE = re.compile(r"(?i)s[ée]rum|facial|creme|ampola|booster|\bpele\b|rosto|t[ôo]nico|\bgel\b|"
                               r"hidratante|antioxidante|skin ?care")
COLA_DE_BELEZA = re.compile(r"(?i)c[íi]lios|\bunhas?\b|peruca|\blace\b|\bwig\b|\btips?\b|posti[çc]")


def fora_do_nicho_grupo(titulo: str) -> bool:
    """FORA_NICHO_GRUPO, menos a cola de cílios/unha/peruca (07/10: "New Show Cílios… + 1 Cola", "Cola Alongamento de
    Cílios", "Cola Ultra Hold Lace Wig" eram barradas como cola de sapato)."""
    if COLAGENO_SUPLEMENTO.search(titulo or "") and not COLAGENO_COSMETICO.search(titulo or ""):
        return True
    return any(not (m.group(0).lower() == "cola" and COLA_DE_BELEZA.search(titulo))
               for m in FORA_NICHO_GRUPO.finditer(titulo or ""))


SO_FEMININO_ATE = "2099-12-31"  # dona 03/10 e 05/10 ("promoções péssimas"): só o público dela; sem infantil/eletrônico/casa


def so_feminino_ligado() -> bool:
    return date.today().isoformat() <= str(canais().get("so_feminino_ate") or SO_FEMININO_ATE)


def no_perfil_feminino(o: dict) -> bool:
    """Beleza, cabelo e perfume femininos + bolsa e bijuteria. Roupa, casa, eletrônico, infantil e masculino ficam fora."""
    t = o.get("titulo") or ""
    fora = [m.group(0).lower() for m in FORA_FEMININO.finditer(t)]
    # 06/10 (coordenação): "vitamina" barrava sérum/creme de vitamina C (dermo, nosso nicho) — o site já liberava
    # 09/10 noite: título com a palavra 2 vezes ("Principia Vitamina C - Sérum com 10% de Vitamina C") continuava
    # barrado — a 2ª "vitamina" contava como outro motivo. Agora só barra se sobrar motivo que NÃO seja a exceção.
    if VITAMINA_SKINCARE.search(t):
        fora = [x for x in fora if x != "vitamina"]
    # 09/10 (dona: acessório no mix): relógio FEMININO de pulso é acessório (smartwatch continua fora)
    if o.get("grupo") == "moda" and re.search(r"(?i)feminin", t):
        fora = [x for x in fora if not re.fullmatch(r"rel[óo]gio", x)]
    # 09/10 noite: piranha/presilha/tiara "de pelúcia" é acessório de cabelo, não brinquedo
    if re.search(r"(?i)piranha|presilha|\btiara\b|scrunchie|xuxinha|prendedor de cabelo", t):
        fora = [x for x in fora if not re.fullmatch(r"pel[úu]cia", x)]
    if fora or fora_do_nicho_grupo(t) or eh_masculino(o):
        return False
    return (o.get("grupo") in LINHA_PRINCIPAL
            or (o.get("grupo") == "moda" and bool(MODA_FEMININA.search(t)) and not MODA_FORA.search(t)
                and (tipo_moda(t) != "acessorio" or not ROUPA_RX.search(t)))
            or (o.get("grupo") == "casa" and bool(CASA_BONITA.search(t))))


def barrada_na_hora(o: dict) -> bool:
    """04/10: regras novas valem também para o que JÁ estava aprovado no banco (o servidor posta da fila antiga)."""
    t = o.get("titulo") or ""
    return (de_inflado(o) or fora_do_perfil(t) or titulo_ruim(t)
            or perfume_grife_sem_ml(o) or preco_proibido(o) or farmacia_shopee_comum(o)
            # 08/10 noite: linha masculina e fora do nicho saem SEMPRE (antes só no modo feminino e só pela palavra
            # "masculino" — Ralph's Club/Malbec/Eudora H passavam), validade só na foto (Océane), título sem o tipo
            # do produto e kit de salão da Shopee comum com "De" de vitrine
            or eh_masculino(o) or fora_do_nicho_grupo(t) or validade_na_foto(o) or titulo_sem_tipo(o)
            or kit_salao_shopee(o))


# 08/10 (Beto: Océane oce:61508 e oce:81863 com "VALIDADE PRÓXIMA JAN/2027" só na foto): a foto da loja vem com o mês
# de validade no nome do arquivo ("…/ids/218495/JAN27_0001s_…Objeto Inteligente…jpg") — vale para o que JÁ está no
# banco (coletado antes da regra do oceane.avaliar) e para qualquer fonte
VALIDADE_FOTO = re.compile(r"(?i)/(?:JAN|FEV|FEB|MAR|ABR|APR|MAI|MAY|JUN|JUL|AGO|AUG|SET|SEP|OUT|OCT|NOV|DEZ|DEC)\d{2}_|"
                           r"objeto(?:%20|[-_ ])+inteligente|validade(?:%20|[-_ ])*pr[óo]xima")


def validade_na_foto(o: dict) -> bool:
    return bool(VALIDADE_FOTO.search(str(o.get("foto") or "")))


# 08/10 (Rita: "Eudora Rouge Kit (2 Produtos)", "Kit La Violette O.U.i Feminino (2 Produtos)"): título que só diz
# marca + linha não diz O QUE é. Perfume sai com "Perfume" na frente (a categoria diz); beleza/cabelo sem o tipo, não sai.
TIPO_PRODUTO = re.compile(
    r"(?i)perfum|parfum|eau de|toilette|cologne|col[ôo]nia|\bedp\b|\bedt\b|splash|\bmist\b|bruma|fragr|"
    r"batom|gloss|\blips?\b|labial|l[áa]bios|balm|baume|b[áa]lsamo|lipstick|\btint|\bbase\b|foundation|corretivo|"
    r"concealer|\bp[óo]\b|powder|primer|blush|bronzer|iluminador|highlighter|contorno|sombras?|paleta|palette|"
    r"delineador|eyeliner|l[áa]pis|pencil|r[íi]mel|mascara|m[áa]scara|c[íi]lios|pestanas|sobrancelha|brow|esmalte|"
    r"unhas?\b|nail|pinc[ée]is|pincel|brush|esponja|cushion|fixador|setting|s[ée]rum|serum|essence|ess[êe]ncia|ampola|"
    r"ampoule|booster|creme|cream|lo[çc][ãa]o|lotion|hidratante|moisturi|emuls|\bgel\b|gelatina|espuma|foam|sabonete|"
    r"soap|cleans|limpeza|t[ôo]nico|toner|[áa]gua|micelar|\bh2o\b|demaquil|[óo]leo|\boils?\b|protetor|fps|spf|"
    r"sun ?(?:cream|screen|stick)|suncream|esfolia|peeling|exfoli|scrub|argila|clay|patch|pads?\b|strips?|mask|sheet|"
    r"desodorante|antitranspirante|shampoo|xampu|condicionador|conditioner|leave|finalizador|fluido|mousse|spray|"
    r"pomada|\bwax\b|\bcera\b|tintura|colora[çc]|descolor|tonaliz|matizad|oxidante|reparador|ativador|definidor|"
    r"alisa|relaxa|tratamento|treatment|reconstru|cronograma|capilar|cabelos?\b|\bfios\b|\bpele\b|facial|rosto|"
    r"acidificante|antiqueda|elseve|"
    r"corporal|\bmilk\b|secador|chapinha|prancha|escova|modelador|babyliss|difusor|pente|presilha|piranha|tiara|"
    r"maquiagem|makeup|\bmake\b|skin ?care|rotina|stick|bast[ãa]o|vela|sais de banho|sach[êe]|[áa]cido|retinol|retinal|"
    r"niacinamida|vitamina|bb ?cream|cc ?cream|aplicador|curvex|pin[çc]a|lixa|depila|\brolo\b|gua ?sha|massageador|"
    # 09/10 noite: "Kit 5 Manicure Alicate Slim Palito…" e "Kit 2 Touca De Cetim…" dizem o que são e eram barrados
    r"manicure|pedicure|alicate|cut[íi]cula|\btoucas?\b|"
    r"espelho|necessaire|organizador|\bporta\b|bolsa|clutch|brincos?|colar|pulseira|\banel\b|joia|rel[óo]gio|"
    r"sapat|sand[áa]lia|t[êe]nis|[óo]culos|aparador|m[áa]quina|barbeador|kit (?:de )?(?:maquiagem|make|skincare|"
    r"cuidados?|capilar|facial|corporal|banho|presente|viagem|travel)")
PERFUMARIA = re.compile(r"(?i)eudora|botic[áa]rio|\bnatura\b|\bavon\b|jequiti|o\.?u\.?i\b|la violette|lattafa|"
                        r"al wataniah|maison alhambra|armaf|afnan|\bmahogany\b")


def titulo_sem_tipo(o: dict) -> bool:
    t = o.get("titulo") or ""
    if o.get("grupo") not in LINHA_PRINCIPAL or TIPO_PRODUTO.search(t) or MARCAS_SKINCARE.search(t):
        return False
    return tipo_completado(o) is None


def tipo_completado(o: dict) -> str | None:
    """Título sem o tipo → "Perfume …" quando a categoria (ou marca de perfumaria com ml, sem ser kit) diz que é
    perfume; senão None. Título que já diz o tipo volta igual."""
    t = o.get("titulo") or ""
    if TIPO_PRODUTO.search(t) or MARCAS_SKINCARE.search(t):
        return t
    if o.get("grupo") == "perfume" or (PERFUMARIA.search(t) and re.search(r"(?i)\d+\s?ml\b", t)
                                        and not re.search(r"(?i)\bkit\b|produtos?\b|\bcombo\b", t)):
        return f"Perfume {t}"
    return None


# 08/10 (Beto: kit Marco Boni De 326,65/Por 184 na Shopee × Raia 159,85 o mesmo kit): kit/acessório de SALÃO (escova,
# pente, tesoura, alicate, bobes) em loja comum da Shopee com mais de 40% OFF = "De" de vitrine. Não temos o preço da
# farmácia/Raia no código para comparar → sem loja oficial da marca, não sai.
KIT_SALAO = re.compile(r"(?i)marco boni|santa clara|\bdompel\b|\bbelliz\b|"
                       r"(?:\bkit\b|\bjogo\b|\bconjunto\b|\d+\s*(?:un|unid\w*|p[çc]s|pe[çc]as))\b.{0,40}?"
                       r"\b(?:escovas?|pentes?|tesouras?|alicates?|bobes|presilhas? de sal[ãa]o)\b|"
                       r"\b(?:escovas?|pentes?|tesouras?|alicates?)\b.{0,40}?\bprofissiona(?:l|is)\b")


def kit_salao_shopee(o: dict) -> bool:
    if not re.search(r"shopee|shope\.ee", f"{o.get('link_loja') or ''} {o.get('fonte') or ''}", re.I):
        return False
    p, de = o.get("preco") or 0, o.get("preco_antigo") or 0
    d = o.get("desconto") or (round(100 * (1 - p / de)) if de > p > 0 else 0)
    t = o.get("titulo") or ""
    if re.search(r"(?i)secador|secadora|prancha|chapinha|rotativa|alisadora|modeladora|el[ée]tric|bivolt|\d+ ?w\b", t):
        return False  # aparelho elétrico (escova secadora "profissional") não é kit de salão
    return d > 40 and bool(KIT_SALAO.search(t)) and not loja_oficial_da_marca(o)


# 08/10 noite (Rita: "✔️ Loja oficial" em Medicube/Missha da koksara.kbeauty — Shopee Mall, mas REVENDEDORA): o selo só
# sai quando a loja é oficial da MARCA do produto — loja da própria marca (Océane) ou Shopee Mall/feed oficial com o
# nome da loja batendo com o título. Na dúvida (sem nome de loja, loja multimarca), sem selo.
GENERICO_LOJA = {"oficial", "official", "oficiais", "loja", "store", "shop", "flagship", "brasil", "brazil", "br", "the",
                 "de", "da", "do", "e", "ltda", "online", "ofc", "mall", "skincare", "cosmeticos", "cosmetics", "beauty",
                 "beleza", "makeup", "make", "joias", "joalheria", "moda", "s925", "comercio", "distribuidora", "oficia",
                 # nome de loja multimarca/revendedora ("Amor de Make", "Mix Beleza", "Grupo Elian", "Lojas REDE",
                 # "Beleza na Web", "Beleza Renovada", "Poderosa Beleza") não é marca de produto
                 "amor", "mix", "lojas", "grupo", "rede", "web", "renovada", "poderosa", "mundo", "casa", "center",
                 "mega", "super", "top", "kbeauty", "kbeleza", "importados", "import", "atacado", "variedades", "com"}


def _sem_acento(t: str) -> str:
    import unicodedata
    return unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode().lower()


def loja_oficial_da_marca(o: dict) -> bool:
    s = sinais_de(o)
    if not s.get("oficial"):
        return False
    titulo = _sem_acento(o.get("titulo") or "")
    marca = _sem_acento(str(s.get("marca") or ""))
    if marca and marca == _sem_acento(o.get("loja") or ""):
        return True  # loja da própria marca (Océane: o site é da marca)
    # "Centersportoficial", "Eudora_Oficial": o "oficial" grudado sai
    palavras = [w for w in (re.sub(r"(?:oficial|official|ofc)$", "", x)
                            for x in re.findall(r"[a-z0-9]+", _sem_acento(str(s.get("loja_nome") or ""))))
                if w not in GENERICO_LOJA and len(w) >= 3]
    if not palavras:
        return False
    junto, tit_junto = "".join(palavras), re.sub(r"[^a-z0-9]", "", titulo)
    toks = set(re.findall(r"[a-z0-9]+", titulo))
    # "PhálleBeauty Oficial" × "Primer PhálleBeauty"; "New Show Oficial" × "New Show Cílios"; "Loja oficial Kemei" ×
    # "Kemei …"; "Vnox Official Flagship Store" × "Vnox Pulseira…"
    return (len(junto) >= 4 and junto in tit_junto) or (len(palavras[0]) >= 4 and palavras[0] in toks)


# 08/10 (Beto, 2ª vez: Avène "produto importado" R$ 50 e Cicalfate 20 ml na Raitz Beauty com De 2× exato; Sunless De
# 99,90 × Drogasil 63; Hidraderm De 34,69 × 21,99): dermo importado e marca de FARMÁCIA em loja comum da Shopee vêm com
# o "De" inflado (e às vezes sem garantia de origem) → na Shopee só da loja OFICIAL
DERMO_IMPORTADO = re.compile(
    r"(?i)(?<!\w)(?:av[èe]ne|la roche|bioderma|vichy|cerave|eucerin|isdin|uriage|ducray|sesderma|cetaphil|skinceuticals|"
    r"caudalie|nuxe|filorga|svr|a-derma|neostrata|noreva|cicalfate|effaclar|anthelios|cicaplast|sensibio|atoderm|"
    r"hyalu b5|mineral 89|lipikar|toleriane)(?!\w)")
MARCA_FARMACIA = re.compile(
    r"(?i)(?<!\w)(?:sunless|hidraderm|episol|asepxia|catharine hill|mavala|neutrogena|nivea|darrow|adcos|dermage|"
    r"mantecorp|bepantol|bepantriz|hipoglos|minancora|epidrat|cetrilan|fisiogel|actine|tracta|ricca|depimiel|tabu)(?!\w)")


def farmacia_shopee_comum(o: dict) -> bool:
    link = f"{o.get('link_loja') or ''} {o.get('fonte') or ''}"
    if not re.search(r"shopee|shope\.ee", link, re.I) or sinais_de(o).get("oficial"):
        return False
    t = o.get("titulo") or ""
    return bool(DERMO_IMPORTADO.search(t) or MARCA_FARMACIA.search(t))


# 07–08/10 (Rita, 5ª rodada seguida): o filtro "sem preço 'De'" só olhava o número — mas a legenda ESCONDE o "De" que
# não é confiável (loja de "De" fixo/inflado, koksara, Por já no preço de mercado) → o post saía só com "Por" (Medicube,
# Vnox, Missha, Kokeshi…). Regra da dona: sem "De" maior que o "Por" NA LEGENDA, não vai ao grupo (nem pedido)
def tem_de_no_post(o: dict) -> bool:
    p, de = o.get("preco") or 0, o.get("preco_antigo") or 0
    return p > 0 and de > p and de_confiavel(o)


# 07/10 (Beto, 7× em 06–08/10: Taiff ML de 29/09, Mondial 04/10, fwee 02/10): preço com mais de 24 h não entra na fila
# nem volta. "Visto" = a última vez que a fonte mostrou ESSE preço: atualizado_em (Shopee/ML/Amazon/Época/Océane regravam
# o preço a cada coleta); na Promobit o preço é o do POST da comunidade → visto_em. Amazon (6 h), Magalu/Época (12 h) e
# Océane (OCEANE_VALIDADE_H) têm validade própria, mais curta.
PRECO_VALE_H = 24


def preco_visto_em(o: dict) -> str:
    if o.get("fonte") == "promobit":
        return str(o.get("visto_em") or o.get("atualizado_em") or "")
    return str(o.get("atualizado_em") or o.get("visto_em") or "")


def preco_fresco(o: dict, agora: datetime | None = None) -> bool:
    visto = preco_visto_em(o)
    if not visto:
        return True  # sem data nenhuma (teste/linha manual): quem decide é a validade da fonte
    agora = agora or datetime.now()
    h = PRECO_VALE_H
    link = o.get("link_loja") or ""
    if re.search(r"amazon\.com\.br|amzn\.to", link, re.I):
        h = min(h, 6)
    if o.get("fonte") == "magalu_epoca":
        h = min(h, 12)
    if o.get("fonte") == "oceane_afiliados":
        h = min(h, OCEANE_VALIDADE_H)
    return visto.replace("T", " ") >= (agora - timedelta(hours=h)).isoformat(sep=" ", timespec="seconds")


# 08/10 (Beto: Laneige Lip Glowy Balm saiu 06/10 e voltou 08/10 pela vaga da grife, que deixava voltar em 2 dias): o
# MESMO produto (mesmo id, mesmo item da loja — ASIN/MLB/item Shopee — ou o mesmo título) não sai de novo em REPETIR_DIAS
REPETIR_DIAS = 7


def _norm_titulo(t: str) -> str:
    import unicodedata
    t = unicodedata.normalize("NFKD", limpar_titulo(t or "").lower()).encode("ascii", "ignore").decode()
    return " ".join(re.findall(r"[a-z0-9]+", t))


def publicados_recentes(dias: int = REPETIR_DIAS) -> dict:
    """{"ids", "chaves", "titulos"} do que saiu no grupo nos últimos `dias`."""
    from vendas import revisao_fila
    ids, chaves, titulos = set(), set(), set()
    for r in db.consultar("SELECT id, titulo, link_loja FROM ofertas WHERE publicado_em >= "
                          f"datetime('now','localtime','-{int(dias)} days')") or []:
        ids.add(r["id"])
        if c := revisao_fila.chave_item(r["id"] or "", r["link_loja"]):
            chaves.add(c)
        if r["titulo"]:
            titulos.add(_norm_titulo(r["titulo"]))
    return {"ids": ids, "chaves": chaves, "titulos": titulos}


def ja_saiu(o: dict, pub: dict) -> bool:
    from vendas import revisao_fila
    return (o.get("id") in pub["ids"] or revisao_fila.chave_item(o.get("id") or "", o.get("link_loja")) in pub["chaves"]
            or _norm_titulo(o.get("titulo") or "") in pub["titulos"])


# 06/10 (Beto): perfume de GRIFE sem o tamanho no título (My Way 305/De 569, Idôle, La Vie Est Belle, Chloé, Light Blue
# da Amazon/Promobit/Magalu/Época) com mais de 40% OFF — sem o ml não dá para comparar o "De" com o mercado (o "De" pode
# ser do frasco grande e o "Por" do pequeno) → não sai
PERFUME_GRIFE = re.compile(
    r"(?i)(?<!\w)(?:lanc[ôo]me|armani|dior|chanel|carolina herrera|givenchy|ysl|yves saint|prada|paco rabanne|rabanne|"
    r"jean paul gaultier|azzaro|hugo boss|versace|dolce\s*(?:&|e|and)\s*gabbana|d&g|burberry|gucci|valentino|bvlgari|"
    r"bulgari|tiffany|cartier|montblanc|mont blanc|chlo[ée]|marc jacobs|calvin klein|tommy hilfiger|lacoste|kenzo|"
    r"narciso rodriguez|issey miyake|mugler|tom ford|jo malone|guerlain|herm[èe]s|cacharel|nina ricci|ralph lauren|"
    r"michael kors|victoria.?s secret|id[ôo]le|my way|la vie est belle|la nuit tr[ée]sor|light blue|good girl|212|"
    r"olymp[ée]a|black opium|libre|j'?adore|miss dior|coco mademoiselle|flowerbomb|scandal|invictus|1 million|"
    r"lady million|acqua di gi[oò]i?a?|si passione|alien|daisy|euphoria|sauvage|amor amor)(?!\w)")
TEM_TAMANHO = re.compile(r"(?i)\d+(?:[.,]\d+)?\s*(?:ml|l)\b|\d+(?:[.,]\d+)?\s*fl\.?\s*oz")


def perfume_grife_sem_ml(o: dict) -> bool:
    t = o.get("titulo") or ""
    if not (o.get("grupo") == "perfume" or PERFUME_RX.search(t)) or not PERFUME_GRIFE.search(t) or TEM_TAMANHO.search(t):
        return False
    p, de = o.get("preco") or 0, o.get("preco_antigo") or 0
    d = o.get("desconto") or (round(100 * (1 - p / de)) if de > p > 0 else 0)
    return d > 40


# 06/10 (Beto): o programa da Vivara (Awin) PROÍBE divulgar preço/parcela, "promoção" e % OFF — e todo post do grupo e
# card do site tem "Por R$" → oferta Vivara (a Promobit traz o link da vivara.com.br) não sai em lugar nenhum
SEM_PRECO = re.compile(r"(?i)vivara")


LIMITE_CANDIDATOS = 8000  # quantas aprovadas (por score) entram no funil do grupo


def preco_proibido(o: dict) -> bool:
    return bool(SEM_PRECO.search(" ".join(str(o.get(k) or "") for k in ("loja", "link_loja", "titulo"))))


POUCO_ESTOQUE = 30
# 07/10 (dona: "quero essas ofertas no nosso… pode por o fone… o pote não foi errado" e "acha oferta boa que foi sem
# minha etiqueta e põe de novo"): oferta que a DONA pediu passa por cima das regras de GOSTO (aprovação/desconto,
# perfil feminino, teto por tipo, tema do dia) e vai na frente; as de SEGURANÇA continuam (link e foto ≥ 600 px, veto,
# vencendo, preço proibido, Amazon com preço ≤ 6 h). Vale 24 h; a que já tinha saído ANTES do pedido volta uma vez.
# Arquivo {id: {"quando": iso, "grupo"?: "moda", "motivo"?: "..."}} — viaja ao servidor com o estado (nuvem.ARQS_ESTADO).
PEDIDOS_DONA = config.DADOS / "achadinhos" / "pedidos_dona.json"


def pedidos_dona(horas: int = 24) -> dict:
    try:
        d = json.loads(PEDIDOS_DONA.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    corte = (datetime.now() - timedelta(hours=horas)).isoformat(timespec="seconds")
    return {k: v for k, v in d.items() if isinstance(v, dict) and str(v.get("quando", "")) >= corte}


def _pedidos_da_dona(amazon_ok: str) -> list[dict]:
    ped = pedidos_dona()
    if not ped:
        return []
    from vendas import revisao_fila
    vet = revisao_fila.vetados()
    chaves_vet = revisao_fila.chaves_vetadas(vet)
    oceane_ok = (datetime.now() - timedelta(hours=OCEANE_VALIDADE_H)).isoformat(sep=" ", timespec="seconds")
    out = []
    for r in db.consultar(f"SELECT * FROM ofertas WHERE id IN ({','.join('?' * len(ped))})", tuple(ped)):
        o, p = dict(r), ped[r["id"]]
        if o.get("publicado_em") and o["publicado_em"] >= str(p["quando"]).replace("T", " "):
            continue  # já saiu depois do pedido
        # 08/10 (Rita/Beto): pedido também precisa do "De" na legenda e de preço visto há ≤ 24 h (regra da dona)
        if not tem_de_no_post(o) or not preco_fresco(o):
            continue
        if (not (o.get("link_loja") and o.get("foto")) or vencendo(o.get("titulo") or "") or preco_proibido(o)
                or validade_na_foto(o)  # 08/10 noite: validade próxima só na foto (Océane) também não vai a pedido
                or revisao_fila.vetada({**o, "pedido_dona": True}, vet, chaves_vet)  # 08/10: veto de frase não barra
                or (re.search(r"amazon\.com\.br|amzn\.to", o["link_loja"], re.I) and (o.get("atualizado_em") or "") < amazon_ok)
                # 08/10: Océane com preço velho ou que saiu da promoção (sinais "fora") não vai nem a pedido
                or (o.get("fonte") == "oceane_afiliados" and ((o.get("atualizado_em") or "") < oceane_ok
                                                              or sinais_de(o).get("fora")))):
            continue
        o.update(publicado_em=None, pedido_dona=True, **({"grupo": p["grupo"]} if p.get("grupo") else {}))
        out.append(o)
    # 09/10: na ordem do ARQUIVO (o `pedido` da dona entra no começo dele = vai primeiro); antes era a ordem do banco
    pos = {k: i for i, k in enumerate(ped)}
    out.sort(key=lambda o: pos.get(o["id"], len(pos)))
    # 09/10 (dona: Océane abaixo de R$ 90 sempre primeiro): entre os pedidos da Océane vale a "ordem" da Odete
    # (integracoes/oceane.escolher_pedidos); os das outras lojas ficam onde estavam
    pos_oce = [i for i, o in enumerate(out) if o.get("fonte") == "oceane_afiliados"]
    for i, o in zip(pos_oce, sorted((out[i] for i in pos_oce),
                                    key=lambda o: (int(ped[o["id"]].get("ordem") or 10 ** 6), o["id"]))):
        out[i] = o
    return out


# ---------------- pedido da dona pelo WhatsApp da loja (09/10) ----------------
# A dona manda promoções na conversa dela com o WhatsApp da loja, quase sempre com o meli.la de OUTRA pessoa. A Central
# de Afiliados do ML só acha parte delas (4 de 6 ficaram de fora em 09/10), mas o NOSSO link (link_afiliado: anúncio +
# matt_word/matt_tool) vale para QUALQUER anúncio. `vendas achadinhos pedido URL|MLB [--texto "gancho"]`:
#   meli.la → só os cabeçalhos Location (encurtador) → anúncio direto, ou /social/<pessoa> (o caminho: os MLB em ordem,
#   o 1º é o produto) → página PÚBLICA do anúncio (1 requisição) → radar (aprovada, NOSSO link) → pedidos_dona.json
#   no COMEÇO. 1 página de produto por pedido, sem varrer; ML pediu verificação → pausa ML_PAUSA_H (nada de contornar)
#   e o pedido fica em pedidos_pendentes.json (só o MLB — o link DELES nunca é guardado nem postado).
# Shopee (s.shopee/shopee.com.br) → shopee_referencia.pela_api (Open API, gera o nosso s.shopee).
ML_PAUSA = config.DADOS / "achadinhos" / "ml_pausa.json"
ML_PAUSA_H = 6
PEDIDOS_PENDENTES = config.DADOS / "achadinhos" / "pedidos_pendentes.json"
MOTIVO_PEDIDO_WHATSAPP = "dona: pedido no WhatsApp da loja (achadinhos pedido)"
# anúncio = MLB + 6 a 11 dígitos; "-MLB110736447154_092026" (12 dígitos, depois de "-" e antes de "_") é FOTO do mlstatic
MLB_ANUNCIO = re.compile(r"(?<![\w-])MLB-?(\d{6,11})(?![\d_])")
PAREDE_ML = re.compile(r"account-verification|/gz/|captcha|/lgz/login|/jms/mlb/lgz|challenge", re.I)
CABECALHO_NAVEGADOR = {**UA, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}


class PedidoErro(RuntimeError):
    """Pedido que não deu para montar (motivo em português, para a saída do comando)."""


class MLBloqueado(PedidoErro):
    """O ML respondeu com verificação/captcha: parar o ML (é a dona quem resolve), não tentar de novo."""


def ml_pausado(agora: datetime | None = None) -> str | None:
    """Hora em que o ML pediu verificação, se ainda estiver dentro da pausa (senão None)."""
    try:
        d = json.loads(ML_PAUSA.read_text(encoding="utf-8"))
        desde = datetime.fromisoformat(d["desde"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return d["desde"] if (agora or datetime.now()) - desde < timedelta(hours=ML_PAUSA_H) else None


def _pausar_ml(onde: str) -> None:
    ML_PAUSA.parent.mkdir(parents=True, exist_ok=True)
    ML_PAUSA.write_text(json.dumps({"desde": datetime.now().isoformat(timespec="seconds"), "onde": onde},
                                   ensure_ascii=False), encoding="utf-8")


def _get_ml(url: str, cli: httpx.Client) -> str:
    """A ÚNICA página do ML que o pedido lê. Verificação/captcha → pausa e MLBloqueado (sem repetir)."""
    if desde := ml_pausado():
        raise MLBloqueado(f"ML em pausa desde {desde[11:16]} (pediu verificação; volto a tentar depois de "
                          f"{ML_PAUSA_H} h — quem resolve a verificação é a dona)")
    r = cli.get(url, headers=CABECALHO_NAVEGADOR, follow_redirects=True)
    if PAREDE_ML.search(str(r.url)) or r.status_code in (403, 429):
        _pausar_ml(str(r.url)[:80])
        raise MLBloqueado(f"ML pediu verificação ao abrir {url.split('?')[0]} — parei o ML por {ML_PAUSA_H} h")
    if r.status_code >= 400:
        raise PedidoErro(f"ML respondeu {r.status_code} em {url.split('?')[0]}")
    return r.text


def mlbs_no_html(html: str) -> list[str]:
    """MLB de ANÚNCIO na ordem em que aparecem (id de foto do mlstatic fica de fora), sem repetir."""
    return list(dict.fromkeys(f"MLB{m}" for m in MLB_ANUNCIO.findall(html or "")))


def _seguir_encurtador(url: str, cli: httpx.Client) -> str:
    """meli.la → destino lendo SÓ o cabeçalho Location (não abre a página do ML)."""
    from urllib.parse import urlparse
    for _ in range(5):
        if not any(e in urlparse(url).netloc for e in ENCURTADORES):
            break
        r = cli.get(url, headers=UA, follow_redirects=False)
        loc = r.headers.get("location")
        if not (300 <= r.status_code < 400 and loc):
            raise PedidoErro("o link curto não levou a lugar nenhum (expirado?)")
        url = loc if loc.startswith("http") else str(r.url.join(loc))
    return url


def resolver_pedido(alvo: str, cli: httpx.Client) -> dict:
    """{"loja": "ml", "mlb", "url", "html"?, "outros"?} ou {"loja": "shopee", "url"}. Link de terceiro é só caminho."""
    alvo = (alvo or "").strip()
    if re.fullmatch(r"(?i)MLB-?\d+", alvo):
        num = re.sub(r"\D", "", alvo)
        if len(num) > 11:
            raise PedidoErro(f"{alvo}: {len(num)} dígitos é id de FOTO do ML (mlstatic), não de anúncio — "
                             "pegue o MLB do link do produto")
        return {"loja": "ml", "mlb": f"MLB{num}"}
    if not re.match(r"https?://", alvo):
        raise PedidoErro(f"não entendi o pedido: {alvo[:60]} (mande o link ou o MLB)")
    if re.search(r"s\.shopee\.com\.br|shope\.ee|shopee\.com\.br", alvo):
        return {"loja": "shopee", "url": alvo}
    url = _seguir_encurtador(alvo, cli) if "meli.la" in alvo else alvo
    if not re.search(r"mercadoli(?:vre|bre)\.com", url):
        raise PedidoErro(f"o link não é do Mercado Livre nem da Shopee: {url.split('?')[0][:60]}")
    if "/social/" in url:  # vitrine da OUTRA pessoa: o HTML lista os MLB em ordem — o 1º é o do link
        html = _get_ml(url, cli)
        ids = mlbs_no_html(html)
        if not ids:
            raise PedidoErro("a vitrine /social não mostrou nenhum anúncio")
        return {"loja": "ml", "mlb": ids[0], "html": html, "outros": ids[1:6]}
    m = re.search(r"/p/(MLB\d+)", url)  # anúncio de catálogo: a URL da página é a própria /p/
    if m and not MLB_ANUNCIO.search(url.split("/p/")[0]):
        return {"loja": "ml", "mlb": m.group(1), "url": url.split("?")[0].split("#")[0]}
    if not (m := MLB_ANUNCIO.search(url)):
        raise PedidoErro(f"não achei o MLB no link: {url.split('?')[0][:70]}")
    return {"loja": "ml", "mlb": f"MLB{m.group(1)}"}


def _num(v) -> float | None:
    """'1.234,56' / '1234.56' / 179.55 → float."""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = re.sub(r"[^\d,.]", "", str(v))
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _reais_aria(rotulo: str, html: str) -> float | None:
    """aria-label="Antes: 286 reais com 66 centavos" (o "De" riscado da página do ML). 09/10: o Chrome da loja devolve
    o rótulo em espanhol ("Antes: 219 reales con 10 centavos") — vale também."""
    m = re.search(rf'aria-label="{rotulo}:?\s*([\d.]+)\s*rea(?:is|les)(?:\s*co[mn]\s*(\d+)\s*centavos?)?', html, re.I)
    return float(m.group(1).replace(".", "")) + int(m.group(2) or 0) / 100 if m else None


def _bloco_preco(html: str) -> str | None:
    """O bloco de preço PRINCIPAL do anúncio (id="price"). A página tem dezenas de "Antes:" de carrossel embaixo — o
    "De" só vale se estiver AQUI. None = HTML sem esse bloco (mínimo/antigo)."""
    i = html.find('id="price"')
    if i < 0:
        i = html.find("ui-pdp-price__main-container")
    return html[i:i + 5000] if i >= 0 else None


# og:title do ML = "Título - R$ 219" (09/10, blush Too Faced: o preço vinha grudado no título)
_OG_TITULO_PRECO = re.compile(r"^(.*?\S)\s+-\s+R\$\s*([\d.]+(?:,\d{1,2})?)\s*$")


def ler_anuncio_ml(html: str, mlb: str) -> dict:
    """Título, preço, "De", foto, loja oficial, nota e vendidos da página PÚBLICA do anúncio (ld+json + meta + rótulos).
    Serve também para o HTML da página aberta no Chrome da loja (`achadinhos pedido MLB --html`)."""
    d: dict = {}
    html = html or ""
    bloco = _bloco_preco(html)
    for ld in re.findall(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', html, re.S | re.I):
        try:
            j = json.loads(ld, strict=False)  # quebra de linha dentro do texto não derruba
        except ValueError:
            continue
        for x in j if isinstance(j, list) else j.get("@graph", [j]) if isinstance(j, dict) else []:
            if not (isinstance(x, dict) and x.get("@type") == "Product"):
                continue
            img = x.get("image")
            of = x.get("offers") or {}
            of = of[0] if isinstance(of, list) and of else of
            nota = (x.get("aggregateRating") or {}).get("ratingValue") if isinstance(x.get("aggregateRating"), dict) else None
            d.update({k: v for k, v in {
                "titulo": " ".join(_html.unescape(x.get("name") or "").split()),
                "foto": img[0] if isinstance(img, list) and img else img if isinstance(img, str) else None,
                "preco": _num(of.get("price")) if isinstance(of, dict) else None, "nota": _num(nota)}.items() if v})

    def meta(prop, onde=html):
        m = (re.search(rf'<meta[^>]+(?:property|name|itemprop)="{prop}"[^>]*content="([^"]*)"', onde, re.I)
             or re.search(rf'<meta[^>]+content="([^"]*)"[^>]*(?:property|name|itemprop)="{prop}"', onde, re.I))
        return _html.unescape(m.group(1)).strip() if m else None
    # título: ld+json Product → <h1 class="ui-pdp-title"> → og:title SEM o " - R$ 219" do fim (09/10, Too Faced)
    og = " ".join(_html.unescape(meta("og:title") or "").split(" | ")[0].split())
    og_preco = None
    if m := _OG_TITULO_PRECO.match(og):
        og, og_preco = m.group(1), _num(m.group(2))
    if not d.get("titulo"):
        m = re.search(r'<h1[^>]*ui-pdp-title[^>]*>([^<]{3,300})</h1>', html)
        d["titulo"] = " ".join(_html.unescape(m.group(1)).split()) if m else (og or None)
    d.setdefault("foto", meta("og:image"))
    if not d.get("preco"):
        d["preco"] = (_num(meta("price", bloco)) if bloco else None) or _num(meta("price")) \
            or _reais_aria("Agora", bloco or html)
    # "De" = o riscado do bloco de preço PRINCIPAL; sem esse bloco (HTML mínimo) → original_price ou o R$ do og:title.
    # Com o bloco e sem riscado = não tem "De" (nada de pegar o "Antes" de um carrossel nem inventar pelo og:title).
    if bloco is not None:
        de = _reais_aria("Antes", bloco)
    else:
        de = (_reais_aria("Antes", html) or _num((re.search(r'"original_price"\s*:\s*([\d.]+)', html) or [None, None])[1])
              or og_preco)
    d["preco_antigo"] = de if de and d.get("preco") and de > d["preco"] else None
    # 09/10: "10% OFF no Pix" = o "Por" da página é o preço no Pix (no cartão é outro) → aviso para o Beto
    texto_bloco = " ".join(_html.unescape(re.sub(r"<[^>]+>", " ", bloco or "")).split())
    d["so_pix"] = bool(re.search(r"OFF\s+no\s+Pix", texto_bloco, re.I))
    m = re.search(r"no\s+Pix\s+ou\s+R\$\s*([\d.]+)(?:\s*,\s*(\d{1,2}))?", texto_bloco, re.I) if d["so_pix"] else None
    d["preco_cartao"] = _num(f"{m.group(1)},{m.group(2) or '0'}") if m else None
    if not d.get("nota"):
        m = re.search(r'ui-pdp-review__rating[^>]*>\s*([\d.,]+)', html)
        d["nota"] = _num(m.group(1)) if m else None
    m = re.search(r'\+\s*(\d+(?:[.,]\d+)?)\s*(mil)?\s*vendid', html, re.I)
    d["vendidos_num"] = int(float(m.group(1).replace(",", ".")) * (1000 if m.group(2) else 1)) if m else 0
    # loja oficial: nome no JSON → o "Loja oficial <loja>" do resumo do vendedor → qualquer "Loja oficial X" que não seja
    # o selo genérico "Loja oficial do Mercado Livre" (09/10: vinha "do Mercado Livre" como nome da loja)
    m = (re.search(r'"official_store_name"\s*:\s*"([^"]{2,60})"', html)
         or re.search(r'seller-summary__label-sold"[^>]*>\s*(?:<[^>]+>\s*)*Loja oficial\s*(?:<[^>]+>\s*)+([^<"]{2,60})<',
                      html, re.I)
         or re.search(r'Loja oficial\s*(?:<[^>]+>\s*)*(?!d[oae]s?\s+Mercado\s+Livre)([^<"{\s][^<"{]{1,59})', html,
                      re.I))
    d["oficial"], d["loja_nome"] = bool(m), (_html.unescape(m.group(1)).strip() if m else "")
    if d.get("foto"):  # miniatura do mlstatic → a original (~1200 px)
        d["foto"] = re.sub(r"(mlstatic\.com/)D_Q_NP_(?:2X_)?(.+)-[A-Z]{1,2}\.(webp|jpg)$", r"\1D_NQ_NP_2X_\2-F.\3",
                           d["foto"])
        # 09/10: og:image do ML vem "-O" (500 px) → "-F" (tamanho cheio) ANTES de medir (FOTO_MIN_PX)
        d["foto"] = re.sub(r"(mlstatic\.com/.+)-O\.(webp|jpe?g|png)$", r"\1-F.\2", d["foto"])
    d["do_anuncio"] = re.sub(r"\D", "", mlb) in html
    return d


def _gravar_pedido_dona(oid: str, grupo: str | None = None) -> None:
    """Põe o id no COMEÇO de pedidos_dona.json (mesmo formato da Rafa/Océane: {id: {"quando", "motivo", "grupo"?}})."""
    arq = PEDIDOS_DONA
    try:
        d = json.loads(arq.read_text(encoding="utf-8")) if arq.exists() else {}
    except (OSError, ValueError):
        raise PedidoErro(f"{arq.name} ilegível — não sobrescrevo os pedidos que já estão lá") from None
    if not isinstance(d, dict):
        raise PedidoErro(f"{arq.name} fora do formato")
    novo = {"quando": datetime.now().isoformat(timespec="seconds"), "motivo": MOTIVO_PEDIDO_WHATSAPP,
            **({"grupo": grupo} if grupo else {})}
    d = {oid: novo, **{k: v for k, v in d.items() if k != oid}}
    arq.parent.mkdir(parents=True, exist_ok=True)
    tmp = arq.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(arq)


def _pendente(mlb: str, texto: str | None, motivo: str, tirar: bool = False) -> None:
    """pedidos_pendentes.json: [{mlb, texto, quando, motivo}] — só o MLB (nunca o link de terceiro)."""
    try:
        lista = json.loads(PEDIDOS_PENDENTES.read_text(encoding="utf-8"))
        lista = lista if isinstance(lista, list) else []
    except (OSError, ValueError):
        lista = []
    lista = [p for p in lista if p.get("mlb") != mlb]
    if not tirar:
        lista.append({"mlb": mlb, "texto": texto or "", "quando": datetime.now().isoformat(timespec="seconds"),
                      "motivo": motivo})
    PEDIDOS_PENDENTES.parent.mkdir(parents=True, exist_ok=True)
    PEDIDOS_PENDENTES.write_text(json.dumps(lista, ensure_ascii=False, indent=1), encoding="utf-8")


def pendentes() -> list[dict]:
    try:
        lista = json.loads(PEDIDOS_PENDENTES.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [p for p in lista if isinstance(p, dict) and p.get("mlb")] if isinstance(lista, list) else []


def _pedido_ml(r: dict, texto: str | None, cli: httpx.Client) -> dict:
    mlb = r["mlb"]
    url = r.get("url") or f"https://produto.mercadolivre.com.br/MLB-{mlb[3:]}"
    html = r.get("html")
    d = ler_anuncio_ml(html, mlb) if html else {}
    if not (d.get("titulo") and d.get("preco") and d.get("do_anuncio") and d.get("foto")):
        if r.get("so_html"):  # --html: a página do Chrome é a ÚNICA fonte — nunca abre o ML daqui
            raise PedidoErro(f"{mlb}: o HTML salvo não tem título/preço/foto deste anúncio — abra o produto de novo no "
                             "Chrome da loja e rode o ml_pedido.js")
        # a vitrine /social é só o caminho do meli.la deles; o anúncio é a 1 página do produto que o pedido lê
        d = ler_anuncio_ml(_get_ml(url, cli), mlb)
    if not d.get("do_anuncio"):
        raise PedidoErro(f"{mlb}: a página aberta não é a deste anúncio")
    if not (d.get("titulo") and d.get("preco")):
        raise PedidoErro(f"{mlb}: a página não mostrou título e preço (anúncio pausado/encerrado?)")
    if not (d.get("foto") and re.match(r"https://http2\.mlstatic\.com/", d["foto"])):
        raise PedidoErro(f"{mlb}: sem foto do anúncio")
    if (px := foto_px(d["foto"])) < FOTO_MIN_PX:
        raise PedidoErro(f"{mlb}: foto com {px} px (mínimo {FOTO_MIN_PX})")
    titulo = d["titulo"]
    grupo = _grupo_final(_grupo(titulo), titulo)
    de, preco = d.get("preco_antigo"), d["preco"]
    sinais = {"tipo": "NORMAL", "comissao": 0, "vendidos_num": d.get("vendidos_num") or 0, "oficial": d["oficial"],
              "loja_nome": d.get("loja_nome") or "", "pedido_dona": True, "origem": "pedido_dona"}
    if d.get("so_pix"):
        sinais.update(so_pix=True, **({"preco_cartao": d["preco_cartao"]} if d.get("preco_cartao") else {}))
    if texto:
        sinais["gancho_dona"] = texto.strip().upper()
    vend = sinais["vendidos_num"]
    o = {"id": f"mlaf:{mlb}", "fonte": "ml_afiliados", "loja": "Mercado Livre", "titulo": titulo, "grupo": grupo,
         "preco": preco, "preco_antigo": de, "desconto": round((1 - preco / de) * 100) if de else 0, "cupom": None,
         "foto": d["foto"], "link_fonte": None, "link_loja": url if "/p/" in url else f"https://produto.mercadolivre.com.br/MLB-{mlb[3:]}",
         "nota": d.get("nota"), "vendidos": f"{vend} vendidos" if vend else None, "sinais": sinais}
    return o


# 09/10: o ML bloqueia o robô (verificação), mas o Chrome DA LOJA abre o produto normal. Caminho oficial do pedido com
# link do ML: abrir o produto no Chrome (1 aba) → js/ml_pedido.js (POST /estudo → dados/estudos/mlpedido_<MLB>.json)
# → `vendas achadinhos pedido MLB --html [ARQ]`. Nenhuma requisição ao ML sai daqui (só a foto do mlstatic é medida).
def html_pedido_salvo(mlb: str) -> Path:
    """Onde o receptor grava o HTML que o ml_pedido.js mandou."""
    return config.DADOS / "estudos" / f"mlpedido_{mlb}.json"


def ler_html_salvo(arq: str | Path) -> str:
    """.html/.htm = o HTML cru; .json do receptor = string JSON (o outerHTML) ou {"html"/"dados": ...}."""
    arq = Path(arq)
    try:
        bruto = arq.read_text(encoding="utf-8")
    except OSError as e:
        raise PedidoErro(f"não consegui ler {arq}: {type(e).__name__}") from None
    if arq.suffix.lower() == ".json":
        try:
            j = json.loads(bruto)
        except ValueError:
            raise PedidoErro(f"{arq.name}: JSON ilegível") from None
        j = j.get("html") or j.get("dados") if isinstance(j, dict) else j
        if not isinstance(j, str):
            raise PedidoErro(f"{arq.name}: não tem o HTML da página (esperava o outerHTML do ml_pedido.js)")
        bruto = j
    if "<" not in bruto[:2000]:
        raise PedidoErro(f"{arq.name}: não parece HTML")
    return bruto


def mlb_do_arquivo(arq: str | Path) -> str | None:
    """mlpedido_MLB6074272600.json → MLB6074272600."""
    m = re.search(r"(MLB\d{6,11})(?!\d)", Path(arq).name)
    return m.group(1) if m else None


def _resolver_sem_rede(alvo: str) -> dict:
    """Com --html: o MLB vem do MLB, do item_id da URL (pdp_filters=item_id:MLB…) ou do link do produto. meli.la e
    vitrine /social precisariam abrir o ML → pede o MLB (o ml_pedido.js mostra qual é)."""
    alvo = (alvo or "").strip()
    if m := re.search(r"item_id(?:%3A|:)(MLB\d{6,11})(?!\d)", alvo, re.I):
        return {"loja": "ml", "mlb": m.group(1).upper()}
    if re.search(r"meli\.la|/social/|shopee|shope\.ee", alvo, re.I):
        raise PedidoErro("com --html mande o MLB do anúncio (o ml_pedido.js mostra qual é) — daqui não abro link nenhum")
    r = resolver_pedido(alvo, None)  # MLB solto, produto.mercadolivre…/MLB-…, /p/MLB…: nada disso usa a rede
    if r.get("loja") != "ml":
        raise PedidoErro("--html é só para anúncio do Mercado Livre")
    return r


def pedido(alvo: str, texto: str | None = None, cli: httpx.Client | None = None, html: str | None = None) -> dict:
    """Pedido da dona → radar (aprovada, NOSSO link) + frente da fila. Devolve {"ok", "id", "titulo", "preco", "de",
    "link", "avisos"} ou {"ok": False, "erro"}. Nunca guarda o link de terceiro.
    `html` = a página do anúncio aberta no Chrome da loja (ml_pedido.js): lê só ela, sem requisição ao ML (vale mesmo
    com o ML em pausa)."""
    _tabela()
    fechar = cli is None
    cli = cli or httpx.Client(timeout=25)
    mlb = None
    try:
        if html is not None:
            r = {**_resolver_sem_rede(alvo), "html": html, "so_html": True}
        else:
            r = resolver_pedido(alvo, cli)
        if r["loja"] == "shopee":
            return _pedido_shopee(r["url"], texto)
        mlb = r["mlb"]
        o = _pedido_ml(r, texto, cli)
    except MLBloqueado as e:
        if mlb:
            _pendente(mlb, texto, str(e))
        return {"ok": False, "erro": str(e), "mlb": mlb, "pendente": bool(mlb)}
    except (PedidoErro, httpx.HTTPError) as e:
        return {"ok": False, "erro": str(e) if isinstance(e, PedidoErro) else f"rede: {type(e).__name__}", "mlb": mlb}
    finally:
        if fechar:
            cli.close()
    avisos = []
    if tem_afiliado_terceiro(o["link_loja"]) or not pagina_de_produto(o["link_loja"]):
        return {"ok": False, "erro": f"link fora do padrão: {o['link_loja']}", "mlb": mlb}
    if not o.get("preco_antigo"):
        avisos.append("a página não mostra preço 'De' → a fila não posta sem 'De' (regra da dona)")
    if o["sinais"].get("so_pix"):
        cartao = o["sinais"].get("preco_cartao")
        avisos.append("o 'Por' é o preço NO PIX" + (f" (no cartão {_brl(cartao)})" if cartao else "")
                      + " — o Beto confere se o 'De' é promoção de verdade")
    if FALSIFICAVEL.search(o["titulo"]) and not o["sinais"]["oficial"]:
        avisos.append("marca muito falsificada e a loja NÃO é oficial — o Beto confere antes de sair")
    o["score"] = pontuar(o)
    agora = datetime.now().isoformat(sep=" ", timespec="seconds")
    with db.conectar() as con:
        con.execute("""INSERT INTO ofertas (id, fonte, loja, titulo, grupo, preco, preco_antigo, desconto, cupom, foto,
                         link_fonte, link_loja, nota, vendidos, sinais, score, aprovada, visto_em, atualizado_em)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?)
                       ON CONFLICT(id) DO UPDATE SET titulo=excluded.titulo, grupo=excluded.grupo, preco=excluded.preco,
                         preco_antigo=excluded.preco_antigo, desconto=excluded.desconto, foto=excluded.foto,
                         link_loja=excluded.link_loja, nota=excluded.nota, vendidos=excluded.vendidos,
                         sinais=excluded.sinais, score=excluded.score, aprovada=1, visto_em=excluded.visto_em,
                         atualizado_em=excluded.atualizado_em""",
                    (o["id"], o["fonte"], o["loja"], o["titulo"], o["grupo"], o["preco"], o["preco_antigo"],
                     o["desconto"], None, o["foto"], None, o["link_loja"], o["nota"], o["vendidos"],
                     json.dumps(o["sinais"], ensure_ascii=False), o["score"], agora, agora))
        _guardar_precos(con, [(o["id"], o["preco"])])
    # 09/10 (Claude): reprocessar um pedido JÁ POSTADO o punha de novo na frente da fila → repetiu no grupo no mesmo
    # dia (Kérastase/Too Faced 13h16/13h30). Já saiu nos últimos REPETIR_DIAS → só atualiza o preço, não volta à fila
    ja = db.consultar("SELECT publicado_em FROM ofertas WHERE id=? AND publicado_em >= datetime('now','localtime',?)",
                      (o["id"], f"-{REPETIR_DIAS} days"))
    if ja and ja[0]["publicado_em"]:
        avisos.append(f"já saiu no grupo em {ja[0]['publicado_em'][:16]} → preço atualizado, NÃO voltou para a fila")
    else:
        _gravar_pedido_dona(o["id"])
    _pendente(mlb, None, "", tirar=True)
    return {"ok": True, "id": o["id"], "titulo": o["titulo"], "preco": o["preco"], "de": o["preco_antigo"],
            "link": link_afiliado(o["link_loja"], canal="whatsapp"), "grupo": o["grupo"], "avisos": avisos,
            "outros": r.get("outros") or []}


def _pedido_shopee(url: str, texto: str | None) -> dict:
    """Shopee: o caminho que já existe (Open API pelo itemId → nosso s.shopee) e depois a frente da fila."""
    from vendas.integracoes import shopee_referencia as sr
    limpa = sr.limpar_url(url)
    if not limpa:
        return {"ok": False, "erro": "não achei o produto da Shopee nesse link"}
    res = sr.pela_api([limpa], log=lambda *a: None)
    oid = f"shpaf:{limpa.rsplit('/', 1)[1]}"
    linha = db.consultar("SELECT * FROM ofertas WHERE id = ?", (oid,))
    if not linha:
        return {"ok": False, "erro": "; ".join(res.get("recusados") or []) or "a Open API não devolveu o produto"}
    o = dict(linha[0])
    s = sinais_de(o)
    if not re.match(r"https://s\.shopee\.com\.br/\w+$", s.get("offer_link") or ""):
        return {"ok": False, "erro": f"{oid}: sem o NOSSO link curto da Shopee (a fila não posta sem ele)"}
    s.update(pedido_dona=True, **({"gancho_dona": texto.strip().upper()} if texto else {}))
    agora = datetime.now().isoformat(sep=" ", timespec="seconds")
    with db.conectar() as con:
        con.execute("UPDATE ofertas SET sinais = ?, aprovada = 1, atualizado_em = ? WHERE id = ?",
                    (json.dumps(s, ensure_ascii=False), agora, oid))
    _gravar_pedido_dona(oid)
    return {"ok": True, "id": oid, "titulo": o["titulo"], "preco": o["preco"], "de": o.get("preco_antigo"),
            "link": s["offer_link"], "grupo": o["grupo"],
            "avisos": [] if o.get("preco_antigo") else ["sem preço 'De' → a fila não posta sem 'De' (regra da dona)"]}


def relatorio_pedido(r: dict) -> str:
    if not r.get("ok"):
        extra = " (ficou em pedidos_pendentes.json — `vendas achadinhos pedido` sem link tenta de novo)" \
            if r.get("pendente") else ""
        return f"x {r.get('mlb') or ''} {r['erro']}{extra}".replace("x  ", "x ")
    de = f"de {_brl(r['de'])} " if r.get("de") else ""
    linhas = [f"OK {r['id']} · {r['titulo'][:70]} · {de}por {_brl(r['preco'])} · {r['grupo']}", f"   {r['link']}"]
    linhas += [f"   ! {a}" for a in r.get("avisos") or []]
    if r.get("outros"):
        linhas.append(f"   (outros MLB na vitrine /social, em ordem: {', '.join(r['outros'])})")
    return "\n".join(linhas)


# 09/10 noite (dona: "uma janela de uma hora sem post" — às 21h o servidor tinha 3 ofertas na fila, com 1.241 aprovadas
# de preço fresco no radar). CAUSA: o "1 por tipo" (2 primeiras palavras do título) vinha ANTES das regras da oferta. A de
# maior score de cada tipo quase sempre já tinha saído, estava vetada, era masculina ou não tinha "De" → o tipo INTEIRO
# sumia (para sempre, enquanto ela seguisse no topo) e a 2ª, 3ª… boa do mesmo tipo nunca chegava à fila (288 postáveis
# viravam 3). Agora cada oferta passa primeiro por TODAS as regras dela e só então entra a variedade:
#   · tipo que saiu nas últimas JANELAS_TIPO_H[0] horas não repete (Mila 02/10) e fica 1 por tipo na fila;
#   · só se isso não deixa na fila nem 1 post por rodada até o fim do horário (rodadas que faltam + 4 de folga, no máx.
#     VARIEDADE_MIN) a janela encolhe (48 → 24 → 12 h) — o grupo não pode parar. O mesmo PRODUTO continua sem repetir
#     em REPETIR_DIAS e o teto por tipo/dia (TETO_TIPO, "cílios 900 vezes") continua valendo.
JANELAS_TIPO_H = (48, 24, 12)
VARIEDADE_MIN = 60


def rodadas_que_faltam(agora: datetime | None = None) -> int:
    """Rodadas do grupo que ainda faltam hoje (config/achadinhos.json: horário e intervalo). Fora do horário = o dia
    inteiro (a fila que conta é a de amanhã cedo)."""
    agora = agora or datetime.now()
    try:
        cfg = canais()
        h_ini, h_fim = cfg.get("horario", [8, 22])
        passo = max(1, int(cfg.get("intervalo_rodada_min", 15)))
    except Exception:  # noqa: BLE001 — config ilegível: o padrão do grupo
        h_ini, h_fim, passo = 8, 22, 15
    minuto = agora.hour * 60 + agora.minute
    if not h_ini * 60 <= minuto < h_fim * 60:
        minuto = h_ini * 60
    return max(1, (h_fim * 60 - minuto) // passo)


def _candidatos(horas: int = 30, so_com_link_curto: bool = True, funil: list | None = None,
                permitidos_teste: list | None = None) -> tuple[list[dict], set, str]:
    """Ofertas que PODEM ir ao grupo agora (filtros baratos, sem baixar foto): (candidatas, tipos que não podem repetir
    agora, corte da Amazon). Base de fila_posts e de estoque(). `funil` (lista) recebe (etapa, quantas sobraram, por
    grupo) a cada filtro — `vendas achadinhos funil` (05/10: achar qual regra seca a fila)."""
    def marca(etapa: str, lista: list) -> None:
        if funil is not None:
            por: dict = {}
            for o in lista:
                por[o.get("grupo") or "?"] = por.get(o.get("grupo") or "?", 0) + 1
            funil.append((etapa, len(lista), por))
    agora = datetime.now()
    # 02/10 (Mila): 24 h deixava 18% de repetidos de ontem → o TIPO não repete em 48 h (hora do último post de cada tipo;
    # 09/10 noite: a janela encolhe quando a fila fica curta — ver JANELAS_TIPO_H)
    saiu_tipo: dict = {}
    for r in db.consultar("SELECT titulo, publicado_em FROM ofertas WHERE publicado_em >= "
                          f"datetime('now','localtime','-{JANELAS_TIPO_H[0]} hours')") or []:
        r = dict(r)
        k = chave_produto(r.get("titulo") or "")
        saiu_tipo[k] = max(saiu_tipo.get(k, ""), str(r.get("publicado_em") or "9").replace("T", " "))  # sem hora = agora
    # 30/09: Amazon vai com a hora do preço no post → só preço visto nas últimas 6 h (antes saiu perfume com preço de
    # ontem 12h51 — com o carimbo apareceria "29/09" e o preço podia já ter mudado)
    amazon_ok = (agora - timedelta(hours=6)).isoformat(sep=" ", timespec="seconds")
    # 06/10 (Ana): contrato do Influenciador Magalu veda informação desatualizada (11.4) e a coleta da Época no Magalu só
    # roda no Chrome → só preço coletado nas últimas 12 h
    magalu_ok = (agora - timedelta(hours=12)).isoformat(sep=" ", timespec="seconds")
    oceane_ok = (agora - timedelta(hours=OCEANE_VALIDADE_H)).isoformat(sep=" ", timespec="seconds")
    # 07/10 (Bia, Quarta do Perfume): com o feed novo da Shopee (+2 mil) o corte das 3.000 de maior score deixava de
    # fora 19 perfumes aprovados (Época/Amazon) → 8.000
    # 08/10 (Beto): preço visto há mais de 24 h não vai nem volta
    todas = melhores(horas, LIMITE_CANDIDATOS)
    frescas = [o for o in todas if preco_fresco(o)]
    marca(f"aprovadas com preço visto há ≤ {PRECO_VALE_H} h (de {len(todas)})", frescas)
    base = [o for o in frescas if o["link_loja"] and o.get("foto")
            and (not re.search(r"amazon\.com\.br|amzn\.to", o["link_loja"], re.I) or (o.get("atualizado_em") or "") >= amazon_ok)
            and (o.get("fonte") != "magalu_epoca" or (o.get("atualizado_em") or "") >= magalu_ok)
            and (o.get("fonte") != "oceane_afiliados" or (o.get("atualizado_em") or "") >= oceane_ok)]
    marca("com link e foto, preço na validade da loja", base)
    pub = publicados_recentes()  # 08/10 (Beto: Laneige voltou 2 dias depois): o mesmo produto não sai de novo em 7 dias
    base = [o for o in base if not ja_saiu(o, pub)]
    marca(f"não saiu (mesmo item/título) em {REPETIR_DIAS} dias", base)
    cand = [o for o in base if not o["publicado_em"]]
    marca("ainda não postadas", cand)
    # 04/10 (grupo parado com o PC desligado): oferta que o radar AINDA vê em promoção (preço conferido nas últimas 2 h)
    # e saiu há mais de REPETIR_DIAS (era 3) pode voltar quando a fila fica curta — quem entrou depois não viu
    volta = (agora - timedelta(days=REPETIR_DIAS)).isoformat(sep=" ", timespec="seconds")
    fresco = (agora - timedelta(hours=2)).isoformat(sep=" ", timespec="seconds")
    voltam = [o for o in base if o["publicado_em"] and o["publicado_em"] < volta and (o.get("atualizado_em") or "") >= fresco]
    # 01/10 (Mila): a vaga do variado testava 6 fotos de 400–500 px JÁ medidas e se perdia → pula de cara quem tem foto
    # pequena conhecida (Promobit→ML ainda pode trocar pela foto do ML em foto_boa); e nada de produto vencendo
    try:
        px = json.loads(_FOTO_PX_ARQ.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        px = {}
    from vendas import revisao_fila  # 01/10 (dona): revisoras vetam ANTES de postar → o robô pula o vetado
    vet = revisao_fila.vetados()
    chaves_vet = revisao_fila.chaves_vetadas(vet)  # 06/10 (Beto): veto vale pelo produto (ASIN/item/MLB), não só pelo id

    def foto_pequena(o: dict) -> bool:
        # 07/10: Promobit com foto pequena só segue se der para TROCAR pela foto real do mesmo produto (coleta do
        # ML/Amazon ou página da loja) — senão já sai aqui (antes passava toda e, sem troca no servidor, ia com 400 px)
        return (str(o.get("foto")).startswith("http") and px.get(o["foto"]) is not None and px[o["foto"]] < FOTO_MIN_PX
                and not ("promobit.com.br" in o["foto"] and (LOJAS_FOTO.search(o.get("link_loja") or "")
                                                             or foto_do_mesmo_produto(o))))
    # regras DA OFERTA, na ordem do funil (os pedidos da dona entram depois, por cima)
    regras: list = []
    if so_com_link_curto:
        regras.append(("Shopee só com link curto", lambda o: not shopee_sem_curto(o)))
    regras.append(("foto pequena, vencendo, vetada, barrada",
                   lambda o: not (foto_pequena(o) or vencendo(o.get("titulo") or "")
                                  or revisao_fila.vetada(o, vet, chaves_vet) or barrada_na_hora(o))))
    regras.append(("acima do mercado", lambda o: not acima_do_mercado(o)))
    # 07/10 (Rita: COSRX/Medicube só com "Por"): o post do grupo é "De riscado → Por" (modelo Entre Mulheres) → sem
    # "De" maior que o "Por" não vai. 08/10: e o "De" tem de APARECER na legenda (tem_de_no_post) — antes só o número
    # era olhado e a legenda escondia o "De" não confiável, então o post saía só com "Por"
    regras.append(("sem preço 'De'", lambda o: tem_de_no_post(o)))
    # 03/10 (dona, depois do post do "pato" e de uma pessoa sair): "modo só o melhor" — só as categorias do público
    # (config/achadinhos.json → "grupos_permitidos"; sem a chave = todas)
    permitidos = permitidos_teste or canais().get("grupos_permitidos")
    if permitidos:
        regras.append((f"só {', '.join(permitidos)}", lambda o: o.get("grupo") in permitidos))
    if so_feminino_ligado():
        regras.append(("modo feminino", lambda o: no_perfil_feminino(o)))
    for nome, passa in regras:
        cand = [o for o in cand if passa(o)]
        marca(nome, cand)

    # 01/10 (Nina): 6 relógios masculinos e 3 creatinas no mesmo dia → no máx. MAX_TIPO_DIA do mesmo tipo por dia
    # (04/10: a linha de beleza também — TETO_TIPO, "cílios 900 vezes")
    tipos_hoje: dict = {}
    for r in db.consultar("SELECT titulo, grupo FROM ofertas WHERE publicado_em >= date('now','localtime')"):
        t = tipo_repetivel(dict(r))
        if t:
            tipos_hoje[t] = tipos_hoje.get(t, 0) + 1
    from vendas import datas as _datas
    tema = _datas.tema_do_dia()  # 07/10 (dona: "hoje é dia do perfume… precisa ter muitos perfumes"): o tema não tem teto

    def com_teto(lista: list) -> list:
        def _teto(fator: int) -> list:
            return [o for o in lista
                    if (tema and _datas.casa(o, tema))
                    or tipos_hoje.get(tipo_repetivel(o) or "", 0) < fator * TETO_TIPO.get(tipo_repetivel(o), MAX_TIPO_DIA)]
        out = _teto(1)
        # 06/10 (dona: "por que esse espaço gigante entre os posts?" — à noite sobravam 5–7 ofertas e saía 1 por
        # rodada): se o teto deixa a fila do perfil com menos de 20, dobra o teto (ainda varia o tipo; cílios no máx.
        # 8/dia). 07/10 (dona: "por que parou de postar?" — de manhã eram 38 no perfil e saía 0–1 por rodada): 20 → 60
        if so_feminino_ligado() and sum(1 for o in out if no_perfil_feminino(o)) < 60:
            out = _teto(2)
        return out
    alvo = min(VARIEDADE_MIN, rodadas_que_faltam(agora) + 4)  # 1 post por rodada até o fim do horário, com folga

    def variar(lista: list) -> tuple[list, list, set, int]:
        """Tipo que saiu há pouco não repete e fica 1 por tipo; depois o teto do tipo por dia. A janela só encolhe se o
        que sobra no fim é menos que o `alvo`. Devolve (1 por tipo, depois do teto, tipos bloqueados, janela em h)."""
        melhor: tuple = ([], [], set(), JANELAS_TIPO_H[0])
        for i, h in enumerate(JANELAS_TIPO_H):
            corte = (agora - timedelta(hours=h)).isoformat(sep=" ", timespec="seconds")
            bloq = {k for k, quando in saiu_tipo.items() if quando >= corte}
            unicos = sem_repetidos([o for o in lista if chave_produto(o.get("titulo") or "") not in bloq])
            final = com_teto(unicos)
            if i == 0 or len(final) > len(melhor[1]):  # janela menor só vale se trouxer MAIS ofertas
                melhor = (unicos, final, bloq, h)
            if len(final) >= alvo:
                break
        return melhor
    unicos, final, recentes, janela = variar(cand)
    if len(final) < POUCO_ESTOQUE and voltam:
        de_volta = [o for o in voltam if all(passa(o) for _, passa in regras)]
        if de_volta:
            unicos, final, recentes, janela = variar(cand + de_volta)
    marca(f"tipo sem repetir em {janela} h, 1 por tipo", unicos)
    cand = final
    marca("teto do mesmo tipo por dia", cand)
    try:  # 04/10 (dona): produto que os grupos de referência postaram (e temos, com o NOSSO link) vem primeiro
        from vendas import referencia
        dest = referencia.na_frente()
        cand.sort(key=lambda o: o["id"] not in dest)
    except Exception:  # noqa: BLE001 — sem a leitura, segue a ordem normal
        pass
    ped = _pedidos_da_dona(amazon_ok)  # 07/10: pedido da dona na frente de tudo (ver PEDIDOS_DONA)
    # 08/10 (revisão): o pedido também espera o link curto da Shopee (ia com o shope.ee/an_redir comprido)
    ped = [o for o in ped if not (so_com_link_curto and shopee_sem_curto(o))]
    if ped:
        ids = {o["id"] for o in ped}
        cand = ped + [o for o in cand if o["id"] not in ids]
        marca("+ pedidos da dona (na frente)", cand)
    return cand, recentes, amazon_ok


def estoque(horas: int = 30) -> int:
    """04/10 (grupo parou às 8h06 com "0 postadas de 0": o servidor postava 18/h e a fila secou com o PC desligado)
    → quantas ofertas ainda podem sair (com link curto ou de emergência)."""
    return len(_candidatos(horas, so_com_link_curto=False)[0])


def estoque_postavel(horas: int = 30, medir: int = 40) -> int:
    """09/10 noite: estoque REAL para o ritmo — o que estoque() conta MENOS o que a rodada não consegue postar: fonte
    com o teto do dia cheio (Shein) e foto que não passa em foto_boa. Em 09/10, das 20h45 às 21h15, o robô contava
    "5, 3, 3 ofertas → 2 nesta rodada" e a fila vinha VAZIA (pedido da dona com foto de 500 px, fotos nunca medidas).
    Mede no máx. `medir` fotos ainda sem medida por chamada (as primeiras da fila; as outras contam como boas) — com a
    fila curta, que é quando o número importa, mede todas. medir=0 não usa a internet."""
    cand = dentro_do_teto_dia(_candidatos(horas, so_com_link_curto=False)[0])
    try:
        px = json.loads(_FOTO_PX_ARQ.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        px = {}
    n = 0
    for o in cand:
        f = str(o.get("foto") or "")
        if f.startswith("http") and (f not in px or "promobit.com.br" in f):  # medir/trocar a foto usa a internet
            if medir <= 0:
                n += px.get(f) is None or px[f] >= FOTO_MIN_PX  # sem medida = conta; pequena já medida = não conta
                continue
            medir -= 1
        n += bool(foto_boa(dict(o)))
    return n


def minutos_sem_post(agora: datetime | None = None) -> float | None:
    """Minutos desde a última OFERTA postada no grupo (None = nunca postou) — o ritmo usa para não deixar 2 rodadas
    vazias seguidas."""
    r = db.consultar("SELECT MAX(publicado_em) m FROM ofertas") or [{}]
    m = dict(r[0]).get("m")
    try:
        return ((agora or datetime.now()) - datetime.fromisoformat(str(m)[:19].replace("T", " "))).total_seconds() / 60
    except (TypeError, ValueError):
        return None


# 09/10 noite: estoque postável abaixo do mínimo → alerta para o Guarda e o Claude (Caixa de revisão + `rotina saude`)
ESTOQUE_MINIMO = 15
ALERTA_CAIXA_H = 3  # na Caixa de revisão, no máx. 1 linha a cada 3 h (o arquivo é regravado a cada rodada)


def estoque_baixo_arq() -> Path:
    return config.DADOS / "achadinhos" / "estoque_baixo.json"  # na hora (os testes trocam config.DADOS)


def alerta_estoque(est: int, onde: str = "", agora: datetime | None = None) -> str | None:
    """Estoque postável < ESTOQUE_MINIMO → grava dados/achadinhos/estoque_baixo.json (o `vendas rotina saude` mostra
    com "!!") e, no máx. 1× a cada ALERTA_CAIXA_H horas, 1 linha na Caixa de revisão. Estoque de volta ao normal →
    apaga o arquivo. Devolve o texto do alerta (None = sem alerta). Com o arquivo no lugar, a coleta da Shopee (Open
    API) roda de hora em hora em vez de a cada 2 h (shopee_afiliados.coletar_se_devido)."""
    agora = agora or datetime.now()
    try:
        ant = json.loads(estoque_baixo_arq().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        ant = {}
    if est >= ESTOQUE_MINIMO:
        estoque_baixo_arq().unlink(missing_ok=True)
        return None
    txt = (f"!! estoque postável do grupo em {est} (mínimo {ESTOQUE_MINIMO}){' — ' + onde if onde else ''} · repor: "
           "`vendas achadinhos repor` (Shopee pela Open API) e a coleta do ML na Central de Afiliados (rotina "
           "loja-shopee-rotina, passo 0c); `vendas achadinhos funil` mostra o que cada regra corta")
    na_caixa = str(ant.get("caixa") or "")
    try:
        venceu = not na_caixa or (agora - datetime.fromisoformat(na_caixa)).total_seconds() >= ALERTA_CAIXA_H * 3600
    except ValueError:
        venceu = True
    if venceu and os.environ.get("HERMES_MAQUINA") != "nuvem":  # a Caixa é do PC (o PC lê o estoque do servidor e avisa)
        try:
            from vendas import caixa
            # seção "Comigo" (Claude/Guarda resolvem): repor oferta não é tarefa da dona ("Precisa de você" é só dela)
            if caixa.anotar(f"- [ ] {agora:%Y-%m-%d} · Robô do grupo · {agora:%Hh%M} · {txt}", "Comigo"):
                na_caixa = agora.isoformat(timespec="seconds")
        except Exception:  # noqa: BLE001 — sem a Caixa, fica só o arquivo
            pass
    estoque_baixo_arq().parent.mkdir(parents=True, exist_ok=True)
    estoque_baixo_arq().write_text(json.dumps({"quando": agora.isoformat(timespec="seconds"), "estoque": est, "onde": onde,
                                             "desde": ant.get("desde") or agora.isoformat(timespec="seconds"),
                                             "caixa": na_caixa}, ensure_ascii=False), encoding="utf-8")
    return txt


def estoque_baixo(horas: float = 2) -> dict | None:
    """O alerta de estoque baixo ainda vale? (gravado nas últimas `horas` — a rodada regrava a cada 15 min)."""
    try:
        d = json.loads(estoque_baixo_arq().read_text(encoding="utf-8"))
        return d if datetime.fromisoformat(d["quando"]) >= datetime.now() - timedelta(hours=horas) else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def funil_fila(horas: int = 30, grupos: list | None = None) -> str:
    """05/10: quantas ofertas sobram depois de CADA regra da fila (e de que grupo) — responde "qual regra seca o grupo"
    sem gastar token. `grupos` simula "grupos_permitidos" sem ligar (ex.: só beleza/moda, pedido da dona em 03/10)."""
    etapas: list = []
    _candidatos(horas, so_com_link_curto=True, funil=etapas, permitidos_teste=grupos)
    linhas, antes = [], None
    for etapa, n, por in etapas:
        corte = f"  (−{antes - n})" if antes is not None and antes > n else ""
        grupos_txt = ", ".join(f"{g} {q}" for g, q in sorted(por.items(), key=lambda x: -x[1])[:6])
        linhas.append(f"{n:6d}{corte:10s} {etapa} · {grupos_txt}")
        antes = n
    for f, teto in TETO_FONTE_DIA.items():  # 09/10: fonte com teto por dia (Shein) — reserva e quanto já saiu hoje
        res = [o for o in melhores(horas, LIMITE_CANDIDATOS) if o.get("fonte") == f and not o.get("publicado_em")]
        frescas = [o for o in res if preco_fresco(o)]
        if not res and not postadas_hoje_fonte(f):
            continue
        linhas.append(f"  {f}: {len(frescas)} na reserva com preço ≤ {PRECO_VALE_H} h (de {len(res)} aprovadas) · "
                      f"teto {teto}/dia, saiu hoje {postadas_hoje_fonte(f)}")
    return "\n".join(linhas) or "sem ofertas"


def detalhe_barradas(horas: int = 30) -> str:
    """09/10 noite: abre a etapa "foto pequena, vencendo, vetada, barrada" do funil por MOTIVO (e por grupo) — para ver
    de cara se alguma regra nova está barrando demais (`vendas achadinhos funil --detalhe`). Uma oferta pode ter mais
    de um motivo; "só ela" = ofertas que caem só por aquele."""
    from vendas import revisao_fila
    agora = datetime.now()
    amazon_ok = (agora - timedelta(hours=6)).isoformat(sep=" ", timespec="seconds")
    magalu_ok = (agora - timedelta(hours=12)).isoformat(sep=" ", timespec="seconds")
    oceane_ok = (agora - timedelta(hours=OCEANE_VALIDADE_H)).isoformat(sep=" ", timespec="seconds")
    pub = publicados_recentes()
    cand = [o for o in melhores(horas, LIMITE_CANDIDATOS)
            if preco_fresco(o) and o["link_loja"] and o.get("foto") and not o["publicado_em"] and not ja_saiu(o, pub)
            and (not re.search(r"amazon\.com\.br|amzn\.to", o["link_loja"], re.I) or (o.get("atualizado_em") or "") >= amazon_ok)
            and (o.get("fonte") != "magalu_epoca" or (o.get("atualizado_em") or "") >= magalu_ok)
            and (o.get("fonte") != "oceane_afiliados" or (o.get("atualizado_em") or "") >= oceane_ok)
            and not shopee_sem_curto(o)]
    try:
        px = json.loads(_FOTO_PX_ARQ.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        px = {}
    vet = revisao_fila.vetados()
    chaves_vet = revisao_fila.chaves_vetadas(vet)
    regras = [("foto pequena (< 600 px)", lambda o, t: str(o.get("foto")).startswith("http") and px.get(o["foto"]) is not None
               and px[o["foto"]] < FOTO_MIN_PX and not ("promobit.com.br" in o["foto"] and (
                   LOJAS_FOTO.search(o.get("link_loja") or "") or foto_do_mesmo_produto(o)))),
              ("vencendo", lambda o, t: vencendo(t)),
              ("vetada pelas revisoras", lambda o, t: revisao_fila.vetada(o, vet, chaves_vet)),
              ("linha masculina", lambda o, t: eh_masculino(o)),
              ("fora do nicho", lambda o, t: fora_do_nicho_grupo(t)),
              ("título sem o tipo do produto", lambda o, t: titulo_sem_tipo(o)),
              ("farmácia/dermo na Shopee sem loja oficial", lambda o, t: farmacia_shopee_comum(o)),
              ("kit de salão na Shopee comum", lambda o, t: kit_salao_shopee(o)),
              ("'De' inflado (> 2×)", lambda o, t: de_inflado(o)),
              ("perfume de grife sem ml", lambda o, t: perfume_grife_sem_ml(o)),
              ("validade só na foto", lambda o, t: validade_na_foto(o)),
              ("fora do perfil / título ruim / preço proibido",
               lambda o, t: fora_do_perfil(t) or titulo_ruim(t) or preco_proibido(o))]
    total: dict = {}
    so: dict = {}
    grupos: dict = {}
    cortadas = 0
    for o in cand:
        t = o.get("titulo") or ""
        motivos = [nome for nome, pega in regras if pega(o, t)]
        cortadas += bool(motivos)
        for m in motivos:
            total[m] = total.get(m, 0) + 1
            g = grupos.setdefault(m, {})
            g[o.get("grupo") or "?"] = g.get(o.get("grupo") or "?", 0) + 1
        if len(motivos) == 1:
            so[motivos[0]] = so.get(motivos[0], 0) + 1
    linhas = [f"{cortadas} cortadas de {len(cand)} (com link curto, ainda não postadas)"]
    for m, n in sorted(total.items(), key=lambda x: -x[1]):
        gs = ", ".join(f"{g} {q}" for g, q in sorted(grupos[m].items(), key=lambda x: -x[1])[:5])
        linhas.append(f"{n:6d}  (só ela {so.get(m, 0):3d})  {m} · {gs}")
    return "\n".join(linhas)


# 09/10 noite (dona: "uma janela de uma hora sem post" — das 8h às 9h45 saíram 3 posts com 8–20 ofertas na fila; à tarde,
# 4–5 por rodada com 90 e a fila secou às 20h45): o ritmo antigo arredondava para CIMA, somava +1 enquanto sobrasse "1
# post a cada 3 rodadas" (= 45 min sem post) e, com pouca oferta, postava 1 a cada k rodadas (k chegou a 7 = 1h45).
MAX_SEM_POST_MIN = 45   # dona: o grupo NUNCA fica 45 min ou mais sem post entre 8h e 22h
RESERVA_POR_RODADA = 2  # estoque abaixo de 2 × rodadas que faltam = "baixo": sem +1; o +1 nunca deixa abaixo disso


def por_rodada(quantos: int, estoque_atual: int, agora: datetime, h_fim: int, intervalo_min: int = 10,
               min_sem_post: float | None = None) -> int:
    """Ritmo pelo estoque REAL postável (estoque_postavel), para durar até o fim do horário sem buraco:
    · estoque ≥ 2 × rodadas que faltam → o que dá por rodada (arredondado para BAIXO), +1 só se o que sobra continua
      ≥ 2 por rodada até o fim (dona 09/10: "põe uma a mais" — o +1 só vale quando sobra);
    · entre 1× e 2× → 1 por rodada (toda rodada tem post, sem gastar rápido);
    · menos que as rodadas → 1 post a cada 2 rodadas (30 min), NUNCA mais espaçado que MAX_SEM_POST_MIN: com
      `min_sem_post` (minutos desde o último post) posta sempre que a rodada anterior ficou vazia.
    Nunca passa de `quantos` nem do estoque."""
    faltam = max(1, (h_fim * 60 - (agora.hour * 60 + agora.minute)) // intervalo_min)
    if estoque_atual <= 0 or quantos <= 0:
        return 0
    if estoque_atual < faltam:
        k = min(-(-faltam // estoque_atual), max(1, (MAX_SEM_POST_MIN - 1) // intervalo_min))  # rodadas por post
        if min_sem_post is not None:
            return 1 if min_sem_post >= k * intervalo_min - 5 else 0  # −5: o post sai alguns minutos depois da hora
        return 1 if ((agora.hour * 60 + agora.minute) // intervalo_min) % k == 0 else 0
    base = estoque_atual // faltam
    if (estoque_atual >= RESERVA_POR_RODADA * faltam
            and estoque_atual - base - 1 >= RESERVA_POR_RODADA * (faltam - 1)):
        base += 1
    return min(quantos, base, estoque_atual)


def tipo_na_rodada(o: dict, out: list[dict]) -> bool:
    """04/10 (dona: "cílios 900 vezes"): 2 do mesmo tipo (cílios, batom, sérum…) na mesma rodada, não."""
    t = tipo_repetivel(o)
    return bool(t) and any(tipo_repetivel(x) == t for x in out)


def fila_posts(n: int = 5, horas: int = 30, so_com_link_curto: bool = True, conferir_foto: bool = True) -> list[dict]:
    """Próximas ofertas para o grupo: aprovadas, com link e foto, ainda não postadas; o melhor de cada tipo de produto
    (sem repetir tipo postado nas últimas 24 h) e variando categoria (sem 3 iguais seguidas).
    01/10 (dona): Shopee SEMPRE com link curto oficial → sem ele a oferta espera (so_com_link_curto)."""
    cand, recentes, amazon_ok = _candidatos(horas, so_com_link_curto)
    cand = dentro_do_teto_dia(cand)  # 09/10 (dona): Shein no máx. 1 POR DIA (TETO_FONTE_DIA)
    # 05/10: a lista da revisora (nuvem._linhas_da_fila) só precisa do TEXTO — sem baixar e medir 40 fotos (prendia a
    # rodada do servidor). Quem posta continua conferindo a foto de cada uma.
    foto_ok = foto_boa if conferir_foto else (lambda o: True)
    # linha do grupo (usuário): beleza/cabelo/perfume primeiro → em cada 5 posts, 3 da linha principal e 2 das outras
    # (fitness, casa, bebê, moda, pet), sempre o de maior score de cada lado; sem 3 da mesma categoria seguidas
    out, ultimo = [], []
    # 08/10 (revisão: 105 posts SEGUIDOS da Shopee em 07/10): no máx. MAX_LOJA_SEGUIDA seguidos da mesma loja, contando
    # o fim da rodada anterior (últimos posts no banco); só passa disso se a fila só tiver essa loja
    antes = [loja_de(dict(r)) for r in db.consultar(
        "SELECT loja, link_loja, fonte FROM ofertas WHERE publicado_em IS NOT NULL ORDER BY publicado_em DESC "
        f"LIMIT {max(MAX_LOJA_SEGUIDA, LOJA_JANELA - 1)}") or []][::-1]
    lojas_cand = {loja_de(x) for x in cand}
    # 08/10 noite (Beto 07/10: 22 perfumes seguidos na fila e 8 numa rodada): no máx. PERFUME_MAX_JANELA perfumes em
    # cada PERFUME_JANELA posts, contando o fim da rodada anterior. As vagas do TEMA do dia (ex.: "Quarta do Perfume",
    # pedido da dona em 07/10) e os pedidos da dona não têm esse teto — mas contam na janela
    perf_antes = [eh_perfume_post(dict(r)) for r in db.consultar(
        "SELECT titulo, grupo FROM ofertas WHERE publicado_em IS NOT NULL ORDER BY publicado_em DESC "
        f"LIMIT {PERFUME_JANELA - 1}") or []][::-1]

    def perfume_cabe(o: dict) -> bool:
        if not eh_perfume_post(o):
            return True
        seq = perf_antes + [eh_perfume_post(x) for x in out]
        return sum(seq[-(PERFUME_JANELA - 1):]) < PERFUME_MAX_JANELA

    def sem_rajada(lista: list, lojas_resto) -> list:
        seq = antes + [loja_de(x) for x in out]
        bons = [x for x in lista if loja_cabe(loja_de(x), seq)]
        if len(bons) == len(lista):
            return lista
        # só passa da regra se NENHUMA outra loja da fila cabe agora (o grupo não para)
        return bons if any(loja_cabe(lj, seq) for lj in set(lojas_resto)) else lista
    # 07/10: o que a dona pediu sai primeiro (1 tipo por rodada). 08/10 (Vitória: 33 do ML seguidos das 13h30 às 15h31,
    # todos pedidos) → no máx. metade da rodada é pedido e no máx. 1 por loja por rodada (o resto mistura)
    lojas_ped: dict = {}
    # 09/10 (dona: Océane ≤ R$ 90 "sempre dar um jeito de pôr", sem quebrar o mix 7/2/1): o pedido da Océane só entra
    # quando a próxima vaga do PADRAO_LINHA é de beleza ("B") — ocupa a vaga B, e o padrão segue igual
    pos_hoje = (db.consultar("SELECT COUNT(*) n FROM ofertas WHERE date(publicado_em) = date('now','localtime')")
                or [{"n": 0}])[0]["n"]
    for o in [x for x in cand if x.get("pedido_dona")]:
        if len(out) >= max(1, n // 2):
            break
        loja = (o.get("loja") or o.get("fonte") or "")[:20]
        if lojas_ped.get(loja) or not sem_rajada([o], lojas_cand):
            continue
        if o.get("fonte") in FONTES_SO_BELEZA and not PADRAO_LINHA[(pos_hoje + len(out)) % len(PADRAO_LINHA)].startswith("B"):
            continue
        if not tipo_na_rodada(o, out) and foto_ok(o):
            lojas_ped[loja] = 1
            out.append(o)
            ultimo.append(o["grupo"])
        cand.remove(o)
    # o pedido que ficou de fora pelo limite espera a PRÓXIMA rodada (não entra por outra vaga nesta)
    cand = [x for x in cand if not x.get("pedido_dona")]
    # 30/09: 1 oferta de GRIFE abre cada rodada (o vídeo de divulgação promete "itens de marca por preço de verdade" no
    # grupo — tem que ser verdade quando a pessoa entra): a de maior desconto em reais, 'de' ≥ R$ 200, ≥ 25% off
    fresco = (datetime.now() - timedelta(hours=12)).isoformat(sep=" ", timespec="seconds")
    # 01/10: a vaga abre ~56 rodadas/dia e a grife inédita ACABAVA (sobrava 1) → grife ainda em promoção pode voltar
    # depois de 2 dias (quem entrou no grupo nesse meio tempo não viu; chegam só ~13 grifes boas por dia) e "de" a
    # partir de R$ 150
    # 08/10 (Beto: Laneige de 06/10 voltou em 08/10 por aqui) → só depois de REPETIR_DIAS, com preço fresco e "De" no post
    volta = (datetime.now() - timedelta(days=REPETIR_DIAS)).isoformat(sep=" ", timespec="seconds")
    from vendas import revisao_fila
    vet = revisao_fila.vetados()  # 06/10: a grife que VOLTA também respeita o veto (pelo produto)
    chaves_vet = revisao_fila.chaves_vetadas(vet)
    pub = publicados_recentes()
    pool = cand + [o for o in sem_repetidos(melhores(horas, LIMITE_CANDIDATOS)) if o["publicado_em"] and o["publicado_em"] < volta
                   and o["link_loja"] and o.get("foto") and chave_produto(o["titulo"]) not in recentes
                   and not (so_com_link_curto and shopee_sem_curto(o)) and not barrada_na_hora(o)
                   and preco_fresco(o) and tem_de_no_post(o) and not ja_saiu(o, pub)
                   and not revisao_fila.vetada(o, vet, chaves_vet)]
    grife = [o for o in pool if LUXO.search(o["titulo"] or "") and (o.get("preco_antigo") or 0) >= 150
             and (o.get("desconto") or 0) >= 25 and not MASCULINO.search(o["titulo"] or "")
             and (o.get("atualizado_em") or "") >= fresco  # preço visto há pouco (grife muda rápido)
             and (not so_feminino_ligado() or no_perfil_feminino(o))
             and (not re.search(r"amazon\.com\.br|amzn\.to", o["link_loja"], re.I) or (o.get("atualizado_em") or "") >= amazon_ok)]
    # 01/10 (dona: "segue o ritmo do grupo das mulheres", ~108/dia → 2 por rodada): o padrão de 10 vagas CONTINUA de uma
    # rodada para a outra (pelo nº de posts de hoje) — senão toda rodada começaria em "B, M" e nunca sairia variado,
    # masculino nem grife. A grife abre a rodada que começa um novo ciclo de 10 (com 10 por rodada: toda rodada).
    pos = (db.consultar("SELECT COUNT(*) n FROM ofertas WHERE date(publicado_em) = date('now','localtime')")
           or [{"n": 0}])[0]["n"]
    from vendas import datas
    tema = datas.tema_do_dia()  # 01/10 (dona): 2 dias temáticos por semana ("Quarta do Perfume") → 9 em 10 do tema
    vez_grife = (n >= len(PADRAO_LINHA) or pos % len(PADRAO_LINHA) < n) and not tema
    for top in sorted(sem_rajada([o for o in grife if perfume_cabe(o)], lojas_cand),
                      key=lambda o: (o["preco_antigo"] or 0) - o["preco"], reverse=True)[:8] if vez_grife else []:
        if top in cand:
            cand.remove(top)
        if foto_ok(top):  # 30/09 (dona): SEMPRE verificar a imagem antes de enviar
            out.append(top)
            ultimo.append(top["grupo"])
            break
    casa_bonita = lambda o: o["grupo"] == "casa" and bool(CASA_BONITA.search(o.get("titulo") or ""))  # noqa: E731
    filas = {"B": [o for o in cand if o["grupo"] in LINHA_PRINCIPAL], "M": [o for o in cand if o["grupo"] == "moda"],
             "C": [o for o in cand if casa_bonita(o)],  # 07/10: vaga de casa bonita
             "O": [o for o in cand if o["grupo"] not in LINHA_PRINCIPAL and o["grupo"] != "moda" and not casa_bonita(o)]}
    if datas.chegando():  # data grande chegando (Dia das Crianças, Natal…): o que é ligado a ela sobe na fila
        for f in filas.values():
            f.sort(key=lambda o: (o.get("score") or 0) + datas.bonus(o), reverse=True)
    # 01/10 (Marcos): 1 lojista da Shopee fez 27% dos posts (todos com ~70% "de" fixo) → no máx. 1 por rodada
    por_vendedor = {vendedor(o): 1 for o in out if vendedor(o)}
    # 01/10 (Mila): com 3 por rodada, "1 por rodada" ainda deu 33% de um lojista → 1 por lojista a cada 10 posts
    for r in db.consultar("SELECT link_loja FROM ofertas WHERE publicado_em >= datetime('now','localtime','-4 hours') "
                          "ORDER BY publicado_em DESC LIMIT 9") or []:
        if vendedor(dict(r)):
            por_vendedor[vendedor(dict(r))] = 1
    tentativas = 0
    if tema:
        k = datas.vagas_do_tema(pos, len(out), n, int(tema.get("em_10", datas.FOCO)))
        for o in sorted((o for o in cand if datas.casa(o, tema)),
                        key=lambda o: (o.get("score") or 0) + datas.bonus(o), reverse=True):
            if k <= 0 or tentativas > n * 10:
                break
            if por_vendedor.get(vendedor(o)) or tipo_na_rodada(o, out) or not sem_rajada([o], lojas_cand):
                continue
            tentativas += 1
            for f in filas.values():
                if o in f:
                    f.remove(o)
            if foto_ok(o):
                out.append(o)
                ultimo.append(o["grupo"])
                k -= 1
                if vendedor(o):
                    por_vendedor[vendedor(o)] = 1
    moda_hoje: dict = {}  # 09/10: tipos de moda que já saíram hoje (a vaga "Mv" prefere o que menos saiu)
    for r in db.consultar("SELECT titulo, grupo FROM ofertas WHERE date(publicado_em) = date('now','localtime')") or []:
        if dict(r).get("grupo") == "moda":
            tp = tipo_moda(dict(r).get("titulo") or "")
            moda_hoje[tp] = moda_hoje.get(tp, 0) + 1
    inicio = (pos + len(out)) % len(PADRAO_LINHA)
    for vez in (PADRAO_LINHA[inicio:] + PADRAO_LINHA * (n // len(PADRAO_LINHA) + 12)):
        if len(out) >= n or not any(filas.values()) or tentativas > n * 10:
            break
        fila = filas[vez[0]] or next((filas[k] for k in ("B", "M", "C", "O") if filas[k]), [])

        def _livres(f: list, variar: bool = True) -> list:
            lv = [x for x in f if not (variar and len(ultimo) >= 2 and ultimo[-1] == ultimo[-2] == x["grupo"])
                  and not por_vendedor.get(vendedor(x)) and not tipo_na_rodada(x, out)]
            return sem_rajada(lv, (loja_de(y) for g in filas.values() for y in g))  # 08/10: 4º seguido da loja, não
        livres = _livres(fila)
        # 08/10 noite: teto de perfume — se a vaga só tem perfume e o teto encheu, vai o próximo NÃO perfume desta fila
        # ou de outra (se preciso, um 3º seguido da mesma categoria — beleza é a linha do grupo); só passa do teto se não
        # sobrar nada que não seja perfume ou se a rodada ainda não tem nenhum post (o grupo não para)
        cabem = [x for x in livres if perfume_cabe(x)]
        if livres and not cabem:
            for k, variar in [(k, v) for v in (True, False) for k in ("B", "M", "C", "O")]:
                if alt := [x for x in _livres(filas[k], variar) if perfume_cabe(x)]:
                    fila, cabem = filas[k], alt
                    break
            else:
                if not out or all(eh_perfume_post(y) for g in filas.values() for y in g):
                    cabem = livres
        livres = cabem
        # 01/10 (dona): "tem que ter coisas masculinas também" → vagas "h" preferem produto masculino; as outras, não
        quer_h = vez.endswith("h")

        def _pref(x: dict, vez: str = vez) -> int:
            """09/10: "Mb" quer bolsa, "Ms" sapato, "Mv" o tipo de moda que menos saiu hoje (varia bolsa/sapato/
            acessório/pijama); as outras vagas não têm preferência."""
            if not vez.startswith("M") or x.get("grupo") != "moda":
                return 0
            tp = tipo_moda(x.get("titulo") or "")
            if vez.endswith("b"):
                return int(tp != "bolsa")
            if vez.endswith("s"):
                return int(tp != "sapato")
            if vez.endswith("v"):
                return moda_hoje.get(tp, 0) + sum(tipo_moda(y.get("titulo") or "") == tp for y in out
                                                  if y.get("grupo") == "moda")
            return 0
        # 01/10 (Marcos): foto ruim no 1º da fila PERDIA a vaga (o "variado" sumia) → tenta o próximo da mesma vaga
        for o in sorted(livres, key=lambda x: (eh_masculino(x) != quer_h, _pref(x)))[:6]:
            fila.remove(o)
            tentativas += 1
            if foto_ok(o):  # foto pequena/borrada de origem → não vai pro grupo (fica pro site, que usa miniatura)
                out.append(o)
                ultimo.append(o["grupo"])
                if vendedor(o):
                    por_vendedor[vendedor(o)] = 1
                break
    if not out and n > 0:
        # 09/10 noite (dona: o grupo NUNCA fica 45 min sem post): rodada com oferta na fila e NENHUMA vaga preenchida só
        # por regra de ESPAÇAMENTO (lojista 1 a cada 10 posts, 3ª seguida da categoria, 4ª seguida da loja, teto de
        # perfume) — em 09/10, 20h45–21h15, "estoque 5 → 0 postadas de 0". Vai a melhor que sobrou com foto boa; as
        # regras da OFERTA (foto, veto, "De", nicho, preço fresco) já foram todas aplicadas em _candidatos.
        for o in dentro_do_teto_dia([x for k in ("B", "M", "C", "O") for x in filas[k]])[:12]:
            if foto_ok(o):
                out.append(o)
                break
    return dentro_do_teto_dia(out)  # 09/10: guarda final (a grife/pool não pode furar o teto da Shein)


# 09/10 (dona: "vamos pôr poucas coisas deles" → "Shein é 1 por DIA no grupo, só isso"): teto RÍGIDO de posts por dia de
# uma fonte, contando o que já saiu hoje (publicado_em) — vale para todas as vagas, pedido da dona e grife inclusive
TETO_FONTE_DIA = {"lomadee_shein": 1}


def postadas_hoje_fonte(fonte: str) -> int:
    r = db.consultar("SELECT COUNT(*) n FROM ofertas WHERE fonte = ? AND date(publicado_em) = date('now','localtime')",
                     (fonte,))
    return (r or [{"n": 0}])[0]["n"]


def dentro_do_teto_dia(lista: list[dict]) -> list[dict]:
    """Tira da lista o que passaria do TETO_FONTE_DIA (mantém a ordem; da fonte com teto fica só a 1ª que ainda cabe)."""
    sobra = {f: t - postadas_hoje_fonte(f) for f, t in TETO_FONTE_DIA.items()}
    out = []
    for o in lista:
        f = o.get("fonte")
        if f in sobra:
            if sobra[f] <= 0:
                continue
            sobra[f] -= 1
        out.append(o)
    return out


MAX_LOJA_SEGUIDA = 3  # 08/10 (revisão: 105 da Shopee seguidos em 07/10) — ver fila_posts
# 08/10 noite (Beto: 22 perfumes seguidos e 8 numa rodada): a linha padrão (PADRAO_LINHA) é beleza variada — perfume no
# máx. 2 em cada 10 posts (fora as vagas do tema do dia e os pedidos da dona)
PERFUME_JANELA, PERFUME_MAX_JANELA = 10, 2


def eh_perfume_post(o: dict) -> bool:
    """Perfume de gente (para o teto da fila): categoria perfume ou título de perfume — perfume capilar não conta."""
    t = o.get("titulo") or ""
    return eh_perfume(t, o.get("grupo")) and not re.search(r"(?i)capilar|cabelos?\b|\bhair\b", t)
# 08/10 (Beto: 3 Océane em 5 posts, 2 coladas): loja que NÃO é marketplace (Océane, Época, Doce Beleza, Netshoes…) sai
# espaçada — nunca 2 seguidas e no máx. LOJA_MAX_JANELA em cada LOJA_JANELA posts. Marketplace segue MAX_LOJA_SEGUIDA.
LOJA_JANELA, LOJA_MAX_JANELA = 5, 2
MARKETPLACES = ("shopee", "mercadolivre", "amazon", "magalu")


def loja_cabe(loja: str, seq: list[str]) -> bool:
    """A próxima oferta da `loja` pode sair depois da sequência de lojas `seq` (a mais recente no fim)?"""
    if loja in MARKETPLACES:
        ult = seq[-MAX_LOJA_SEGUIDA:]
        return not (len(ult) >= MAX_LOJA_SEGUIDA and all(lj == loja for lj in ult))
    return not seq or (seq[-1] != loja and seq[-(LOJA_JANELA - 1):].count(loja) < LOJA_MAX_JANELA)
_REDES = (("shopee", re.compile(r"shopee|shope\.ee", re.I)),
          ("mercadolivre", re.compile(r"mercado ?livre|mercadoli(?:vre|bre)\.com|meli\.la", re.I)),
          ("amazon", re.compile(r"amazon|amzn\.", re.I)),
          ("magalu", re.compile(r"magalu|magazine ?luiza|magazineluiza", re.I)))


@functools.lru_cache(maxsize=50000)
def _loja_nome(loja: str, link: str, fonte: str) -> str:
    for alvo in (loja, link):
        for nome, rx in _REDES:
            if rx.search(alvo):
                return nome
    return re.sub(r"\W+", "", loja.lower()) or fonte or "?"


def loja_de(o: dict) -> str:
    """08/10: loja da oferta — 'shopee', 'mercadolivre', 'amazon', 'magalu' ou o nome da loja (Promobit: Netshoes…)."""
    return _loja_nome(o.get("loja") or "", o.get("link_loja") or "", o.get("fonte") or "")


def vendedor(o: dict) -> str:
    """Lojista da Shopee pelo link do produto (shopee.com.br/product/<loja>/<item> ou …-i.<loja>.<item>)."""
    m = re.search(r"shopee\.com\.br/(?:product/)?(\d+)/\d+|-i\.(\d+)\.\d+", o.get("link_loja") or "")
    return f"shopee:{m.group(1) or m.group(2)}" if m else ""


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


# 05/10 (Mila, 02/10: "grife 0 de 106" — Beauty Box/Sephora vinham da Promobit com foto de 400 px e a vaga se perdia):
# a foto grande vem da própria página do produto (og:image), 1 vez por link (cache em foto_loja.json)
LOJAS_FOTO = re.compile(r"(beautybox|sephora|epocacosmeticos|belezanaweb|boticario|natura|eudora|quemdisseberenice)"
                        r"\.com\.br", re.I)
_FOTO_LOJA_ARQ = config.DADOS / "achadinhos" / "foto_loja.json"
_OG_IMAGE = (re.compile(r"""<meta[^>]+property=["']og:image(?::secure_url)?["'][^>]*content=["']([^"']+)""", re.I),
             re.compile(r"""<meta[^>]+content=["']([^"']+)["'][^>]*property=["']og:image["']""", re.I))


def foto_da_loja(link: str) -> str | None:
    """Foto principal (og:image) da página do produto na loja; None se não achar. Cache por link."""
    try:
        cache = json.loads(_FOTO_LOJA_ARQ.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        cache = {}
    if link not in cache:
        url = ""
        try:
            html = httpx.get(link, timeout=10, follow_redirects=True, headers=UA).text
            m = next((m for rx in _OG_IMAGE if (m := rx.search(html))), None)
            url = m.group(1).replace("&amp;", "&") if m and m.group(1).startswith("https://") else ""
        except Exception:  # noqa: BLE001 — loja fora do ar = fica com a foto que tinha
            pass
        cache[link] = url
        if len(cache) > 3000:
            cache = dict(list(cache.items())[-2500:])
        _FOTO_LOJA_ARQ.parent.mkdir(parents=True, exist_ok=True)
        _FOTO_LOJA_ARQ.write_text(json.dumps(cache), encoding="utf-8")
    return cache[link] or None


def foto_boa(o: dict) -> bool:
    """30/09 (dona: "sempre que for enviar a imagem, verifique"): só vai pro grupo foto com ≥ FOTO_MIN_PX de verdade.
    Oferta da Promobit que aponta pro ML: usa a foto em alta da coleta do ML do MESMO anúncio, se tivermos.
    05/10: Promobit → Beauty Box/Sephora/Época…: tenta a foto grande da página do produto."""
    f = o.get("foto") or ""
    # 05/10 (dona: "não é pra ficar barrando — corrija e mande"): CORRIGE trocando pela foto REAL grande do mesmo
    # produto — a da coleta do ML/Amazon (mesmo MLB/ASIN) ou a da página da loja (Beauty Box, Natura, Beleza na Web…)
    if "promobit.com.br" in f and (alt := foto_do_mesmo_produto(o)) and foto_px(alt) >= FOTO_MIN_PX:
        o["foto"] = f = alt
    if "promobit.com.br" in f and LOJAS_FOTO.search(o.get("link_loja") or "") and foto_px(f) < FOTO_MIN_PX:
        alt = foto_da_loja(o["link_loja"])
        if alt and foto_px(alt) >= FOTO_MIN_PX:
            o["foto"] = f = alt
    if f and not f.startswith("http") and Path(f).exists() and _AMPLIADAS not in Path(f).parents:
        return True  # arquivo nosso já pronto (kit da loja etc.)
    # 07/10 (verificador do servidor 68%: 7 fotos de 400 px da Promobit e 1 de 442 px da Shopee saíram entre 18h e 21h,
    # com a fila curta): só foto REAL com ≥ 600 px — sem foto boa do mesmo produto, a oferta é PULADA (a ampliação de
    # 05/10 esticava 400 px para 900 e ia borrada; ampliar_foto ficou fora do grupo)
    return bool(f) and foto_px(f) >= FOTO_MIN_PX


def foto_do_mesmo_produto(o: dict, fotos: dict | None = None) -> str | None:
    """07/10: foto do MESMO produto (mesmo MLB do ML ou ASIN da Amazon) já coletada por outra fonte do radar — a
    Promobit guarda ~400 px; a coleta do ML/Amazon tem a foto grande. Só olha o banco (nada de internet).
    `fotos` = {id: foto} já lido (fotos_de_outras_fontes) para muitas ofertas de uma vez."""
    from vendas.revisao_fila import chave_item
    c = chave_item("", o.get("link_loja")) or ""
    pre, _, num = c.partition(":")
    alvo = {"mlb": f"mlaf:MLB{num}", "amz": f"amzref:{num}"}.get(pre)
    if not alvo or alvo == o.get("id"):
        return None
    if fotos is not None:
        return fotos.get(alvo)
    r = db.consultar("SELECT foto FROM ofertas WHERE id = ? AND foto LIKE 'http%'", (alvo,))
    return r[0]["foto"] if r else None


def fotos_de_outras_fontes() -> dict:
    """{id: foto} das coletas do ML e da Amazon (para foto_do_mesmo_produto em lote)."""
    return {r["id"]: r["foto"] for r in db.consultar(
        "SELECT id, foto FROM ofertas WHERE (id LIKE 'mlaf:%' OR id LIKE 'amzref:%') AND foto LIKE 'http%'")}


_AMPLIADAS = config.DADOS / "achadinhos" / "fotos_ampliadas"


def ampliar_foto(url: str, alvo: int = 900, minimo: int = 280) -> str | None:
    """Foto real pequena (≥ `minimo` px) → mesma foto ampliada para `alvo` px (LANCZOS + nitidez leve), em quadrado
    branco se não for quadrada. Devolve o caminho do arquivo (dentro do projeto, aceito pelo postar) ou None."""
    import hashlib
    import io

    from PIL import Image, ImageFilter
    destino = _AMPLIADAS / (hashlib.sha1(url.encode()).hexdigest()[:16] + ".jpg")
    if destino.exists():
        return str(destino)
    try:
        r = httpx.get(url, timeout=20, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"})
        im = Image.open(io.BytesIO(r.content)).convert("RGB")
    except Exception:  # noqa: BLE001
        return None
    if max(im.size) < minimo:
        return None
    esc = alvo / max(im.size)
    im = im.resize((round(im.width * esc), round(im.height * esc)), Image.LANCZOS)
    im = im.filter(ImageFilter.UnsharpMask(radius=1.6, percent=60, threshold=2))
    tela = Image.new("RGB", (alvo, alvo), "white")
    tela.paste(im, ((alvo - im.width) // 2, (alvo - im.height) // 2))
    _AMPLIADAS.mkdir(parents=True, exist_ok=True)
    tela.save(destino, quality=92)
    return str(destino)


LINHA_PRINCIPAL = ("beleza", "cabelo", "perfume")
FONTES_SO_BELEZA = ("oceane_afiliados",)  # 08/10: o grupo da oferta não sai da linha de beleza no reavaliar()
# 08/10: Océane é coletada 1×/dia (de manhã, integracoes/oceane.py) e a marca exige preço oficial → preço visto há até
# 14 h (a coleta das 9h vale até as 23h, o fim do grupo; a de ontem não sai hoje)
OCEANE_VALIDADE_H = 14
# 01/10 (dona + estudo do "Ofertas Entre Mulheres": ~58% beleza / 31% moda / 11% resto) → em cada 10: 6 beleza, 3 moda,
# 1 variado (casa, pet, esporte, infantil, eletrônicos); + a grife que abre a rodada = ~70% beleza
# 01/10 (dona): também público MASCULINO → 2 das 10 vagas ("Bh" beleza, "Mh" moda) preferem produto masculino
# 05/10 (dona: "vamos focar: achadinhos de BELEZA — dermo, make, skincare, cabelo, perfume, bolsa e acessório de luxo;
# tirar brinquedo e o resto"): sem vaga de variado nem de masculino → 8 beleza/cabelo/perfume + 2 bolsa/joia
# 07/10 (dona: "pode incluir coisas de casa bonito"): 1 vaga "C" de casa bonita (decoração, aroma, penteadeira, cama e
# banho); sem casa bonita na fila, a vaga volta para beleza/moda
# 09/10 (dona: "vamos dar uma variada, colocando bolsa, sapato, acessório, essas coisas, pijama e tal") → 6 beleza /
# 3 moda / 1 casa bonita: "Mb" prefere bolsa, "Ms" sapato, "Mv" o tipo de moda (tipo_moda) que menos saiu hoje
# (acessório, pijama…). Sem moda na fila, a vaga volta para beleza (filas vazias caem na próxima)
PADRAO_LINHA = ("B", "Mb", "B", "B", "C", "B", "Ms", "B", "Mv", "B")
# 03/10 (dona: "sinto necessidade de bolsas no grupo" — 11 bolsas em ~420 posts): a 1ª vaga de moda prefere BOLSA
BOLSA = re.compile(r"\bbolsas?\b|\bclutch\b|\btote\b|transversal|tiracolo|baguete", re.I)
NAO_BOLSA = re.compile(r"t[ée]rmica|marmita|lancheira|mochila|escolar|necessaire|cosm[ée]tic|maternidade|viagem", re.I)


def eh_masculino(o: dict) -> bool:
    t = o.get("titulo") or ""
    return bool(MASCULINO.search(t)) and not re.search(r"feminin|unissex|mulher|calcinha|pour femme|for her\b", t, re.I)


def marcar_postado(id_oferta: str) -> None:
    with db.conectar() as con:
        con.execute("UPDATE ofertas SET publicado_em = datetime('now','localtime') WHERE id = ?", (id_oferta,))
    if os.environ.get("HERMES_MAQUINA") == "nuvem":  # 02/10: o que o SERVIDOR postou (o PC traz e usa no plano B)
        arq = config.DADOS / "nuvem" / "meus_posts.jsonl"
        arq.parent.mkdir(parents=True, exist_ok=True)
        with open(arq, "a", encoding="utf-8") as f:
            f.write(json.dumps({"id": id_oferta, "publicado_em": datetime.now().isoformat(sep=" ", timespec="seconds")})
                    + "\n")


def reavaliar() -> int:
    """Reaplica as regras (que mudam com os pedidos do usuário) em tudo que está no banco."""
    _tabela()
    n = 0
    with db.conectar() as con:
        for r in con.execute("SELECT * FROM ofertas").fetchall():
            o = dict(r)
            o["sinais"] = json.loads(o["sinais"] or "{}")
            g = _grupo_final(o["grupo"], o["titulo"])
            # 08/10: loja que só vende beleza (Océane) não troca de grupo ("Toalha Demaquilante" virava casa e saía)
            o["grupo"] = (o["grupo"] if o.get("fonte") in FONTES_SO_BELEZA and o["grupo"] in LINHA_PRINCIPAL
                          and g not in LINHA_PRINCIPAL else g)
            if o.get("fonte") == FONTE_SHEIN:  # 09/10: Shein entra só como moda (bolsa/sapato/acessório/pijama)
                o["grupo"] = "moda"
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


def promo_valendo(p: dict, agora: datetime | None = None) -> dict | None:
    """Promoção REAL do produto da loja (config/midia.json → promo) valendo AGORA, ou None.
    06/10 (Otto): promo renovada até 31/10, mas 10/10 é só a Oferta Relâmpago (outro preço) → `pausa` = [de, até]."""
    pr = p.get("promo") or {}
    agora_txt = (agora or datetime.now()).strftime("%Y-%m-%d %H:%M")
    pausa = pr.get("pausa") or ["", ""]
    if (pr and pr.get("por") and pr.get("desde", "") <= agora_txt <= pr.get("ate", "")
            and not (pausa[0] and pausa[0] <= agora_txt <= pausa[1])):
        return pr
    return None


def preco_loja_site(p: dict, agora: datetime | None = None) -> str:
    """07/10 (dona: "os preços da nossa lojinha no site tão desatualizados"): o site mostrava o preço CHEIO (R$ 24,90)
    com a promoção da Shopee a R$ 18,00 valendo → mostra o preço de agora, com o "de"."""
    pr = promo_valendo(p, agora)
    if pr:
        return f"{pr['por']} na {pr.get('canal', 'Shopee')} (de {pr['de']})"
    return p.get("preco", "")


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
        lista.append({"nome": p["nome"], "preco": preco_loja_site(p), "links": p["links"], "foto": f"loja/{sku}.jpg"})
    # 06/10 (Kátia, dona: "impulsiona os KITS no ML"): os kits do ML entram na vitrine do SITE (não é WhatsApp/Telegram;
    # anúncio próprio, sem etiqueta de afiliado) — só os ativos com estoque disponível no Hermes
    try:
        from vendas import estoque
        disp = {a["id_externo"]: a["disponivel"] for a in estoque.anuncios() if a["canal"] == "ml" and a["ativo"]}
    except Exception:  # noqa: BLE001 — vitrine é secundária
        disp = {}
    for k in json.loads((config.CONFIG / "midia.json").read_text(encoding="utf-8")).get("kits_ml") or []:
        foto = config.RAIZ / k.get("foto", "")
        if k.get("ativo") is False or not (foto.is_file() and disp.get(k["mlb"], 0) > 0):  # 06/10: Íngrid reprovou
            continue
        destino = pasta / "loja" / f"kit_{k['mlb']}.jpg"
        if not destino.exists() or destino.stat().st_mtime < foto.stat().st_mtime:
            im = Image.open(foto).convert("RGB")
            im.thumbnail((500, 500))
            im.save(destino, quality=85)
        lista.append({"nome": k["nome"], "preco": k.get("preco", ""), "foto": f"loja/kit_{k['mlb']}.jpg",
                      "links": {"Mercado Livre": f"https://produto.mercadolivre.com.br/{k['mlb'][:3]}-{k['mlb'][3:]}"}})
    (radar / "config" / "loja.json").write_text(json.dumps(lista, ensure_ascii=False, indent=1), encoding="utf-8")


def _reclonar_site(pasta, git: list) -> None:
    """Troca um .git corrompido do site por um clone novo (só histórico, sem baixar arquivos: --filter=blob:none) e
    remonta o índice a partir do main da nuvem; os arquivos da pasta não são tocados. O .git velho fica ao lado."""
    import shutil
    import subprocess
    url = "https://bellalucce@github.com/bellalucce/bellalucce.github.io.git"
    tmp = pasta.parent / "pages_reclone"
    if tmp.exists():
        shutil.rmtree(tmp)
    subprocess.run(["git", "clone", "-q", "--no-checkout", "--filter=blob:none", url, str(tmp)], check=True, timeout=900)
    (pasta / ".git").rename(pasta.parent / f"pages_git_quebrado_{time.strftime('%Y%m%d_%H%M')}")
    (tmp / ".git").rename(pasta / ".git")
    tmp.rmdir()
    subprocess.run(git + ["config", "credential.helper", "manager"], check=True)
    subprocess.run(git + ["reset", "-q"], check=True)


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
    publicos = {k: af[k] for k in ("amazon_tag", "magalu_loja", "ml_tool", "ml_etiqueta", "shopee_id") if k in af}
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
    # 05/10: logo escolhida pela dona (monograma creme + caramelo, Marca/Achadinhos/site); sem ela, a antiga de dados/marca
    nova = config.RAIZ / "Marca" / "Achadinhos" / "site"
    for origem, destino in (("logo_256.png", "logo.png"), ("favicon.png", "favicon.png")):
        fonte = nova / destino if (nova / destino).exists() else config.DADOS / "marca" / origem
        if fonte.exists():
            shutil.copyfile(fonte, pasta / destino)
    git = ["git", "-C", str(pasta), "-c", "user.name=bellalucce", "-c", "user.email=bellalucce@users.noreply.github.com",
           "-c", "credential.helper=", "-c", "credential.helper=manager"]
    # 09/10: o PC desligou no meio de um push (08/10 17:32) e zerou index/refs do .git → site parado 3 h. O clone
    # local não guarda nada só dele (a nuvem é a fonte), então: .git quebrado vai para o lado e vem um clone novo.
    ok = subprocess.run(git + ["rev-parse", "-q", "--verify", "HEAD"], capture_output=True).returncode == 0
    r = subprocess.run(git + ["add", "-A"], capture_output=True, text=True) if ok else None
    if r is None or r.returncode != 0:
        _reclonar_site(pasta, git)
        r = subprocess.run(git + ["add", "-A"], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"git add falhou: {r.stderr.strip()[:200]}")
    if subprocess.run(git + ["diff", "--cached", "--quiet"]).returncode == 0:
        return "radar na nuvem: nada novo"
    subprocess.run(git + ["commit", "-qm", "Radar: dados/código do PC"], check=True)
    subprocess.run(git + ["pull", "-q", "--rebase", "origin", "main"], capture_output=True, text=True, timeout=120)
    r = subprocess.run(git + ["push", "-q", "origin", "main"], capture_output=True, text=True, timeout=120)
    return "radar na nuvem atualizado" if r.returncode == 0 else f"push falhou: {r.stderr.strip()[:200]}"
