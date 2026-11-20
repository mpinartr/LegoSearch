# Plan — Datos de mercado autónomos para reventa fiable

> **Objetivo:** que LegoSearch obtenga, **sin búsquedas manuales**, resultados
> fiables de sets que se pueden comprar hoy, que van a dejar de fabricarse
> (EoL) y que subirán de valor en poco tiempo, para revenderlos con un ROI
> basado en datos reales y no en estimaciones.

Este documento es la planificación para pasar de un motor que *calcula* con
precios estimados a mano a uno que *consulta* el mercado automáticamente en cada
escaneo. Complementa a [`PLAN_TDD.md`](./PLAN_TDD.md): todo cambio aquí descrito
sigue el ciclo red → verde → refactor y queda cubierto por tests sin red.

---

## 0. Estado de implementación

| Fase | Estado | Evidencia |
|---|---|---|
| **A — BrickLinkClient (OAuth)** | ✅ Completada | `mercado.py`, firma validada con el vector canónico RFC 5849 |
| **B — Reventa real en pipeline** | ✅ Completada | `enriquecer_watchlist`, `reventa_verificada=true`, flag `--sin-bricklink` |
| **C — Cobertura EoL** | ✅ Completada *(reorientada)* | Brickset ampliado (12 temas × 4 años) + presupuesto `max_consultas`; LEGO.com descartado (403) |
| **D — Filtro de liquidez** | ✅ Completada | `liquidez_minima_unidades` en `FILTROS`; solo filtra si BrickLink aporta `unidades` |
| **E — Precio de compra "hoy"** | ✅ Completada | `bricklink_compra` (stock) + origen `mercado_stock` en el pipeline |

> **Cambio respecto al plan original:** el acceso a LEGO.com devuelve **403**
> (protección anti-bot), así que la señal `RETIRING_SOON` no es viable sin
> scraping frágil. La cobertura EoL se mejora vía Brickset, que es la fuente
> oficial y accesible.

---

## 1. Problema que se resuelve

Hoy la aplicación **ya hace bien la lógica** (filtro dual, scoring, cruce,
calendario EoL), pero tres datos críticos son manuales o poco fiables:

| Dato | Estado actual | Consecuencia |
|---|---|---|
| Precio de reventa | Estimación a mano en `config.py` (`reventa_verificada: false`) | ROI orientativo, no real |
| Fecha de retirada (EoL) | `exitDate` de Brickset, cobertura ~50-60 % | Se pierden sets candidatos |
| Precio de compra actual | Fuentes RSS escasas; Chollometro caído; Reddit 429 | No se sabe si hoy está barato |
| Liquidez | No se mide | Un set puede "subir" pero no venderse |

---

## 2. Fuentes de datos elegidas (y por qué)

### 2.1 BrickLink — precio de reventa real + liquidez *(fuente principal)*
Marketplace de segunda mano de LEGO. Su **Price Guide** devuelve precios de
ventas **realizadas** y volumen, exactamente lo que falta.

- Autenticación: **OAuth 1.0** (Consumer Key/Secret + Token/Secret). Requiere
  cuenta BrickLink verificada.
- Endpoints:
  - Ventas realizadas:
    ```
    GET https://api.bricklink.com/api/store/v1/items/SET/{set_number}/price?guide_type=sold&new_or_used=N&v=1
    ```
    Campos: `min_price`, `avg_price`, `max_price`, `qty_avg_price`,
    `total_quantity`, `unit_quantity`.
  - Anuncios actuales (para "comprar hoy"):
    ```
    .../price?guide_type=stock&new_or_used=N&v=1
    ```
    → precio mínimo/mín. a la venta ahora mismo.
- Aporta: `precio_estimado_reventa` real, `reventa_verificada=true`, y **métrica
  de liquidez** (ventas recientes).

### 2.2 Brickset — PVP oficial y fechas de retirada *(ya integrado)*
- `brickset.py` ya obtiene `LEGOCom` (PVP en euros) y `exitDate`.
- Mejora prevista: ampliar cobertura consultando **por tema + año** (ya lo hace
  `calendario.py`) para *descubrir* sets que se retiran, no solo enriquecer los
  que ya están en la watchlist.

