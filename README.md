# 🧱 LegoSearch

Monitor **Lean** de oportunidades de inversión en LEGO.

Detecta ofertas leídas en **feeds RSS públicos** (sin scraping de tiendas
protegidas por Cloudflare), las cruza con una **watchlist avanzada de sets** y
aplica un **motor dual** (criterios de coleccionista + cálculo financiero de
beneficio neto y ROI). El resultado se exporta en `opportunities.json` listo
para que lo consuma un **superagente externo** (GitHub Actions, LLM, bot propio).

> **Sin notificaciones propias.** LegoSearch no envía Telegram, email ni push:
> su única salida es un JSON estructurado y un reporte Markdown para humanos.

---

## 1. Instalación

Requiere **Python 3.10+** (probado con 3.11).

```bash
git clone https://github.com/<tu-usuario>/LegoSearch.git
cd LegoSearch

python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Dependencias: `feedparser` y `requests` (ver `requirements.txt`).

## 2. Primeras ejecuciones

```bash
python main.py --simulate          # demo offline con datos de ejemplo
python main.py                     # escaneo real de los feeds
python main.py --dry-run -v        # escaneo real sin escribir ficheros
```

Salidas:

| Fichero | Contenido |
|---|---|
| `opportunities.json` | Datos estructurados para el agente (histórico acumulado). |
| `reports/reporte_YYYY-MM-DD.md` | Resumen legible de cada ejecución. |
| `reports/oportunidades_YYYY-MM-DD.csv` | Ranking en CSV (`;`, BOM UTF-8): se abre en Excel directamente. |

Opciones de CLI:

| Flag | Descripción |
|---|---|
| `--simulate` | No usa red; datos de ejemplo para demos y CI. |
| `--dry-run` | No escribe ficheros, solo imprime resultados. |
| `--output RUTA` | JSON de salida alternativo. |
| `--only-feeds TEXTO…` | Escanea solo fuentes cuyo nombre/URL contenga el texto (`chollometro`, `reddit`…). |
| `--max-entradas N` | Entradas máximas por feed (por defecto 50). |
| `--no-report` | No genera el reporte Markdown. |
| `--top N` | Muestra solo las N mejores oportunidades en la tabla. |
| `--tabla` | Imprime el ranking del JSON existente y sale (sin red). |
| `--sin-brickset` | No consulta la API de Brickset aunque haya `BRICKSET_API_KEY`. |
| `--eol [--top N]` | Calendario de retiradas: qué sets dejan de fabricarse y cuándo. |
| `--csv RUTA` | Exporta el ranking a CSV (con `--tabla`, sin volver a rastrear). |
| `--verbose`, `-v` | Log detallado, incluyendo **por qué se descarta** cada candidato. |

## 3. Configuración (`config.py`)

Todo el comportamiento se ajusta en un único fichero, sin tocar el motor:

- **`FINANZAS`** — `gastos_plataforma_pct` (10 %), `gastos_envio_pct` (5 %),
  `roi_minimo_pct` (25 %), `beneficio_neto_minimo_eur` (10 €).
- **`FILTROS`** — criterios de coleccionista (`piezas_alto_volumen`,
  `score_minimo_coleccionista`), vigencia de la oferta
  (`dias_max_antiguedad_oferta` = 45, `dias_max_antiguedad_news` = 365),
  anti-ruido (`precio_minimo_pct_pvp`) y umbrales de scoring
  (`score_total_minimo`, `confianza_minima_cruce`).

> Los umbrales están deliberadamente **bajos**: el score sirve para *ordenar*
> la tabla, no para esconder resultados. El filtro que de verdad protege la
> calidad es el contexto LEGO + los umbrales financieros.
- **`FEEDS`** — fuentes RSS. Cada entrada tiene `tipo` (`ofertas`, `eol`,
  `noticias`) y `valor_eur` (fiabilidad 0–1, pondera el score).
- **`WATCHLIST`** — base de datos local de sets (ver abajo).
- **`RED`** — timeouts, reintentos, `User-Agent` y pausa entre peticiones a Reddit.

### Añadir un set a la watchlist

```python
WATCHLIST = {
    # ...
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
        "alias": ["Eiffel Tower", "Torre Eiffel"],   # opcional
        "notas": "contexto para humanos",             # opcional
    },
}
```

### Watchlist personal sin tocar el repo

Crea `watchlist.json` en la raíz con el mismo formato (objeto
`{"referencia": {...}}`). Se **fusiona** con la de `config.py` y sus entradas
tienen **prioridad**. El fichero está en `.gitignore`: perfecto para tus sets
privados sin ensuciar el repositorio.

## 4. Cómo funciona el motor

```
RSS ∈ FEEDS ──► feedparser ──► normalizar texto
                                   │
                 regex de referencias (75192) y de precios (€/EUR)
                                   │
                    cruce con WATCHLIST (nº o nombre/alias)
                                   │
                ┌──────────────────┴──────────────────┐
        FILTRO A: coleccionista              FILTRO B: financiero
        AFOL + piezas + exclusivas           compra → gastos → ROI
                └──────────────────┬──────────────────┘
                                   │  score 0-100
                         opportunities.json (histórico)
