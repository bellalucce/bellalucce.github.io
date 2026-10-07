"""Repertório de ganchos do grupo de achadinhos (30/09, pedido da dona: "cria você as frases, analisa o produto… não fica
Ctrl-C Ctrl-V das mesmas frases dos grupos, pra não ficar maçante").

Como funciona:
- `achadinhos.beneficio()` descobre o TIPO do produto (a chave de REPERTORIO = a frase-base de cada tipo).
- Aqui ficam várias frases por tipo e frases por CARACTERÍSTICA que aparece no título (vindas da análise das palavras
  mais usadas nos títulos reais de cada tipo: "2000W"/"íon" no secador, "zero transparência" na legging, "400 fios" no
  lençol, "com cor" no protetor…). A característica vence a frase genérica do tipo.
- `escolher()` evita repetir o que saiu nos últimos posts (`recentes`, do mais antigo ao mais novo).
Regras de texto: benefício real e humano, sem promessa de tratamento ("elimina", "cura", "acaba com"), sem urgência
inventada, curto (cabe numa linha do celular), 1 emoji.
"""
import re
from datetime import date

# marcas coreanas (K-beauty) — 30/09: COSRX, Beauty of Joseon, Skin1004, Anua, TIRTIR, Laneige… as mais vendidas no BR
KB = (r"(?<!\w)(?:cosrx|beauty of joseon|anua|skin ?1004|laneige|medicube|tirtir|torriden|round ?lab|celimax|missha|"
      r"klairs|some ?by ?mi|biodance|mediheal|dr\.? ?jart|numbuzin|isntree|purito|innisfree|sulwhasoo|abib|mixsoon|"
      r"axis-?y|d'?alba|beplain|haruharu|banila|tocobo|coreanos?|coreanas?|k-?beauty)(?!\w)")  # "anua" ≠ "anual"