### 2.3 LEGO.com — descartado (403)
- Se probó el acceso al producto (`/api/product/v3/sets/...` y páginas de
  producto) el 2026-11-20: responde **HTTP 403** por protección anti-bot.
- Implementar `RETIRING_SOON` ahí exigiría scraping frágil, justo lo que el
  proyecto decidió evitar. **Descartado como fuente.**
- La cobertura EoL se cubre ampliando Brickset, que es oficial y accesible.

### 2.4 Fuentes de ofertas con precio real *(compra "hoy")*
- Reactivar Reddit r/legodeals (el 429 ya se sortea con `curl`).
- Añadir deal-sites que publiquen importe; descartar los que solo citan PVP de
  lanzamiento (anti-MSRP ya lo filtra).

---

## 3. Diseño propuesto

### 3.1 Nuevo módulo `mercado.py`
Contiene el cliente de BrickLink, simétrico a `BricksetClient`:

```
class BrickLinkClient:
    def __init__(self, consumer_key, consumer_secret, token, token_secret,
                 *, timeout=20, get=None)   # `get` = seam para tests
    @classmethod
    def desde_entorno(cls)
    @property
    def activo(self) -> bool
    def precio_venta(self, ref) -> dict | None   # sold: avg/min/max/qty_sold
    def precio_compra(self, ref) -> dict | None  # stock: min_actual/qty
```

Firma OAuth 1.0 (HMAC-SHA1) en una función pura `_firmar(...)` para poder
testearla sin red.

### 3.2 Integración en el flujo (`main.py`)
1. En `ejecutar`, tras `cargar_watchlist` y el enriquecimiento con Brickset:
   - Para cada set de la watchlist (y de los candidatos EoL descubiertos),
     llamar `BrickLinkClient.precio_venta(ref)`.
   - Si responde: sobrescribir `precio_estimado_reventa = avg_price`,
     añadir `liquidez` (ventas recientes) y marcar `reventa_verificada = True`.
2. El filtro financiero y el scoring **no cambian**: ya consumen esos campos,
   así que el ROI pasa a ser real sin tocar lógica.
3. Nuevo filtro opcional de **liquidez** (descartar sets con pocas ventas
   reales recientes) en `evaluar_oportunidades`.

### 3.3 Descubrimiento autónomo de candidatos
- `calendario.py` amplía temas/años y, con Brickset, produce la lista de sets
  con `exitDate` dentro del horizonte.
- Esos sets se cruzan con liquidez de BrickLink; los que **se retiran + se
  venden bien + se compran hoy bajo PVP** entran automáticamente a la watchlist
  de salida, sin intervención manual.

### 3.4 Caché y límites
- Cachear por referencia (ya existe el patrón `_cache` en `BricksetClient`).
- Pausa entre peticiones y reintento con backoff para no ser bloqueado por
  BrickLink (límites estrictos).

---

## 4. Fases de implementación (con TDD)

### Fase A — `BrickLinkClient` (núcleo)
- Firma OAuth 1.0 (`_firmar`) con tests unitarios.
- `precio_venta`/`precio_compra` con HTTP simulado (`responses` o `get=`).
- Sin credenciales → `activo=False` y métodos devuelven `None` (nunca rompe el
  escaneo, igual que Brickset).

**Criterio:** `precio_venta("75192")` devuelve `{avg, min, max, qty_sold}` con
test verde, y sin red.

### Fase B — Integración en `ejecutar` / `enriquecer_watchlist`
- Inyectar precios reales y `reventa_verificada=True`.
- Test de integración: watchlist mínima + BrickLink simulado → el JSON de salida
  lleva `reventa_verificada: true` y ROI calculado sobre `avg_price`.

**Criterio:** el ROI del JSON es real (campo verificado a `true`).

### Fase C — Cobertura EoL (Brickset ampliado) ✅
- `CALENDARIO_EOL` ampliado a **12 temas × 4 años (2022-2025)**.
- Nuevo `max_consultas` para no agotar la cuota gratuita de Brickset
  (~100/día): 28 sets de watchlist + 48 del calendario = ~76.
- `construir_calendario` respeta el presupuesto y avisa de cobertura parcial.
- LEGO.com descartado por 403 (ver 2.3).