```

1. **Rastreo robusto.** Cada feed se descarga con timeout; si devuelve 403/429 o
   falla la conexión, se reintenta y, si persiste, se registra en
   `estado_feeds` y **la ejecución continúa**.
2. **Extracción.** Referencias de 4–6 dígitos (se descartan años y grupos de
   miles) y precios en formatos ES/EN (`599,99 €`, `€599.99`, `EUR 84.99`,
   `84.99€`, `599 euros`), clasificados como `oferta`, `pvp` o `general` según
   el contexto.
3. **Cruce.** Coincidencia por referencia numérica o por nombre/alias en el
   título. La **confianza de cruce** (0–100) penaliza coincidencias débiles.
4. **Filtro dual.**
   - *Coleccionista* (0–3): set AFOL + piezas ≥ 1000 + minifiguras exclusivas.
   - *Financiero*: `coste_total = compra + gastos_plataforma + gastos_envio`,
     `beneficio_neto = reventa − gastos − compra`, `ROI = beneficio / coste_total`.
5. **Score compuesto** (0–100): coleccionista 30, confianza 25, ROI 25,
   fuente 10, frescura 10 (+5 si el precio detectado ≤ objetivo).
6. **Exportación.** Se añaden solo las nuevas (IDs estables) al histórico; el
   resto se ignora como duplicado. Escritura **atómica** del JSON.

> **Nota sobre Chollometro:** su endpoint histórico `rss/search?q=lego`
> devuelve 404 desde 2025. Config usa `rss/hot` (público y estable). Si tienes
> el token de una alerta de palabra clave, usa
> `https://www.chollometro.com/rssx/keyword-alarm/lego`.

## 5. El fichero `opportunities.json`

Ejemplo completo: [`examples/opportunities.example.json`](examples/opportunities.example.json).