REPERTORIO: dict[str, list[str]] = {
    "PELE PROTEGIDA SEM PESAR NO BOLSO ☀️": [
        "PELE PROTEGIDA SEM PESAR NO BOLSO ☀️", "PROTETOR BOM NÃO PODE FALTAR ☀️", "SOL SEM MEDO, AMIGA ☀️",
        "O PASSO DO SKINCARE QUE NÃO SE PULA ☀️"],
    "CÍLIOS DE BONECA POR ESSE PREÇO? 👀": [
        "CÍLIOS DE BONECA POR ESSE PREÇO? 👀", "OLHAR PODEROSO NUMA PASSADA 👀", "MÁSCARA QUE ABRE O OLHAR 👀"],
    "PELE LISINHA E COM VIÇO, AMIGA ✨": [
        "PELE LISINHA E COM VIÇO, AMIGA ✨", "SKINCARE BOM E BARATO EXISTE ✨", "CUIDADO DE PELE NO PRECINHO ✨",
        "SUA PELE VAI AGRADECER ✨", "AMIGAS NÃO GUARDAM SEGREDO! 🤫"],  # "segredo de amiga" = dica de cuidado (dona)
    "PELE DE FILTRO NA VIDA REAL 💄": [
        "PELE DE FILTRO NA VIDA REAL 💄", "PELE UNIFORME EM MINUTOS 💄", "ACABAMENTO DE MAKE DE SALÃO 💄"],
    "OLHAR PODEROSO NO PRECINHO 👁️": [
        "OLHAR PODEROSO NO PRECINHO 👁️", "MAKE DE OLHO QUE CHAMA ATENÇÃO 👁️", "DO DIA A DIA À BALADA 👁️"],
    "BOCA LINDA GASTANDO POUCO 💋": [
        "BOCA LINDA GASTANDO POUCO 💋", "O BATOM CERTO MUDA O DIA 💋", "BOCA PERFEITA EM SEGUNDOS 💋"],
    "CABELO LINDO E SECO RAPIDINHO 💨": [
        "CABELO LINDO E SECO RAPIDINHO 💨", "SECA EM MINUTOS, SEM SOFRER 💨", "ESCOVA DE SALÃO EM CASA 💨"],
    "LISO PERFEITO EM MINUTOS ✨": [
        "LISO PERFEITO EM MINUTOS ✨", "LISO DE SALÃO SEM SAIR DE CASA ✨", "CHAPINHA BOA MUDA TUDO ✨"],
    "CACHOS DE SALÃO EM CASA 🌀": ["CACHOS DE SALÃO EM CASA 🌀", "CACHO DEFINIDO SEM ESFORÇO 🌀"],
    "CABELO MACIO DE SALÃO EM CASA 💆‍♀️": [
        "CABELO MACIO DE SALÃO EM CASA 💆‍♀️", "O CABELO AGRADECE 💆‍♀️", "HIDRATAÇÃO DE SALÃO EM CASA 💆‍♀️",
        "CRONOGRAMA CAPILAR SEM GASTAR MUITO 💆‍♀️"],
    "CHEIRO DE GRIFE, PREÇO DE ÁRABE 🔥": [
        "CHEIRO DE GRIFE, PREÇO DE ÁRABE 🔥", "PERFUME ÁRABE QUE FIXA DE VERDADE 🔥", "CHEIRO DE RICA GASTANDO POUCO 🔥",
        "O ÁRABE QUE TODO MUNDO PERGUNTA O NOME 🔥"],
    "CHEIROSA O DIA INTEIRO, AMIGA 🌸": [
        "CHEIROSA O DIA INTEIRO, AMIGA 🌸", "PERFUME BOM NO PRECINHO 🌸", "AQUELE CHEIRINHO QUE RENDE ELOGIO 🌸"],
    "LOOK DE TREINO QUE VALORIZA TUDO 🍑": [
        "LOOK DE TREINO QUE VALORIZA TUDO 🍑", "TREINO COM ESTILO 💪", "PRA IR LINDA PRA ACADEMIA 💪"],
    "SUPLEMENTO BOM COM PREÇO DE AMIGA 💪": [
        "SUPLEMENTO BOM COM PREÇO DE AMIGA 💪", "PRA QUEM LEVA O TREINO A SÉRIO 💪", "SUA DIETA AGRADECE 💪"],
    "ACADEMIA EM CASA NO PRECINHO 🚴‍♀️": [
        "ACADEMIA EM CASA NO PRECINHO 🚴‍♀️", "TREINO SEM SAIR DE CASA 🚴‍♀️", "CHUVA NÃO É MAIS DESCULPA 🚴‍♀️"],
    "CAMA DE HOTEL NA SUA CASA 🛏️": [
        "CAMA DE HOTEL NA SUA CASA 🛏️", "SONO GOSTOSO GASTANDO POUCO 🛏️", "QUARTO NOVO SEM REFORMA 🛏️"],
    "ORGANIZE SUA BAGUNÇA 🧺": ["ORGANIZE SUA BAGUNÇA 🧺", "TUDO NO LUGAR, FINALMENTE 🧺", "CASA ARRUMADA É OUTRA VIDA 🧺"],
    "TOALHA FOFINHA DE HOTEL 🛁": ["TOALHA FOFINHA DE HOTEL 🛁", "BANHO GOSTOSO COM TOALHA MACIA 🛁"],
    "COZINHA LINDA GASTANDO POUCO 🍳": [
        "COZINHA LINDA GASTANDO POUCO 🍳", "A COZINHA AGRADECE 🍳", "COZINHAR FICA MAIS GOSTOSO 🍳"],
    "MAMÃE, CORRE QUE TÁ BARATO 👶": ["MAMÃE, CORRE QUE TÁ BARATO 👶", "ACHADINHO PRA MAMÃE 👶", "O BEBÊ AGRADECE 👶"],
    "PRESENTE PROS PEQUENOS 🧸": ["PRESENTE PROS PEQUENOS 🧸", "DIVERSÃO GARANTIDA 🧸", "A CRIANÇADA VAI AMAR 🧸"],
    "ROUPINHA FOFA PROS PEQUENOS 🧸": ["ROUPINHA FOFA PROS PEQUENOS 🧸", "LOOK FOFO PROS PEQUENOS 🧸"],
    "A BOLSA QUE COMBINA COM TUDO 👜": [
        "A BOLSA QUE COMBINA COM TUDO 👜", "BOLSA NOVA, LOOK NOVO 👜", "CABE TUDO QUE VOCÊ PRECISA 👜"],
    "PÉ LINDO E CONFORTÁVEL 👟": ["PÉ LINDO E CONFORTÁVEL 👟", "CONFORTO DO COMEÇO AO FIM DO DIA 👟"],
    "SALTO LINDO PRO LOOK 👠": ["SALTO LINDO PRO LOOK 👠", "ELEGÂNCIA NO PRECINHO 👠", "PRONTA PRA ARRASAR NO SALTO 👠"],
    "JOIA DE MARCA COM DESCONTO 💎": ["JOIA DE MARCA COM DESCONTO 💎", "PRESENTE QUE ENCANTA 💎"],
    "BRILHO NO LOOK SEM GASTAR MUITO ✨": ["BRILHO NO LOOK SEM GASTAR MUITO ✨", "O DETALHE QUE MUDA O LOOK ✨"],
    "LOOK NOVO GASTANDO POUCO 👗": [
        "LOOK NOVO GASTANDO POUCO 👗", "ROUPA NOVA SEM PESAR NO BOLSO 👗", "PRA SAIR LINDA POR POUCO 👗"],
    "PRONTA PRO VERÃO 👙": ["PRONTA PRO VERÃO 👙", "PRAIA OU PISCINA, VOCÊ ESCOLHE 👙"],
    "PROTEÇÃO COM ESTILO 😎": ["PROTEÇÃO COM ESTILO 😎", "ÓCULOS NOVO NO PRECINHO 😎"],
    "O PET AGRADECE E O BOLSO TAMBÉM 🐾": ["O PET AGRADECE E O BOLSO TAMBÉM 🐾", "MIMO PRO SEU PET 🐾"],
    "TECNOLOGIA NO PRECINHO 📱": ["TECNOLOGIA NO PRECINHO 📱", "GADGET ÚTIL NO PRECINHO 📱"],
    "GELADINHO OU QUENTINHO O DIA TODO 🧊": ["GELADINHO OU QUENTINHO O DIA TODO 🧊", "HIDRATAÇÃO COM ESTILO 🧊"],
    "ALÍVIO PRO CORPO CANSADO 💆‍♀️": ["ALÍVIO PRO CORPO CANSADO 💆‍♀️", "MASSAGEM SEM SAIR DE CASA 💆‍♀️"],
    "DEPILAÇÃO EM CASA, SEM SOFRER ✨": ["DEPILAÇÃO EM CASA, SEM SOFRER ✨", "PELE LISINHA SEM IR AO SALÃO ✨"],
    "PRESENTE CERTO PRA ELE 🎁": ["PRESENTE CERTO PRA ELE 🎁", "ELE VAI AMAR 🎁", "ACHADINHO PRO MOZÃO 🎁"],
    "PELE MACIA O DIA INTEIRO 🧴": ["PELE MACIA O DIA INTEIRO 🧴", "HIDRATAÇÃO PRO CORPO TODO 🧴",
                                    "PÓS-BANHO GOSTOSO DEMAIS 🧴"],
}

