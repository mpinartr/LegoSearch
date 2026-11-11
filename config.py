"""
config.py — Configuración central de LegoSearch.

Estrategia "Lean":
    Se evita por completo el web scraping masivo de tiendas protegidas por
    Cloudflare. Todo el descubrimiento se hace leyendo fuentes RSS públicas
    (blogs, foros y agregadores). La aplicación NO notifica ni opera compras:
    exporta los datos en ``opportunities.json`` y un superagente externo
    (GitHub Actions / LLM / bot propio) decide qué hacer con ellos.

Este módulo solo contiene DATOS de configuración (diccionarios, listas y
constantes). No importa nada interno de la aplicación, para que se pueda
editar sin miedo a romper el código.

Edición recomendada:
    - Ajusta finanzas en ``FINANZAS``.
    - Ajusta calidad de señales en ``FILTROS``.
    - Añade/quita fuentes en ``FEEDS``.
    - Amplía ``WATCHLIST`` con tus sets objetivo. Puedes además sobrescribirla
      sin tocar este fichero usando ``watchlist.json`` (ver ``main.py``).
"""

from __future__ import annotations

import os

# ---------------------------------------------------------------------------
# 1. RUTAS DEL PROYECTO
# ---------------------------------------------------------------------------
# Rutas relativas al directorio del proyecto (no al CWD desde donde se ejecute).
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Fichero donde ``main.py`` escribe las oportunidades estructuradas.
OUTPUT_FILE = os.path.join(BASE_DIR, "opportunities.json")

# Watchlist opcional en JSON. Si existe, se FUSIONA con la de este fichero
# (las referencias del JSON tienen prioridad). Útil para personalizar sin
# ensuciar el repositorio.
WATCHLIST_JSON = os.path.join(BASE_DIR, "watchlist.json")

# ---------------------------------------------------------------------------
# 2. FINANZAS / MOTOR FINANCIERO
# ---------------------------------------------------------------------------
FINANZAS = {
    # Gasto de gestión de la plataforma de venta (Wallapop, eBay, BrickLink...).
    # 10 % es una estimación conservadora para particulares en marketplaces.
    "gastos_plataforma_pct": 10.0,

    # Envío estimado como porcentaje del precio de venta. MEJORA: cámbialo a un
    # valor fijo en euros (p. ej. 4.95) si vendes siempre con el mismo carrier.
    "gastos_envio_pct": 5.0,

    # Umbrales de calidad financiera.
    # Ajustados para "ver más chollos": el objetivo es detectar y priorizar,
    # no descartar. El score_total ordena y el usuario decide.
    "roi_minimo_pct": 25.0,             # antes 40 %: filtraba oportunidades válidas
    "beneficio_neto_minimo_eur": 10.0,  # antes 15 €: permite sets pequeños rentables

    # Tope de comisión total para evitar configuraciones inviables.
    "comision_total_max_pct": 60.0,
}

# ---------------------------------------------------------------------------
# 3. FILTRO DUAL — CRITERIOS DE COLECCIÓN / INVERSOR
# ---------------------------------------------------------------------------
FILTROS = {
    # Restringe el cruce a sets "retirados" (EoL) o "próximos a retirarse".
    # Los sets activos pueden seguir apareciendo en el histórico si los
    # retiras manualmente del estado aquí.
    "solo_estados_retirada": False,
    "estados_retirada": ["EoL", "Retirado", "Próximo EoL"],

    # --- Criterios de coleccionista -------------------------------------
    "piezas_alto_volumen": 1_000,   # piezas >= este valor => volumen alto
    "score_minimo_coleccionista": 1,  # antes 2: un único criterio ya es suficiente

    # --- Vigencia de la oferta ------------------------------------------
    # Si la noticia es más antigua que esto, el precio detectado probablemente
    # ya no esté vigente. Se mantiene fuera del JSON de "oportunidades" y se
    # reporta como descartada.
    "dias_max_antiguedad_oferta": 45,      # días para aceptar una oferta
    "dias_max_antiguedad_news": 365,       # días para señales de EoL/rumor

    # --- Descarte por oferta insostenible -------------------------------
    # Si el precio detectado es menos del X % del PVP oficial, lo más probable
    # es que el regex haya capturado la cuota de un sorteo, un cupón, una
    # pieza o un artículo satírico. Se descarta.
    "precio_minimo_pct_pvp": 10.0,

    # Umbrales de scoring final.
    # Bajos a propósito: el score sirve para ORDENAR la tabla, no para ocultar
    # resultados. Los umbrales financieros de FINANZAS siguen filtrando.
    "score_total_minimo": 30.0,       # antes 55: ocultaba chollos válidos
    "confianza_minima_cruce": 25,     # 0-100: confianza de que la noticia hable del set
}

