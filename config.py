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

    # Umbrales de calidad financiera: una oportunidad solo se exporta si
    # supera TODOS los umbrales aplicables.
    "roi_minimo_pct": 40.0,            # ROI neto mínimo aceptable
    "beneficio_neto_minimo_eur": 15.0,  # evita "ROIs altos" sobre sets baratos

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
    # Un set se considera apto si cumple AL MENOS UNO de estos criterios:
    "piezas_alto_volumen": 1_000,   # piezas >= este valor => volumen alto
    "score_minimo_coleccionista": 2,  # 0-3 puntos (piezas, exclusivas, AFOL)

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

    # --- Umbrales de scoring final --------------------------------------
    "score_total_minimo": 55.0,       # 0-100: combo colección + calidad oferta
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
        # su feed "hot": incluye LEGO con frecuencia y el motor descarta el resto.
        # Si tienes el token de tu alerta, cámbialo por:
        #   https://www.chollometro.com/rssx/keyword-alarm/lego
        "nombre": "Chollometro (LEGO)",
        "url": "https://www.chollometro.com/rss/hot",
        "tipo": "ofertas",
        "valor_eur": 1.0,
    },
]

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
        "precio_objetivo_compra": 600.00,
        "precio_estimado_reventa": 1150.00,
        "estado_eol": "Activo",
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
        "precio_objetivo_compra": 480.00,
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
        "precio_objetivo_compra": 380.00,
        "precio_estimado_reventa": 780.00,
        "estado_eol": "Retirado",
        "piezas": 9036,
        "minifiguras_exclusivas": False,
        "publico_adulto_afol": True,
        "alias": ["Colosseum", "Colosseum Rome", "Coliseo de Roma"],
        "notas": "Más de 9.000 piezas: el récord de piezas durante años.",
    },
    "10307": {
        "nombre": "Torre Eiffel (Eiffel Tower)",
        "tematica": "Icons / Arquitectura",
        "precio_oficial": 629.99,
        "precio_objetivo_compra": 450.00,
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
        "precio_oficial": 599.99,
        "precio_objetivo_compra": 430.00,
        "precio_estimado_reventa": 850.00,
        "estado_eol": "Retirado",
        "piezas": 9090,
        "minifiguras_exclusivas": False,
        "publico_adulto_afol": True,
        "alias": ["LEGO Titanic"],
        "notas": "Set altamente demandado por público adulto no estrictamente LEGO.",
    },
    "10297": {
        "nombre": "Boutique Hotel (Modular)",
        "tematica": "Modular Buildings",
        "precio_oficial": 219.99,
        "precio_objetivo_compra": 150.00,
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
        "estado_eol": "Retirado",
        "piezas": 6020,
        "minifiguras_exclusivas": True,
        "publico_adulto_afol": True,
        "alias": ["Hogwarts Castle", "Castillo de Hogwarts"],
        "notas": "El set de Harry Potter más demandado a nivel coleccionista.",
    },
    "21330": {
        "nombre": "Home Alone House",
        "tematica": "Ideas",
        "precio_oficial": 249.99,
        "precio_objetivo_compra": 170.00,
        "precio_estimado_reventa": 420.00,
        "estado_eol": "Retirado",
        "piezas": 3955,
        "minifiguras_exclusivas": True,
        "publico_adulto_afol": True,
        "alias": ["Home Alone", "Solo en casa", "McCullister"],
        "notas": "Set de licencia navideña con minifiguras exclusivas.",
    },
    "10303": {
        "nombre": "Loop Coaster (Montaña Rusa)",
        "tematica": "Fairground",
        "precio_oficial": 449.99,
        "precio_objetivo_compra": 310.00,
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
        "precio_oficial": 199.99,
        "precio_objetivo_compra": 130.00,
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
        "precio_oficial": 249.99,
        "precio_objetivo_compra": 165.00,
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
        "precio_oficial": 379.99,
        "precio_objetivo_compra": 270.00,
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
        "precio_oficial": 379.99,
        "precio_objetivo_compra": 260.00,
        "precio_estimado_reventa": 590.00,
        "estado_eol": "Retirado",
        "piezas": 3599,
        "minifiguras_exclusivas": False,
        "publico_adulto_afol": True,
        "alias": ["Bugatti Chiron"],
        "notas": "El primer Technic Ultimate; revalorización sostenida.",
    },
}

# ---------------------------------------------------------------------------
# 6. AJUSTES DE RED Y EJECUCIÓN
# ---------------------------------------------------------------------------
RED = {
    "timeout_segundos": 15,
    # Reintentos internos de feedparser (no cuentan el primer intento).
    "reintentos_feedparser": 1,
    "user_agent": (
        "LegoSearch/1.0 (monitor de oportunidades LEGO; contacto: "
        "cambia-este-email@example.com)"
    ),
    # Entradas máximas por feed (evita feeds infinitos de Reddit).
    # Se descartan antes las entradas más antiguas que FILTROS["dias_max_antiguedad_news"].
    "max_entradas_por_feed": 50,
    # Pausa mínima (s) entre peticiones al mismo host. Reddit devuelve 429 si
    # recibe varias peticiones seguidas desde la misma IP en poco tiempo.
    "pausa_entre_peticiones_s": 2.0,
    "hosts_con_pausa": ["reddit.com"],
}