# (regex no título, frases) — a característica específica vence a frase genérica do tipo
ATRIBUTOS: dict[str, list[tuple[str, list[str]]]] = {
    "PELE PROTEGIDA SEM PESAR NO BOLSO ☀️": [
        (r"com cor|\bcor\b|tonalizante", ["PROTEGE E JÁ DISFARÇA ☀️"]),
        (r"toque seco|antioleosidade|oil control|oleosidade", ["PROTEGE SEM DEIXAR A PELE OLEOSA ☀️"]),
        (r"bast[ãa]o|stick", ["PROTETOR EM BASTÃO, CABE NA BOLSA ☀️"]),
        (r"kids|infantil|beb[êe]", ["PROTEÇÃO PROS PEQUENOS NO SOL ☀️"]),
        (r"corporal", ["CORPO PROTEGIDO NO VERÃO ☀️"]),
        (r"fps ?(?:70|80|90|99)|fps(?:70|80|90)", ["PROTEÇÃO ALTA PRO DIA TODO ☀️"]),
        (KB, ["PROTETOR COREANO NO PRECINHO ☀️"])],
    "CÍLIOS DE BONECA POR ESSE PREÇO? 👀": [
        (r"prova d.?[áa]gua|waterproof", ["NÃO BORRA NEM NO CALOR 👀"]),
        (r"alonga", ["CÍLIOS LONGUÍSSIMOS 👀"]), (r"volume", ["VOLUME NOS CÍLIOS NUMA PASSADA 👀"])],
    "PELE LISINHA E COM VIÇO, AMIGA ✨": [
        (r"vitamina c", ["VITAMINA C PRA PELE COM BRILHO SAUDÁVEL ✨"]),
        (r"retin[oa]l", ["RETINOL PRA PELE COM CARA DE DESCANSADA ✨"]),
        (r"niacinamida", ["NIACINAMIDA, A QUERIDINHA DO SKINCARE ✨"]),
        (r"hialur", ["HIDRATAÇÃO QUE A PELE SENTE ✨"]),
        (r"limpeza|sabonete|espuma|micelar", ["PELE LIMPINHA SEM REPUXAR ✨"]),
        (r"noturn|creme (?:de )?noite|\bnight\b", ["CUIDA DA PELE ENQUANTO VOCÊ DORME 🌙"]),  # não "Dia/Noite"
        (r"olheira|[áa]rea dos olhos", ["OLHAR COM CARA DE DESCANSADO 👀"]),
        (r"manchas|clareador|uniform", ["PELE MAIS UNIFORME NO DIA A DIA ✨"]),
        (r"antissinais|anti-?idade|rugas|linhas", ["CUIDADO ANTISSINAIS NO PRECINHO ✨"]),
        (r"snail|mucin|caracol", ["A FAMOSA MUCINA DE CARACOL 🐌"]),
        (r"centella|\bcica\b|cicapair", ["CENTELLA PRA ACALMAR A PELE 🌿"]),
        (KB, ["SKINCARE COREANO NO PRECINHO 🇰🇷", "PELE DE VIDRO DAS COREANAS ✨"])],
    "OLHAR PODEROSO NO PRECINHO 👁️": [
        (r"paleta", ["PALETA PRA MIL MAKES DIFERENTES 👁️"]), (r"delineador", ["DELINEADO GATINHO SEM TREMER 👁️"]),
        (r"prova d.?[áa]gua|waterproof", ["NÃO BORRA NEM NO CALOR 👁️"])],
    "PELE DE FILTRO NA VIDA REAL 💄": [
        (r"blush", ["BOCHECHA CORADINHA SEM ESFORÇO 🌷"]), (r"iluminador", ["BRILHO DE PELE SAUDÁVEL ✨"]),
        (r"contorno|bronzer", ["ROSTO MAIS DESENHADO NA MAKE 💄"]),
        (r"matte", ["SEM BRILHO NA TESTA, AMÉM 💄"]), (r"glow|iluminad", ["PELE GLOW O DIA TODO ✨"]),
        (r"corretivo", ["DISFARÇA A OLHEIRA NA HORA 💄"]), (r"p[óo] (?:compacto|solto|transl)", ["SELA A MAKE E TIRA O BRILHO 💄"]),
        (r"primer", ["A MAKE DURA MUITO MAIS 💄"]), (r"bb cream|cc cream", ["COR E CUIDADO NUM PASSO SÓ 💄"]),
        (r"tirtir", ["A BASE COREANA QUE VIRALIZOU 💄"]), (r"cushion", ["PELE PRONTA EM SEGUNDOS COM CUSHION 💄"])],
    "BOCA LINDA GASTANDO POUCO 💋": [
        (r"muda de cor|m[áa]gico", ["O BATOM QUE MUDA DE COR 💋"]),
        (r"lip ?oil|[óo]leo labial", ["BRILHO E CUIDADO JUNTOS 💋"]),
        (r"gloss", ["BRILHINHO QUE TODO MUNDO REPARA 💋"]),
        (r"matte", ["MATTE LINDO PRO DIA A DIA 💋"]),
        (r"hidratante labial|balm|hidrata", ["BOCA MACIA E HIDRATADA 💋"]),
        (r"chocolate|\bmel\b|honey|morango|frutas|cereja|melancia", ["CHEIRINHO GOSTOSO NA BOCA 💋"]),
        (r"tint", ["COR NATURAL QUE DURA 💋"]),
        (r"rom&?nd|peripera", ["O TINT COREANO QUE TODO MUNDO QUER 💋"]),
        (r"lip sleeping|lip mask|m[áa]scara labial", ["BOCA MACIA ENQUANTO VOCÊ DORME 💋"])],
    "CABELO LINDO E SECO RAPIDINHO 💨": [
        (r"escova secadora|rotativa|alisadora", ["SECA E MODELA AO MESMO TEMPO 💨"]),
        (r"\b[íi]on|i[ôo]nic|tourmaline|turmalina", ["MENOS FRIZZ, MAIS BRILHO 💨"]),
        (r"(?:1[89]|2[0-4])00 ?w|bldc|alta velocidade", ["POTÊNCIA DE SECADOR DE SALÃO 💨"]),
        (r"travel|viagem|compacto", ["CABE NA MALA E SECA RAPIDINHO ✈️"]),
        (r"[34] em 1", ["SECA, ALISA E DÁ VOLUME 💨"])],
    "LISO PERFEITO EM MINUTOS ✨": [
        (r"tit[âa]ni", ["PLACAS DE TITÂNIO, LISO DE SALÃO ✨"]),
        (r"cer[âa]mic|nano", ["PLACAS DE CERÂMICA, CUIDADO COM O FIO ✨"]),
        (r"2 em 1|modela", ["ALISA E MODELA NUM APARELHO SÓ ✨"])],
    "CACHOS DE SALÃO EM CASA 🌀": [
        (r"sem calor|touca", ["CACHOS SEM CALOR, FIO PRESERVADO 🌀"]),
        (r"autom[áa]tico|girat", ["O MODELADOR FAZ O CACHO SOZINHO 🌀"])],
    "CABELO MACIO DE SALÃO EM CASA 💆‍♀️": [
        (r"\b[óo]leos?\b|\boils?\b|elixir", ["UMAS GOTINHAS E O FRIZZ VAI EMBORA 💆‍♀️"]),
        (r"leave-?in", ["PENTEIA FÁCIL E SEM FRIZZ 💆‍♀️"]),
        (r"antiqueda|queda", ["CUIDADO EXTRA PRO FIO FRÁGIL 💆‍♀️"]),
        (r"cach", ["CACHOS DEFINIDOS E MACIOS 🌀"]),
        (r"liso|plastia|queratina|keratin", ["LISO ALINHADO E SEM FRIZZ 💆‍♀️"]),
        (r"m[áa]scara", ["HIDRATAÇÃO PROFUNDA NO PRECINHO 💆‍♀️"]),
        (r"\b1 ?l\b|1 litro|1l\b|litro", ["TAMANHO SALÃO, RENDE MESES 💆‍♀️"]),
        (r"k[ée]rastase", ["KÉRASTASE BEM MAIS EM CONTA 💆‍♀️"]),
        (r"wella|k[ée]rastase|truss|l.or[ée]al professionnel|cadiveu|braé|matrix|keune",
         ["MARCA DE SALÃO COM DESCONTO 💆‍♀️"])],
    "CHEIRO DE GRIFE, PREÇO DE ÁRABE 🔥": [
        (r"\byara\b", ["O FAMOSO YARA 🔥"]), (r"\basad\b", ["O FAMOSO ASAD 🔥"]),
        (r"club de nuit", ["O FAMOSO CLUB DE NUIT 🔥"]),
        (r"body splash|splash", ["SPLASH ÁRABE CHEIROSO DEMAIS 🔥"]),
        (r"doce|sugar|vanill?a|baunilha", ["DOCINHO QUE FICA NA ROUPA 🔥"])],
    "CHEIROSA O DIA INTEIRO, AMIGA 🌸": [
        (r"turma da m[ôo]nica|hello kitty|infantil|kids", ["COLÔNIA FOFA PROS PEQUENOS 🌸"]),
        (r"body splash|splash|spray corporal", ["SPLASH GOSTOSINHO PRO DIA A DIA 🌸"]),
        (r"col[ôo]nia|\bdeo\b", ["COLÔNIA LEVINHA PRO DIA A DIA 🌸"]),
        (r"carolina herrera|lanc[ôo]me|jean paul|paco rabanne|calvin klein|azzaro|dior|chanel|givenchy|ysl|prada",
         ["PERFUME DE GRIFE COM DESCONTO 🌸"]),
        (r"\bnatura\b|botic[áa]rio|eudora|jequiti", ["O CLÁSSICO QUE NUNCA FALHA 🌸"]),
        (r"eau de parfum|\bedp\b|intense|extrait", ["PERFUMÃO DE FIXAÇÃO BOA 🌸"])],
    "LOOK DE TREINO QUE VALORIZA TUDO 🍑": [
        (r"transpar|blackout", ["NÃO FICA TRANSPARENTE, UFA 🍑"]),
        (r"cintura alta", ["CINTURA ALTA QUE SEGURA TUDO 🍑"]),
        (r"\bsustenta(?:[çc][ãa]o)?\b", ["SUSTENTAÇÃO DE VERDADE NO TREINO 💪"]),
        (r"sem costura", ["SEM COSTURA, NÃO MARCA NADA 🍑"]),
        (r"plus size", ["PLUS SIZE LINDO NO TREINO 💪"]),
        (r"conjunto", ["CONJUNTINHO FITNESS COMBINANDINHO 💪"])],
    "SUPLEMENTO BOM COM PREÇO DE AMIGA 💪": [
        (r"whey|prote[íi]na|protein", ["PROTEÍNA PRO PÓS-TREINO 💪"]),
        (r"creatina", ["CREATINA PRA TREINAR COM MAIS GÁS 💪"]),
        (r"col[áa]geno", ["COLÁGENO NA ROTINA, SEM ESQUECER 💪"]),
        (r"barra", ["LANCHINHO PROTEICO PRA BOLSA 💪"]),
        (r"pr[ée]-?treino", ["ENERGIA PRO TREINO 💪"])],
    "ACADEMIA EM CASA NO PRECINHO 🚴‍♀️": [
        (r"esteira", ["CAMINHADA NA SALA DE CASA 🏃‍♀️"]), (r"dobr[áa]vel", ["DOBRA E GUARDA EM QUALQUER CANTO 🚴‍♀️"]),
        (r"spinning", ["AULA DE SPINNING NA SUA CASA 🚴‍♀️"])],
    "CAMA DE HOTEL NA SUA CASA 🛏️": [
        (r"(?:300|400|500|600|800|1000) fios", ["{fios} FIOS: MACIO DE VERDADE 🛏️"]),
        (r"edredom|coberdrom|cobertor|manta|sherpa", ["QUENTINHO PRAS NOITES FRIAS 🛏️"]),
        (r"cervical|ortop|ronco", ["O PESCOÇO AGRADECE DE MANHÃ 🛏️"]),
        (r"travesseiro", ["TRAVESSEIRO BOM MUDA O SONO 🛏️"])],
    "ORGANIZE SUA BAGUNÇA 🧺": [
        (r"maquiagem|cosm[ée]tic|batom", ["MAQUIAGEM ARRUMADA E À VISTA 💄"]),
        (r"girat[óo]rio", ["GIRA E VOCÊ ACHA TUDO NA HORA 🧺"]),
        (r"roupa|edredom|cobertor|\bsacos? (?:a |de )?v[áa]cuo", ["GUARDA-ROUPA ORGANIZADO 🧺"]),
        (r"cozinha|\bpotes?\b|despensa|arm[áa]rio", ["DESPENSA ORGANIZADA DÁ GOSTO 🧺"]),
        (r"acr[íi]lico|transparente", ["TRANSPARENTE: VOCÊ VÊ TUDO 🧺"])],
    "TOALHA FOFINHA DE HOTEL 🛁": [
        (r"\bjogo\b|\bkit\b|pe[çc]as", ["JOGO DE TOALHAS NOVINHO 🛁"]), (r"gigante|toalh[ãa]o|banh[ãa]o", ["TOALHÃO QUE ABRAÇA 🛁"])],
    "COZINHA LINDA GASTANDO POUCO 🍳": [
        (r"oven|forno", ["AIR FRYER QUE VIRA FORNINHO 🍳"]),
        (r"air ?fryer|fritadeira", ["FRITURA SEM ÓLEO E SEM CULPA 🍳"]),
        (r"\bpress[ãa]o\b", ["FEIJÃO PRONTO RAPIDINHO 🍳"]),
        (r"pipoca", ["PIPOCA DE CINEMA EM CASA 🍿"]),
        (r"potes? herm", ["COMIDA FRESQUINHA POR MAIS TEMPO 🍳"]),
        (r"mixer|processador|liquidificador", ["PICA, BATE E AGILIZA A COZINHA 🍳"]),
        (r"antiaderente|cer[âa]mic", ["NADA GRUDA NA PANELA 🍳"]),
        (r"indu[çc][ãa]o", ["SERVE ATÉ NO FOGÃO DE INDUÇÃO 🍳"]),
        (r"jogo de panelas|conjunto de panelas|panelas", ["JOGO DE PANELAS NOVINHO 🍳"])],
    "MAMÃE, CORRE QUE TÁ BARATO 👶": [
        (r"fralda", ["FRALDA É SEMPRE BOM ESTOCAR 👶"]), (r"\blen[çc]os?\b", ["LENCINHO NUNCA É DEMAIS 👶"]),
        (r"shampoo|sabonete|banho", ["BANHO GOSTOSO PRO BEBÊ 👶"]), (r"canguru", ["BEBÊ PERTINHO E MÃOS LIVRES 👶"]),
        (r"copo|prato|alimenta|papinha", ["HORA DA PAPINHA SEM SUJEIRA 👶"]),
        (r"mordedor|chocalho|atividades|m[óo]bile|tapete", ["DIVERSÃO PRO BEBÊ 👶"]),
        (r"bab[áa] eletr|monitor", ["OLHO NO BEBÊ DE QUALQUER CANTO 👶"])],
    "PRESENTE PROS PEQUENOS 🧸": [
        (r"educativo|montessori", ["BRINCAR E APRENDER JUNTOS 🧸"]), (r"boneca|reborn", ["A BONECA MAIS FOFA 🧸"]),
        (r"patinete|triciclo|bicicleta", ["DIVERSÃO AO AR LIVRE 🛴"]), (r"controle remoto", ["CONTROLE REMOTO É SUCESSO 🧸"]),
        (r"blocos|lego|montar", ["MONTAR E INVENTAR 🧸"]), (r"pel[úu]cia", ["PELÚCIA FOFINHA PRA ABRAÇAR 🧸"])],
    "ROUPINHA FOFA PROS PEQUENOS 🧸": [(r"fantasia", ["FANTASIA PRA FESTA 🎉"]), (r"pijama", ["PIJAMINHA GOSTOSO 🧸"])],
    "A BOLSA QUE COMBINA COM TUDO 👜": [
        (r"transversal|tiracolo", ["MÃOS LIVRES E ESTILO 👜"]), (r"viagem|mala", ["PRONTA PRA VIAGEM ✈️"]),
        (r"necessaire", ["NECESSAIRE PRA ARRUMAR A MAKE 👜"]), (r"imperme[áa]vel", ["NEM A CHUVA ATRAPALHA 👜"]),
        (r"\btote\b", ["TOTE BAG QUE CABE A VIDA 👜"])],
    "PÉ LINDO E CONFORTÁVEL 👟": [
        (r"sand[áa]lia|rasteira|papete|slide", ["SANDÁLIA LINDA PRO CALOR 👡"]),
        (r"salto|scarpin|tamanco", ["SALTO LINDO E CONFORTÁVEL 👠"]),
        (r"pantufa", ["PÉ QUENTINHO EM CASA 🧦"]), (r"chinelo", ["CHINELO GOSTOSO DE USAR 🩴"]),
        (r"corrida|running|caminhada", ["PRA CAMINHAR COM CONFORTO 👟"]),
        (r"academia|treino", ["PRONTO PRO TREINO 👟"]),
        # 03/10: "Espuma" casava com "puma" e um MOCASSIM saiu como "TÊNIS DE MARCA" → palavra inteira + sapato antes
        (r"mocassim|loafer|oxford|sapatilha|\bsapato", ["SAPATO ELEGANTE PRO DIA A DIA 👞"]),
        (r"\b(?:nike|adidas|puma|asics|mizuno|new balance|fila|olympikus|kappa)\b", ["TÊNIS DE MARCA COM DESCONTO 👟"]),
        (r"t[êe]nis", ["TÊNIS NOVO NO PRECINHO 👟"])],
    "BRILHO NO LOOK SEM GASTAR MUITO ✨": [
        (r"alian[çc]a|namoro|compromisso", ["PRA SELAR O AMOR 💍"]),
        (r"ponto de luz", ["PONTO DE LUZ, DELICADO E LINDO ✨"]),
        (r"prata 925|prata de lei|prata925", ["PRATA 925 DE VERDADE ✨"]),
        (r"banhad|folhead|ouro 18k", ["BANHADO A OURO, BRILHO DE JOIA ✨"]),
        (r"antial[ée]rgic", ["ANTIALÉRGICO, PODE USAR SEM MEDO ✨"]),
        (r"rel[óo]gio", ["RELÓGIO LINDO NO PULSO ⌚"]),
        (r"presente|cora[çc][ãa]o", ["PRESENTE QUE ENCANTA 💝"]),
        (r"\bkit\b|\bconjunto\b|\btrio\b", ["KIT PRA COMBINAR COM TUDO ✨"])],
    "LOOK NOVO GASTANDO POUCO 👗": [
        (r"festa|casamento|formatura|madrinha", ["PRONTA PRA FESTA 🥂"]),
        (r"plus size", ["PLUS SIZE LINDO E CONFORTÁVEL 👗"]),
        (r"pijama", ["PIJAMA GOSTOSO PRA DORMIR BEM 😴"]),
        (r"calcinha|suti[ãa]|lingerie", ["CONFORTO QUE SÓ VOCÊ SENTE 🩲"]),
        (r"jeans", ["JEANS QUE VESTE BEM 👖"]),
        (r"moletom|casaco|jaqueta|frio|t[ée]rmic", ["QUENTINHO E ESTILOSO 🧥"]),
        (r"alfaiataria|social|elegante|blazer", ["ELEGANTE SEM GASTAR MUITO 👗"]),
        (r"evang[ée]lic", ["MODA MODESTA E LINDA 👗"]),
        (r"b[áa]sic|ribana|canelad", ["A BÁSICA QUE COMBINA COM TUDO 👚"]),
        (r"soltinh|ver[ãa]o|regata", ["FRESQUINHO PRO CALOR 👗"]),
        (r"vestido", ["VESTIDO LINDO PRA QUALQUER OCASIÃO 👗"])],
    "PRONTA PRO VERÃO 👙": [
        (r"\bmai[ôo]s?\b", ["MAIÔ LINDO PRO VERÃO 👙"]), (r"cortininha", ["CORTININHA NUNCA SAI DE MODA 👙"]),
        (r"sa[íi]da de praia", ["SAÍDA DE PRAIA CHIQUE 👙"])],
    "PROTEÇÃO COM ESTILO 😎": [
        (r"uv ?400|\buv\b", ["PROTEÇÃO UV400 PROS OLHOS 😎"]),
        (r"leitura|grau|perto", ["LEITURA SEM FORÇAR A VISTA 👓"]),
        (r"ciclismo|corrida|esport", ["PRO ESPORTE AO AR LIVRE 😎"])],
    "O PET AGRADECE E O BOLSO TAMBÉM 🐾": [
        (r"areia", ["AREIA DO GATINHO NO PRECINHO 🐱"]), (r"gato", ["PROS GATINHOS 🐱"]),
        (r"c[ãa]es|cachorro|dog", ["PROS CÃOZINHOS 🐶"]), (r"ra[çc][ãa]o", ["RAÇÃO BOA NO PRECINHO 🐾"])],
    "TECNOLOGIA NO PRECINHO 📱": [
        (r"\bfones?\b", ["MÚSICA SEM FIO O DIA TODO 🎧"]), (r"caixa de som|caixinha", ["SOM ALTO PRA QUALQUER ROLÊ 🔊"]),
        (r"gps|sa[úu]de|monitor|batimento", ["ACOMPANHA SEU TREINO NO PULSO ⌚"]),
        (r"smart ?watch|inteligente", ["NOTIFICAÇÃO NO PULSO, CELULAR NA BOLSA ⌚"])],
    "GELADINHO OU QUENTINHO O DIA TODO 🧊": [
        (r"caf[ée]|\bch[áa]\b", ["CAFÉ QUENTINHO POR HORAS ☕"]), (r"stanley", ["O FAMOSO STANLEY 🧊"]),
        (r"gigante|1,2 ?l|1200|1\.2 ?l", ["COPÃO GIGANTE PRO DIA TODO 🧊"]),
        (r"academia|esporte|squeeze", ["ÁGUA GELADA NO TREINO 🧊"])],
    "ALÍVIO PRO CORPO CANSADO 💆‍♀️": [
        (r"pistola|muscular", ["ALÍVIO PÓS-TREINO 💆‍♀️"]), (r"facial|rosto|pesco", ["MASSAGEM FACIAL EM CASA ✨"])],
    "DEPILAÇÃO EM CASA, SEM SOFRER ✨": [
        (r"sobrancelha|bu[çc]o|facial|rosto", ["ACABAMENTO NO ROSTO EM SEGUNDOS ✨"]),
        (r"\bcera\b|termocera", ["CERA NO PONTO CERTO, EM CASA ✨"]),
        (r"recarreg|usb", ["RECARREGA E LEVA PRA ONDE QUISER ✨"])],
    "PELE MACIA O DIA INTEIRO 🧴": [
        (r"sem perfume|neutr|sens[íi]vel", ["HIDRATA ATÉ PELE SENSÍVEL 🧴"]),
        (r"baunilha|vanilla|frutas|morango|flor|rosa|lavanda|cheir", ["PELE MACIA E CHEIROSA 🌸"]),
        (r"\b[óo]leos?\b|bio oil", ["ÓLEO QUE DEIXA A PELE SEDOSA 🧴"])],
    "PRESENTE CERTO PRA ELE 🎁": [
        (r"perfume|col[ôo]nia|eau de|body splash", ["PERFUME PRA ELE COM DESCONTO 🎁"]),
        (r"rel[óo]gio", ["RELÓGIO PRA ELE NO PRECINHO ⌚"]), (r"t[êe]nis", ["TÊNIS PRA ELE COM DESCONTO 👟"])],
}