# ---------------------------------------------------------------------------
# 4. FUENTES RSS "OFICIALES Y SEGURAS"
# ---------------------------------------------------------------------------
# ``tipo`` orienta la interpretación de la señal:
#   - noticias    : blogs y prensa especializada (lanzamientos, rumores)
#   - eol         : fuente que suele anunciar retiradas (EoL)
#   - ofertas     : agregadores/comunidades de chollos
# ``valor_eur`` es un multiplicador de fiabilidad (0-1) que pondera el score:
# una oferta de r/legodeals vale más que un descuento mencionado de pasada.
FEEDS = [
    {
        "nombre": "Brickset News",
        "url": "https://brickset.com/feed",
        "tipo": "noticias",
        "valor_eur": 0.8,
    },
    {
        "nombre": "Brick Fanatics",
        "url": "https://www.brickfanatics.com/feed/",
        "tipo": "eol",
        "valor_eur": 0.9,
    },
    {
        "nombre": "The Lego Car Blog",
        "url": "https://thelegocarblog.com/feed/",
        "tipo": "noticias",
        "valor_eur": 0.6,
    },
    {
        "nombre": "Reddit r/legodeals",
        "url": "https://www.reddit.com/r/legodeals/.rss",
        "tipo": "ofertas",
        "valor_eur": 1.0,
    },
    {
        "nombre": "Reddit r/lego",
        "url": "https://www.reddit.com/r/lego/.rss",
        "tipo": "noticias",
        "valor_eur": 0.75,
    },
    {
        # ⚠️ Endpoint de búsqueda por palabra clave (público y sin login).
        # La URL histórica "https://www.chollometro.com/rss/search?q=lego"
        # devuelve 404 desde 2025; Pepper la sustituyó por "rssx/keyword-alarm".
        # El keyword-alarm público exige un token de la web, por lo que usamos
        # el feed de novedades: comprobado que trae más LEGO que "rss/hot"
        # (que en las pruebas tenía 0 menciones) y precios estructurados en el
        # atributo pepper:merchant. Si tienes el token de tu alerta, cámbialo a:
        #   https://www.chollometro.com/rssx/keyword-alarm/lego
        "nombre": "Chollometro (LEGO)",
        "url": "https://www.chollometro.com/rss/nuevos",
        "tipo": "ofertas",
        "valor_eur": 1.0,
    },
]

# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# 4b. CALENDARIO DE RETIRADAS (EoL) — datos de Brickset
# ---------------------------------------------------------------------------
# Brickset publica `exitDate` por set: la fecha en que LEGO deja de fabricarlo.
# Es la señal clave para un revendedor: el valor de reventa sube justo después.
#
# ``temas``: temas de Brickset tal como los nombra la API (no los de la
#   watchlist). Se comprobaron contra la API real.
# ``años``: año de lanzamiento a consultar. Cada combinación tema+año es una
#   petición HTTP; mantener la lista corta para no eternizar el escaneo.
# ``meses_horizonte``: solo se reportan retiradas dentro de esta ventana.
CALENDARIO_EOL = {
    "activo": True,
    "temas": [
        "Star Wars",
        "Creator Expert",
        "Icons",
        "Ideas",
        "Technic",
        "Harry Potter",
        "Marvel Super Heroes",
        "Disney",
        "Super Mario",
        "Ninjago",
    ],
    "años": ["2023", "2024", "2025"],
    "meses_horizonte": 24,
}