```json
{
  "schema_version": "1.0",
  "app": { "name": "LegoSearch", "version": "1.0.0" },
  "generado_en": "2026-11-10T17:03:43+00:00",
  "resumen": {
    "sets_en_watchlist": 13,
    "entradas_analizadas": 165,
    "validas": 1,
    "descartadas": 2,
    "descartadas_detalle": [
      { "referencia": "10297", "motivo": "sin precio detectado ni señal de oferta" }
    ],
    "total_historicas": 1,
    "nuevas_en_esta_ejecucion": 1,
    "duplicadas_ignoradas": 0,
    "temas": { "Star Wars UCS": 1 }
  },
  "estado_feeds": [
    { "nombre": "Brickset News", "ok": true, "entradas": 40 },
    { "nombre": "Reddit r/lego", "ok": false, "entradas": 0 }
  ],
  "oportunidades": [
    {
      "id": "3f2c9a7e14b85d01",
      "referencia": "75192",
      "nombre": "Millennium Falcon UCS",
      "tematica": "Star Wars UCS",
      "estado_eol": "Activo",
      "score_total": 82.9,
      "confianza_cruce": 100,
      "criterios_coleccionista": {
        "score": 3, "max": 3,
        "detalle": "AFOL + 7,541 piezas + minifiguras exclusivas"
      },
      "precios": {
        "oficial": 849.99,
        "objetivo_compra": 600.0,
        "estimado_reventa": 1150.0,
        "detectado": null,
        "detectado_texto": null,
        "compra": 600.0,
        "origen": "precio_objetivo_compra",
        "descuento_vs_pvp_pct": 29.41,
        "excede_objetivo": false
      },
      "financiero": {
        "precio_compra": 600.0,
        "gastos_plataforma": 115.0,
        "gastos_envio": 57.5,
        "coste_total": 772.5,
        "ingresos_netos": 977.5,
        "beneficio_neto": 377.5,
        "roi_pct": 48.87,
        "margen_mensual_pct": 2.71
      },
      "plan_reventa": {
        "precio_venta_sugerido": 1092.5,
        "plazo_reventa_meses": 18,
        "tipo_operacion": "tenencia",
        "horizonte": "~18 mes(es) — mantiene valor y captura la subida del EoL"
      },
      "tipo": "chollo",
      "ranking": 1,
      "fuente": {
        "nombre": "Reddit r/legodeals",
        "tipo": "ofertas",
        "titulo": "Buy LEGO Star Wars Millennium Falcon … | Argos",
        "url": "https://www.reddit.com/r/legodeals/comments/example/…",
        "fecha": "2026-11-09T10:12:00+00:00",
        "antiguedad_dias": 1.3
      },
      "detectada_en": "2026-11-10T17:03:43+00:00",
      "notas_set": "El set más reconocible de Star Wars UCS…"
    }
  ]
}
```

### Contrato para el superagente

- `schema_version` sube cuando cambia la forma del JSON; el agente debe
  comprobarlo antes de parsear.
- `oportunidades[].id` es **estable** (hash de set + fuente + URL + precio) y
  sirve para deduplicar entre ejecuciones.
- `oportunidades` está ordenado de **mayor a menor `score_total`**.
- `precios.origen` indica la procedencia del precio de compra:
  - `oferta_detectada`: precio encontrado en el feed.
  - `precio_objetivo_compra`: la entrada era una señal de oferta sin precio
    concreto y se usa tu objetivo de compra como escenario.
- `precios.excede_objetivo = true` avisa de que el precio detectado supera tu
  máximo de compra (oportunidad "menos ideal").
- `resumen.descartadas_detalle` explica por qué se descartaron candidatos:
  útil para detectar criterios demasiado estrictos.
- `estado_feeds` permite al agente saber si un "0 oportunidades" se debe a un
  bloqueo de red.

### Ejemplos de consumo

**Python:**

```python
import json

with open("opportunities.json", encoding="utf-8") as fh:
    data = json.load(fh)

top = [o for o in data["oportunidades"] if o["score_total"] >= 80]
for o in top:
    print(o["referencia"], o["nombre"], o["financiero"]["roi_pct"], "%")
```

**`jq`:**

```bash
jq '.oportunidades[] | select(.financiero.roi_pct > 50) | "\(.referencia) \(.nombre): \(.financiero.beneficio_neto)€"' opportunities.json
```

**GitHub Actions (workflow incluido):** `.github/workflows/monitor.yml` publica
`opportunities.json` y `oportunidades.csv` en la rama `runtime-data`.

El escaneo automático está **desactivado**: el `schedule` está comentado a
propósito mientras el ROI sigue sin verificar. Se lanza a mano:

```bash
# Desde la terminal, con un token con permiso "workflow"
curl -X POST \
  -H "Authorization: Bearer $GITHUB_TOKEN" \
  -H "Accept: application/vnd.github+json" \
  https://api.github.com/repos/mpinartr/LegoSearch/actions/workflows/monitor.yml/dispatches \
  -d '{"ref":"main"}'
```