SAZONAIS = [  # (tipo, frase, início (mês, dia), fim (mês, dia))
    ("PRESENTE PROS PEQUENOS 🧸", "DIA DAS CRIANÇAS TÁ CHEGANDO 🧸", (9, 25), (10, 12)),
    ("ROUPINHA FOFA PROS PEQUENOS 🧸", "PRESENTE PRO DIA DAS CRIANÇAS 🧸", (9, 25), (10, 12)),
    ("PRONTA PRO VERÃO 👙", "O VERÃO TÁ CHEGANDO 👙", (10, 1), (12, 21)),
]

UNIVERSAL = ["OLHA ESSE PRECINHO 😍", "ACHADO DO DIA 🔎", "VALE CADA CENTAVO 💸", "ACHEI E VIM CONTAR 🏃‍♀️",
             "ESSE EU LEVARIA 😍"]  # 06/10 (Beto): "DESCONTO QUE VALE A PENA" saía sem preço De → só no OFF
KIT = ["SÓ {p} CADA 😱", "SAI {p} CADA 😱", "{p} CADA, ACREDITA? 😱"]
OFF = ["{d}% OFF, NÃO É ERRO! 😱", "{d}% OFF DE VERDADE 😱", "CAIU {d}%, OLHA ISSO 😱", "DESCONTO QUE VALE A PENA 💸"]