**Criterio:** se descubren sets con retirada próxima aunque no estén en la
watchlist, sin superar la cuota de Brickset.

### Fase D — Filtro de liquidez
- Descartar sets con `qty_sold` por debajo de un umbral configurable.
- Test de `evaluar_oportunidades` con y sin liquidez.

**Criterio:** un set con ROI alto pero sin ventas reales se descarta.

### Fase E — Precio de compra "hoy" y fuentes de ofertas
- `precio_compra` de BrickLink (stock) para detectar si hoy está bajo PVP.
- Reforzar feeds de ofertas con importe real.

**Criterio:** la app detecta sola "comprar hoy a X por debajo del mercado".

---

## 5. Mapa de cambios por fichero

| Fichero | Cambio |
|---|---|
| `mercado.py` *(nuevo)* | `BrickLinkClient`, firma OAuth, `precio_venta`/`precio_compra` |
| `main.py` | Integración en `ejecutar`; filtro de liquidez; reventa verificada |
| `config.py` | `BRICKLINK_*` (config/señal), umbral de liquidez, ampliar `CALENDARIO_EOL` |
| `calendario.py` | Ampliar cobertura tema/año |
| `brickset.py` | Sin cambios (o añadir helper de disponibilidad LEGO.com si se integra aquí) |
| `.env.example` | Documentar `BRICKLINK_CONSUMER_KEY/SECRET`, `BRICKLINK_TOKEN/SECRET` |
| `requirements*.txt` | `requests-oauthlib` (o firma OAuth propia para no sumar dependencias) |
| `tests/` | `test_mercado.py` (+ integración) |
| `README.md` | Documentar configuración y fuentes |

---

## 6. Riesgos y mitigación

| Riesgo | Mitigación |
|---|---|
| BrickLink exige cuenta verificada + OAuth y limita peticiones | Caché por referencia, pausa entre llamadas, backoff, `activo=False` sin credenciales |
| OAuth 1.0 es engorroso | Firmar con HMAC-SHA1 propio (función pura testeable) y **sin** dependencias extra |
| Cobertura EoL parcial de Brickset | Reforzar con `RETIRING_SOON` de LEGO.com y consulta por tema/año |
| "Subirá de valor" no es garantía | La app reporta *precio realizado + liquidez + retirada*; la revalorización futura se marca como señal, no como certeza |
| Fuentes de ofertas inestables (Reddit 429, Chollometro caído) | Combinar con precio `stock` de BrickLink (no depende de feeds) |
| Brickeconomy sin API pública estable | Descartada como fuente primaria; solo BrickLink |

---

## 7. Criterios de éxito (KPIs)

- [ ] `BrickLinkClient.precio_venta` devuelve datos reales con tests offline.
- [ ] El JSON de salida marca `reventa_verificada: true` para sets con datos.
- [ ] El calendario EoL descubre sets no presentes en la watchlist.
- [ ] Un set con ROI alto pero sin liquidez se descarta.
- [ ] Sin `BRICKLINK_*` configurado, la app funciona exactamente como hoy
      (comportamiento degradado, no roto).
- [ ] Cobertura de tests ≥ 80 % mantenida tras los cambios.

---

## 8. Orden recomendado y próximos pasos

1. ✅ **Fase A** (BrickLink + firma OAuth) — convierte el ROI en real.
2. ✅ **Fase B** (integración + `reventa_verificada`).
3. ✅ **Fase C** (cobertura EoL vía Brickset + presupuesto).
4. ✅ **Fase D** (filtro de liquidez) — usa `bricklink.unidades`.
5. ✅ **Fase E** (precio de compra hoy) — origen `mercado_stock`.

> **Feeds de ofertas:** reforzarlos es una tarea de **datos** (añadir fuentes
> que publiquen importe real), no de código. El transporte de Reddit ya se
> resuelve con `curl` y el anti-MSRP descarta anuncios a precio oficial.

Cada fase se entrega con tests en verde y merge protegido por el CI existente
(`test.yml`, check `test`). La Fase A es el punto de entrada: pequeña, aislada y
de alto valor, siguiendo el patrón TDD ya establecido en el proyecto.