Para reactivar el escaneo cada 6 h, descomenta el bloque `schedule` en el
workflow. El superagente puede lanzarlo igual con esa misma petición.

## 6. Estructura del proyecto

```
LegoSearch/
├── config.py                 # Finanzas, filtros, feeds y watchlist
├── brickset.py               # Precios reales vía API de Brickset (opcional)
├── calendario.py             # Calendario de retiradas (EoL) por tema y año
├── main.py                   # Motor completo (rastreo → filtro dual → export)
├── requirements.txt          # feedparser, requests
├── README.md
├── examples/
│   └── opportunities.example.json
└── .github/workflows/
    └── monitor.yml           # Ejecución programada opcional
```

`opportunities.json`, `watchlist.json` y `reports/` son salidas locales y están
en `.gitignore`.

## 7. Decisiones de diseño y límites

- **Lean, no scraping.** Solo RSS público. Las tiendas con Cloudflare (Amazon,
  El Corte Inglés, LEGO.com…) quedan fuera a propósito para evitar bloqueos.
- **Los precios de reventa son estimaciones de planificación**, no cotizaciones
  de mercado. Ajusta la watchlist periódicamente.
- **Anti-ruido.** Un precio inferior al 10 % del PVP (probable cuota de sorteo o
  error de regex), o un precio "suelto" en un feed editorial sin más importes,
  se descarta.
- **Reddit** limita a ~1 petición por minuto por IP (HTTP 429). Además, su
  filtro detecta la huella TLS de `requests`, así que esos feeds se descargan
  con `curl` (`RED["hosts_via_curl"]`). En GitHub Actions, con IPs compartidas,
  Reddit puede fallar igualmente: el motor continúa sin ese feed.
- **Sin notificaciones.** El diseño delega la alerta en el superagente externo.

### Precios reales con Brickset (opcional)

El punto débil del enfoque RSS es que los feeds publican noticias, no precios:
de 165 entradas, solo ~5 traen un importe y casi siempre es el PVP de un
lanzamiento. La [API de Brickset](https://brickset.com/tools/webservices/requestkey)
cubre ese hueco con PVP oficial, precio de LEGO.com, de Amazon y **valor de
mercado de BrickLink** (nuevo y usado) por set.

```bash
# 1. Key gratuita en brickset.com/tools/webservices/requestkey
echo 'BRICKSET_API_KEY=tu_key' >> .env
set -a && source .env && set +a

python main.py --dry-run    # ahora usa precios de mercado reales
```

Sin key, todo funciona igual que antes: el enriquecimiento es opcional y el
motor nunca falla por no tenerla. En GitHub Actions, añade
`BRICKSET_API_KEY` (y opcionalmente `BRICKSET_USER_HASH`) como *secret*.

### Calendario de retiradas (EoL)

La misma API da `exitDate`, la fecha en que LEGO deja de fabricar un set.
Es la señal más accionable para revender: tras la retirada el valor sube.

```bash
python main.py --eol --top 20    # los 20 más caros que se retiran
```

Con los 10 temas y 3 años de `CALENDARIO_EOL` son ~30 consultas y devuelve
~170 sets con retirada en los próximos 24 meses. Los 114 que salen con
fecha `2026-12-31` son los candidatos inmediatos. También se incluye en
`opportunities.json` como `calendario_eol`, así el bot puede avisar sin
consultar Brickset.

La cobertura de `exitDate` es del ~50-60 % según tema, y `exitDate` suele ser
fin de mes o de año: tómalo como una ventana orientativa, no como un día
exacto.

## 8. Licencia

MIT — ver [`LICENSE`](LICENSE).

---

Proyecto personal de monitorización de coleccionismo LEGO. No afiliado a LEGO®,
a Pepper/Chollometro ni a las fuentes citadas.