# 01/10 (dona): "um agente que todo dia vai atualizar o repertório de legendas, sempre criando novos, pra não ficar
# repetitivo" → a Tati (agente) escreve frases NOVAS em dados/achadinhos/ganchos_extra.json {tipo: [frases]}; aqui só
# entram as que passam na checagem (sem promessa milagrosa, sem link/grupo/@, maiúsculas, curta, com emoji)
# 06/10 (Rita, 3 casos no dia: Kérastase R$ 206 "SEM GASTAR MUITO", Lattafa R$ 135 "CHEIRO DE RICA GASTANDO POUCO",
# máscara R$ 270): frase de ECONOMIA só em produto de até R$ 120 — acima disso vai outra frase do tipo (ou neutra)
PRECO_ECONOMIA = 120
ECONOMIA = re.compile(r"(?i)BARAT|PRECINHO|CENTAVO|BOLSO|GASTAR MUITO|GASTANDO POUCO|POR POUCO|ECONOM|"
                      r"PRE[ÇC]O DE (?:ACHADINHO|AMIGA)|PRE[ÇC]O BAIXO|PAGANDO POUCO")


def economia_ok(frase: str, preco: float | None) -> bool:
    """False quando a frase fala de economia e o produto passa de R$ 120."""
    return not (preco and preco > PRECO_ECONOMIA and ECONOMIA.search(frase or ""))