# ---------------------------------------------------------------------------
# 5. WATCHLIST AVANZADA (BASE DE DATOS LOCAL)
# ---------------------------------------------------------------------------
# Metadatos que buscan inversores/criterios AFOL profesionales:
#
#   "referencia"              : set number LEGO (p. ej. "75192"). Clave única.
#   "nombre"                  : nombre comercial.
#   "tematica"                : nicho (Star Wars UCS, Modular Buildings, Ideas...).
#   "precio_oficial"          : PVP oficial en euros (referencia de descuento).
#   "precio_objetivo_compra"  : precio máximo al que comprarías.
#   "precio_estimado_reventa" : valor estimado de venta tras la retirada (EoL).
#   "estado_eol"              : "EoL" | "Retirado" | "Próximo EoL" | "Activo".
#   "piezas"                  : número de piezas (int).
#   "minifiguras_exclusivas"  : incluye minifiguras exclusivas / únicas (bool).
#   "publico_adulto_afol"     : enfocado a adultos (18+, UCS, Icons) (bool).
#
# Campos opcionales de calidad de datos:
#   "alias"                   : nombres alternativos para cruzar titulares.
#   "notas"                   : contexto editorial para humanos (no se usa en
#                               cálculos).
#
# NOTA: Los precios de reventa son estimaciones de planificación, no precios
# de mercado garantizados. Ajusta el resto según tu criterio.
# ---------------------------------------------------------------------------
WATCHLIST: dict[str, dict] = {
    "75192": {
        "nombre": "Millennium Falcon UCS",
        "tematica": "Star Wars UCS",
        "precio_oficial": 849.99,
        "precio_objetivo_compra": 595.00,
        "precio_estimado_reventa": 1150.00,
        "estado_eol": "Próximo EoL",
        "piezas": 7541,
        "minifiguras_exclusivas": True,
        "publico_adulto_afol": True,
        "alias": ["UCS Falcon", "Ultimate Collector Millennium Falcon"],
        "notas": "El set más reconocible de Star Wars UCS; altísima liquidez en reventa.",
    },
    "75252": {
        "nombre": "Imperial Star Destroyer UCS",
        "tematica": "Star Wars UCS",
        "precio_oficial": 699.99,
        "precio_objetivo_compra": 490.00,
        "precio_estimado_reventa": 900.00,
        "estado_eol": "Retirado",
        "piezas": 4784,
        "minifiguras_exclusivas": True,
        "publico_adulto_afol": True,
        "alias": ["UCS Star Destroyer"],
        "notas": "Retirado en 2022; el UCS más grande por eslora en su momento.",
    },
    "10276": {
        "nombre": "Coliseo (Colosseum)",
        "tematica": "Modular Buildings",
        "precio_oficial": 549.99,
        "precio_objetivo_compra": 385.00,
        "precio_estimado_reventa": 780.00,
        "estado_eol": "Retirado",
        "piezas": 9036,
        "minifiguras_exclusivas": False,
        "publico_adulto_afol": True,
        "alias": ["Colosseum", "Colosseum Rome", "Coliseo de Roma", "Coliseum"],
        "notas": "Más de 9.000 piezas: el récord de piezas durante años.",
    },
    "10307": {
        "nombre": "Torre Eiffel (Eiffel Tower)",
        "tematica": "Icons / Arquitectura",
        "precio_oficial": 629.99,
        "precio_objetivo_compra": 440.00,
        "precio_estimado_reventa": 820.00,
        "estado_eol": "Retirado",
        "piezas": 10001,
        "minifiguras_exclusivas": False,
        "publico_adulto_afol": True,
        "alias": ["Eiffel Tower", "Torre Eiffel"],
        "notas": "Primer set de más de 10.000 piezas; icono arquitectónico.",
    },
    "10294": {
        "nombre": "Titanic",
        "tematica": "Icons",
        "precio_oficial": 679.99,
        "precio_objetivo_compra": 475.00,
        "precio_estimado_reventa": 850.00,
        "estado_eol": "Activo",
        "piezas": 9090,
        "minifiguras_exclusivas": False,
        "publico_adulto_afol": True,
        "alias": ["LEGO Titanic"],
        "notas": "Set altamente demandado por público adulto no estrictamente LEGO.",
    },
    "10297": {
        "nombre": "Boutique Hotel (Modular)",
        "tematica": "Modular Buildings",
        "precio_oficial": 229.99,
        "precio_objetivo_compra": 160.00,
        "precio_estimado_reventa": 380.00,
        "estado_eol": "Retirado",
        "piezas": 3066,
        "minifiguras_exclusivas": True,
        "publico_adulto_afol": True,
        "alias": ["Boutique Hotel", "Modular Hotel"],
        "notas": "Los modulares retirados escalan de forma consistente tras el EoL.",
    },
    "71043": {
        "nombre": "Hogwarts Castle",
        "tematica": "Harry Potter",
        "precio_oficial": 469.99,
        "precio_objetivo_compra": 330.00,
        "precio_estimado_reventa": 720.00,
        "estado_eol": "Activo",
        "piezas": 6020,
        "minifiguras_exclusivas": True,
        "publico_adulto_afol": True,
        "alias": ["Hogwarts Castle", "Castillo de Hogwarts"],
        "notas": "El set de Harry Potter más demandado a nivel coleccionista.",
    },
    "21330": {
        "nombre": "Home Alone House",
        "tematica": "Ideas",
        "precio_oficial": 299.99,
        "precio_objetivo_compra": 210.00,
        "precio_estimado_reventa": 420.00,
        "estado_eol": "Activo",
        "piezas": 3955,
        "minifiguras_exclusivas": True,
        "publico_adulto_afol": True,
        "alias": ["Home Alone", "Solo en casa", "McCullister"],
        "notas": "Set de licencia navideña con minifiguras exclusivas.",
    },
    "10303": {
        "nombre": "Loop Coaster (Montaña Rusa)",
        "tematica": "Fairground",
        "precio_oficial": 399.99,
        "precio_objetivo_compra": 280.00,
        "precio_estimado_reventa": 650.00,
        "estado_eol": "Retirado",
        "piezas": 3756,
        "minifiguras_exclusivas": False,
        "publico_adulto_afol": True,
        "alias": ["Loop Coaster", "Roller Coaster"],
        "notas": "Sets técnicos de feria: nicho muy coleccionable.",
    },
    "21318": {
        "nombre": "Tree House",
        "tematica": "Ideas",
        "precio_oficial": 249.99,
        "precio_objetivo_compra": 175.00,
        "precio_estimado_reventa": 390.00,
        "estado_eol": "Retirado",
        "piezas": 3036,
        "minifiguras_exclusivas": False,
        "publico_adulto_afol": True,
        "alias": ["Tree House", "Casa del árbol"],
        "notas": "Piezas botánicas reutilizables; muy querido por AFOL.",
    },
    "76391": {
        "nombre": "Hogwarts Icons Collectors' Edition",
        "tematica": "Harry Potter",
        "precio_oficial": 299.99,
        "precio_objetivo_compra": 210.00,
        "precio_estimado_reventa": 380.00,
        "estado_eol": "Retirado",
        "piezas": 3010,
        "minifiguras_exclusivas": True,
        "publico_adulto_afol": True,
        "alias": ["Hogwarts Icons"],
        "notas": "Set de aniversario con minifiguras doradas exclusivas.",
    },
    "42115": {
        "nombre": "Lamborghini Sián FKP 37 (Technic)",
        "tematica": "Technic Ultimate",
        "precio_oficial": 449.99,
        "precio_objetivo_compra": 315.00,
        "precio_estimado_reventa": 560.00,
        "estado_eol": "Retirado",
        "piezas": 3696,
        "minifiguras_exclusivas": False,
        "publico_adulto_afol": True,
        "alias": ["Lamborghini Sian", "Lamborghini Sian FKP 37"],
        "notas": "Línea Technic Ultimate: gran demanda de coleccionistas de coches.",
    },
    "42083": {
        "nombre": "Bugatti Chiron (Technic)",
        "tematica": "Technic Ultimate",
        "precio_oficial": 369.99,
        "precio_objetivo_compra": 260.00,
        "precio_estimado_reventa": 590.00,
        "estado_eol": "Retirado",
        "piezas": 3599,
        "minifiguras_exclusivas": False,
        "publico_adulto_afol": True,
        "alias": ["Bugatti Chiron"],
        "notas": "El primer Technic Ultimate; revalorización sostenida.",
    },
    # --- Ampliación: clásicos EoL con reventa consolidada -------------------
    "75257": {
        "nombre": "Millennium Falcon (episodio IX)",
        "tematica": "Star Wars",
        "precio_oficial": 169.99,
        "precio_objetivo_compra": 120.00,
        "precio_estimado_reventa": 290.00,
        "estado_eol": "Retirado",
        "piezas": 1353,
        "minifiguras_exclusivas": False,
        "publico_adulto_afol": False,
        "alias": ["Falcon IX", "Halcón Milenario IX", "Falcon episodio 9"],
        "notas": "Versión asequible del Falcon: entra al EoL fácil y sube con rapidez.",
    },
    "75309": {
        "nombre": "Republic Gunship UCS",
        "tematica": "Star Wars UCS",
        "precio_oficial": 399.99,
        "precio_objetivo_compra": 280.00,
        "precio_estimado_reventa": 620.00,
        "estado_eol": "Retirado",
        "piezas": 3292,
        "minifiguras_exclusivas": True,
        "publico_adulto_afol": True,
        "alias": ["Republic Gunship", "UCS Gunship"],
        "notas": "UCS con enorme demanda por fans de Clone Wars.",
    },
    "10188": {
        "nombre": "Death Star",
        "tematica": "Star Wars",
        "precio_oficial": 419.99,
        "precio_objetivo_compra": 295.00,
        "precio_estimado_reventa": 1100.00,
        "estado_eol": "Retirado",
        "piezas": 3803,
        "minifiguras_exclusivas": True,
        "publico_adulto_afol": True,
        "alias": ["Death Star", "Estrella de la Muerte"],
        "notas": "Clásico ya retirado: solo se compra en segunda mano, reventa muy alta.",
    },
    "10255": {
        "nombre": "Assembly Square (Modular)",
        "tematica": "Modular Buildings",
        "precio_oficial": 299.99,
        "precio_objetivo_compra": 210.00,
        "precio_estimado_reventa": 470.00,
        "estado_eol": "Retirado",
        "piezas": 4002,
        "minifiguras_exclusivas": False,
        "publico_adulto_afol": True,
        "alias": ["Assembly Square", "Plaza del ensamblaje"],
        "notas": "Modular de aniversario; entre los más buscados de la línea.",
    },
    "10260": {
        "nombre": "Downtown Diner (Modular)",
        "tematica": "Modular Buildings",
        "precio_oficial": 149.99,
        "precio_objetivo_compra": 105.00,
        "precio_estimado_reventa": 430.00,
        "estado_eol": "Retirado",
        "piezas": 2480,
        "minifiguras_exclusivas": False,
        "publico_adulto_afol": True,
        "alias": ["Downtown Diner", "Restaurante modular"],
        "notas": "Ya solo en segunda mano: referencia obligada de los modulares.",
    },
    "21319": {
        "nombre": "Central Perk (Friends)",
        "tematica": "Ideas / TV",
        "precio_oficial": 79.99,
        "precio_objetivo_compra": 55.00,
        "precio_estimado_reventa": 150.00,
        "estado_eol": "Retirado",
        "piezas": 1070,
        "minifiguras_exclusivas": True,
        "publico_adulto_afol": False,
        "alias": ["Central Perk", "Friends Central Perk"],
        "notas": "Set de nicho con seguidores fieles; ROI alto por precio de entrada bajo.",
    },
    "42100": {
        "nombre": "Liebherr R 9800 (Technic)",
        "tematica": "Technic",
        "precio_oficial": 449.99,
        "precio_objetivo_compra": 315.00,
        "precio_estimado_reventa": 700.00,
        "estado_eol": "Retirado",
        "piezas": 4108,
        "minifiguras_exclusivas": False,
        "publico_adulto_afol": True,
        "alias": ["Liebherr", "Excavadora Technic"],
        "notas": "Technic de gran escala con motorización: nicho muy cotizado.",
    },
    "43179": {
        "nombre": "Mickey Mouse & Minnie Mouse",
        "tematica": "Disney",
        "precio_oficial": 179.99,
        "precio_objetivo_compra": 125.00,
        "precio_estimado_reventa": 330.00,
        "estado_eol": "Retirado",
        "piezas": 1739,
        "minifiguras_exclusivas": False,
        "publico_adulto_afol": True,
        "alias": ["Mickey Mouse", "Minnie Mouse"],
        "notas": "Coleccionables Disney: demanda cruzada de no-AFOL.",
    },
    "71374": {
        "nombre": "Nintendo Entertainment System",
        "tematica": "Ideas / Gaming",
        "precio_oficial": 269.99,
        "precio_objetivo_compra": 190.00,
        "precio_estimado_reventa": 420.00,
        "estado_eol": "Retirado",
        "piezas": 2646,
        "minifiguras_exclusivas": False,
        "publico_adulto_afol": True,
        "alias": ["Nintendo Entertainment System", "NES", "Nintendo"],
        "notas": "Retro-gaming: alta demanda de nostálgicos, no solo AFOL.",
    },
    "21322": {
        "nombre": "Barracuda Bay (Piratas)",
        "tematica": "Ideas / Piratas",
        "precio_oficial": 199.99,
        "precio_objetivo_compra": 140.00,
        "precio_estimado_reventa": 380.00,
        "estado_eol": "Retirado",
        "piezas": 2545,
        "minifiguras_exclusivas": True,
        "publico_adulto_afol": True,
        "alias": ["Barracuda Bay", "Bahía Barracuda", "Pirate Ship"],
        "notas": "Piratas: nicho con revalorización fuerte tras el EoL.",
    },
    "10300": {
        "nombre": "Delorean Back to the Future",
        "tematica": "Icons / Cine",
        "precio_oficial": 199.99,
        "precio_objetivo_compra": 140.00,
        "precio_estimado_reventa": 340.00,
        "estado_eol": "Próximo EoL",
        "piezas": 1872,
        "minifiguras_exclusivas": False,
        "publico_adulto_afol": True,
        "alias": ["Delorean", "Back to the Future", "Regreso al futuro"],
        "notas": "Licencia de cine con tirón intergeneracional.",
    },
    "10306": {
        "nombre": "Atari 2600",
        "tematica": "Icons / Gaming",
        "precio_oficial": 239.99,
        "precio_objetivo_compra": 170.00,
        "precio_estimado_reventa": 400.00,
        "estado_eol": "Retirado",
        "piezas": 2532,
        "minifiguras_exclusivas": False,
        "publico_adulto_afol": True,
        "alias": ["Atari 2600", "Atari"],
        "notas": "Retro-gaming, misma demanda que la NES.",
    },
    "10295": {
        "nombre": "Porsche 911",
        "tematica": "Icons / Coches",
        "precio_oficial": 169.99,
        "precio_objetivo_compra": 120.00,
        "precio_estimado_reventa": 310.00,
        "estado_eol": "Activo",
        "piezas": 1458,
        "minifiguras_exclusivas": False,
        "publico_adulto_afol": True,
        "alias": ["Porsche 911", "Porsche"],
        "notas": "Coches clásicos: conversión rápida en marketplaces.",
    },
    "10271": {
        "nombre": "Fiat 500",
        "tematica": "Icons / Coches",
        "precio_oficial": 79.99,
        "precio_objetivo_compra": 55.00,
        "precio_estimado_reventa": 190.00,
        "estado_eol": "Retirado",
        "piezas": 960,
        "minifiguras_exclusivas": False,
        "publico_adulto_afol": True,
        "alias": ["Fiat 500", "Fiat"],
        "notas": "Entrada barata, ROI porcentual alto.",
    },
    "10274": {
        "nombre": "Ecto-1 (Cazafantasmas)",
        "tematica": "Icons / Cine",
        "precio_oficial": 239.99,
        "precio_objetivo_compra": 170.00,
        "precio_estimado_reventa": 390.00,
        "estado_eol": "Retirado",
        "piezas": 2352,
        "minifiguras_exclusivas": False,
        "publico_adulto_afol": True,
        "alias": ["Ecto-1", "Ecto 1", "Ghostbusters", "Cazafantasmas"],
        "notas": "Licencia ochentera con base de fans leal.",
    },
}