PROIBIDAS = re.compile(r"(?i)\b(?:cura|curar|elimina\w*|acaba com|garantid\w*|milagr\w*|link|grupo|clique|whatsapp|"
                       r"http|www|definitiv\w*|100%)|@")


def frase_ok(f: str, tipo: str = "") -> bool:
    letras = [c for c in f if c.isalpha()]
    emoji = any(ord(c) > 0x2100 and not c.isalpha() for c in f)
    if not (6 <= len(f) <= 42 and letras and all(c.isupper() for c in letras) and emoji) or PROIBIDAS.search(f):
        return False
    return not (tipo.endswith("PRA ELE 🎁") and re.search(r"AMIGA|LINDA|CHEIROSA", f))  # frase de homem sem "amiga"


# 06/10: sapato ANTES de batom e "lip" só como palavra inteira — "Tênis … Slip On" virou gancho de batom
_DUPE_TIPOS = (("sapato", r"sapat|t[êe]nis|sand[áa]lia|mule|tamanco|rasteir|scarpin|slip ?on"),
               ("batom", r"batom|gloss|\blip\b|lip ?(?:oil|tint|balm|gloss)"), ("blush", r"blush"),
               ("oculos", r"[óo]culos"), ("bolsa", r"bolsa|clutch|necessaire"), ("joia", r"brinco|colar|anel|pulseira|joia|porta.?joias"),
               ("perfume", r"perfume|body splash|col[ôo]nia"))


def dupe(titulo: str) -> list[str]:
    """06/10 (dona: dupe forte, réplica não): ganchos "parece de grife" da Tati (dados/achadinhos/ganchos_dupe.json),
    só para achados da Eva (vitrine fiel) — peça parecida com a foto da divulgação, de outra marca. Frases com {marca}
    ficam de fora (o achado não tem marca de referência)."""
    try:
        import json

        from vendas import config
        d = json.loads((config.DADOS / "achadinhos" / "ganchos_dupe.json").read_text(encoding="utf-8-sig"))
    except Exception:  # noqa: BLE001
        return []
    out = [f for f in d.get("GERAL", []) if isinstance(f, str)]
    for chave, padrao in _DUPE_TIPOS:
        if re.search(padrao, titulo or "", re.I):
            out = [f for f in d.get(chave, []) if isinstance(f, str)] + out
            break
    # frase que cita um TIPO de sapato/acessório só vale se o título for desse tipo ("SALTO…" numa sapatilha, não)
    tipos = {"SAPATILHA": r"sapatilh|bailarin", "SALTO": r"salto|scarpin", "TÊNIS": r"t[êe]nis", "RASTEIRA": r"rasteir",
             "SANDÁLIA": r"sand[áa]lia", "MULE": r"mule|tamanco", "BRINCO": r"brinco", "COLAR": r"colar",
             "ANEL": r"anel", "PULSEIRA": r"pulseira"}
    return [f for f in out if "{" not in f and frase_ok(f)
            and all(re.search(p, titulo or "", re.I) for t, p in tipos.items() if t in f)]


def _carregar_extra() -> int:
    try:
        import json

        from vendas import config
        dados = json.loads((config.DADOS / "achadinhos" / "ganchos_extra.json").read_text(encoding="utf-8-sig"))  # BOM do PowerShell
    except Exception:  # noqa: BLE001 — sem arquivo (ou no radar da nuvem): só o repertório fixo
        return 0
    n = 0
    for tipo, frases in (dados or {}).items():
        lista = UNIVERSAL if tipo == "UNIVERSAL" else REPERTORIO.get(tipo)
        if lista is None:
            continue
        for f in frases:
            if tipo == "UNIVERSAL" and isinstance(f, str) and re.search(r"DESCONTO|\bOFF\b|%", f):
                continue  # 06/10 (Beto): frase universal vai até em oferta sem preço De — desconto só no OFF
            if isinstance(f, str) and f not in lista and frase_ok(f, tipo):
                lista.append(f)
                n += 1
    return n


EXTRA_CARREGADAS = _carregar_extra()


def _sazonais(tipo: str, hoje: date | None) -> list[str]:
    hoje = hoje or date.today()
    return [f for t, f, ini, fim in SAZONAIS if t == tipo and ini <= (hoje.month, hoje.day) <= fim]