# ---------------------------------------------------------------------------
# 6. AJUSTES DE RED Y EJECUCIÓN
# ---------------------------------------------------------------------------
RED = {
    "timeout_segundos": 15,
    # Reintentos internos de feedparser (no cuentan el primer intento).
    "reintentos_feedparser": 1,
    # Reddit devuelve HTTP 429 a User-Agents que se identifican como bots
    # ("LegoSearch/1.0..."). Verificado: con UA de navegador la misma URL
    # responde 200; con UA de bot, 429. No es bloqueo de IP.
    "user_agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    # Entradas máximas por feed (evita feeds infinitos de Reddit).
    # Se descartan antes las entradas más antiguas que FILTROS["dias_max_antiguedad_news"].
    "max_entradas_por_feed": 50,
    # Pausa mínima (s) entre peticiones al mismo host. Aunque el 429 era por
    # el User-Agent, Reddit limita ráfagas por IP: mantenemos una pausa cortés.
    # Pausa mínima (s) entre peticiones al mismo host. Reddit permite ~1
    # petición por minuto por IP: comprobado que la 2ª a los 3 s da 429.
    "pausa_entre_peticiones_s": 62.0,
    "hosts_con_pausa": ["reddit.com"],
    # Hosts que se descargan con curl en lugar de requests.
    # Reddit bloquea (429) la huella TLS de requests aunque se envíe un
    # User-Agent de navegador; curl sí obtiene HTTP 200.
    "hosts_via_curl": ["reddit.com"],
}