def candidatos(tipo: str, titulo: str, hoje: date | None = None, preco: float | None = None) -> list[str]:
    """Frases possíveis para esse produto: sazonal e características do título primeiro, depois as do tipo."""
    out = list(_sazonais(tipo, hoje))
    for rx, frases in ATRIBUTOS.get(tipo, []):
        m = re.search(rx, titulo, re.I)
        if m:
            fios = re.search(r"(\d+) fios", titulo, re.I)
            out += [f.format(fios=fios.group(1) if fios else "") for f in frases]
    rep = REPERTORIO.get(tipo, [tipo])
    # 01/10 (Tati): blush "Banila Co… Lip and Cheek" saiu "PELE UNIFORME EM MINUTOS" (frase de BASE) → sem base no
    # título, só a frase da característica (blush/iluminador/contorno) + a neutra de make
    if tipo == "PELE DE FILTRO NA VIDA REAL 💄" and out and not SO_BASE.search(titulo):
        rep = ["ACABAMENTO DE MAKE DE SALÃO 💄"]
    out += rep
    out = list(dict.fromkeys(out))  # sem duplicar, mantendo a ordem
    # 01/10 (Rita, revisão antes de postar): emoji/frase que não combina com o produto
    if re.search(r"diurn", titulo, re.I) and re.search(r"noturn|night", titulo, re.I):  # 05/10: kit dia + noite
        out = ["CUIDADO DE DIA E DE NOITE 🌙" if "DORME" in f else f for f in out]
    elif re.search(r"diurn|\bday\b", titulo, re.I):
        out = [f for f in out if "DORME" not in f] or out
    if re.search(r"gel de banho|gel douche|sabonete|body wash|shower", titulo, re.I) and tipo == "PELE LISINHA E COM VIÇO, AMIGA ✨":
        return ["BANHO GOSTOSO E PELE MACIA 🧴", "PELE MACIA DESDE O BANHO 🧴"]  # 05/10: banho não é skincare de rosto
    if re.search(r"sapatilha|mocassim|loafer|oxford", titulo, re.I):  # 05/10: "Sapatilha … Salto Baixo" saía "SALTO"/"SANDÁLIA"
        out = [f for f in out if not re.search(r"SALTO|SANDÁLIA", f)] or ["SAPATO ELEGANTE PRO DIA A DIA 👞"]
    if re.search(r"sand[áa]lia|rasteir|chinelo|tamanco|papete|birken", titulo, re.I):
        out = [f.replace("👟", "👡") for f in out]  # 👟 é tênis
    if re.search(r"labial|l[áa]bios?\b|\blip\b|\blips\b|boca", titulo, re.I):
        out = [f for f in out if not re.search(r"\bPELE\b", f)] or out  # reparador LABIAL ≠ "sua pele vai agradecer"
    if preco and preco > PRECO_ECONOMIA:  # R$ 207 não é "bom e barato" (06/10: nem "sem gastar muito"/"gastando pouco")
        out = [f for f in out if economia_ok(f, preco)]  # lista vazia → o robô vai de frase neutra
    if tipo == "CABELO MACIO DE SALÃO EM CASA 💆‍♀️":
        # 05/10 (dona, print do grupo): "Tintura Masculina" saiu "CRONOGRAMA CAPILAR SEM GASTAR MUITO" — cor não é
        # hidratação; e cronograma só para máscara/hidratação/nutrição/reconstrução
        if TINTURA.search(titulo):
            return (["COR DE BARBEARIA EM CASA 💈", "CABELO E BARBA EM DIA 💈"]
                    if re.search(r"mascul|\bmen\b|homem|barba", titulo, re.I) else
                    ["COR DE SALÃO EM CASA 🎨", "COR NOVA SEM SAIR DE CASA 🎨"])
        if not re.search(r"m[áa]scara|hidrata|nutri|reconstr|cronograma|kit", titulo, re.I):
            out = [f for f in out if "CRONOGRAMA" not in f] or out
        # 05/10: shampoo de oleosidade/volume e tônico de couro cabeludo não são "cabelo macio"
        if re.search(r"oleosidade|raiz oleosa|oleos[oa]s?\b|antioleos", titulo, re.I):
            return ["RAIZ LEVE E CABELO SOLTINHO 💆‍♀️", "ADEUS CABELO OLEOSO 💆‍♀️"]
        if re.search(r"volume|volumizing|encorpa", titulo, re.I):
            return ["VOLUME DE SALÃO EM CASA 💆‍♀️", "CABELO ENCORPADO NO PRECINHO 💆‍♀️"
                    if not (preco and preco > 120) else "CABELO ENCORPADO DE VERDADE 💆‍♀️"]
        if re.search(r"t[ôo]nico|scalp|couro cabeludo|queda|anticaspa|caspa|crescimento", titulo, re.I):
            return ["COURO CABELUDO EM DIA 💆‍♀️", "CUIDADO DA RAIZ ÀS PONTAS 💆‍♀️"]
    return out


TINTURA = re.compile(r"tintura|colora[çc][ãa]o|descolorante|tonalizante|\bhenna\b|p[óo] descolorante|"
                     r"\bcolor\s*n[º°o]?\s*\d|matizador|retoque de raiz", re.I)


SO_BASE = re.compile(r"\bbase\b|corretivo|concealer|cushion|p[óo] compacto|bb cream|cc cream|primer|fixador", re.I)


def escolher(opcoes: list[str], n: int, recentes: list[str] | None) -> str:
    """A 1ª opção que não saiu nos últimos posts; se todas saíram, a que saiu há mais tempo. Sem histórico: gira por n
    entre as opções específicas (característica) e a do tipo."""
    if not opcoes:
        return UNIVERSAL[n % len(UNIVERSAL)]
    if recentes:
        livres = [f for f in opcoes if f not in recentes]
        if livres:
            return livres[0]
        return min(opcoes, key=lambda f: max(i for i, r in enumerate(recentes) if r == f))
    return opcoes[0] if n % 4 != 3 else opcoes[(n // 4) % len(opcoes)]
