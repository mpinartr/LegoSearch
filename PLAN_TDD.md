# Plan de migración a Test-Driven Development (TDD)

> **Objetivo:** poder comprobar que todo funciona bien y que ninguna
> actualización de código rompe el comportamiento existente, antes de
> desplegar.

Este documento es la hoja de ruta para pasar de un proyecto **sin tests** a un
flujo de trabajo guiado por pruebas. No es un cambio "big-bang": se hace por
fases, empezando por *congelar* el comportamiento actual y terminando con TDD
estricto (red → verde → refactor) para todo cambio nuevo.

---

## 0. Estado de ejecución

| Fase | Estado | Evidencia |
|---|---|---|
| **0 — Infraestructura** | ✅ Completada | `requirements-dev.txt`, `pyproject.toml`, `tests/conftest.py`, `.github/workflows/test.yml` |
| **1 — Caracterización** | ✅ Completada | 136 tests iniciales sobre toda la lógica pura |
| **2 — Aislamiento de E/S** | ✅ Completada | Seams de reloj (`ahora`, `hoy`) y HTTP (`get=`, `descargar_feed_fn`); red simulada con `responses` |
| **3 — TDD en bucle** | 🔄 Permanente | Aplicado ya a los cambios de las Fases 2–5; es el flujo obligatorio para todo cambio futuro |
| **4 — CI con gates** | ✅ Completada | `test.yml` con `--cov-fail-under=80` + **branch protection activa** en `main` (check `test`) |
| **5 — Cobertura** | ✅ Completada | **201 tests** · cobertura global **93 %** · `main.py` **93 %** |

> Lo que queda sin cubrir son ramas de error poco frecuentes (fallos de red
> inesperados en la publicación, entradas malformadas). No es deuda crítica:
> lo importante (lógica de negocio y flujos de E/S) está cubierto.

---

## 1. Diagnóstico del estado actual

| Aspecto | Situación |
|---|---|
| Tests | **0**. No hay `tests/`, `conftest.py`, `pyproject.toml` ni `pytest.ini`. |
| CI | `.github/workflows/monitor.yml` solo escanea; **no ejecuta tests**. |
| `main.py` | ~1.980 líneas. Mezcla lógica pura con E/S (red, `curl`, ficheros, reloj). |
| `config.py` | Datos puros. Se importa como `import config as cfg` (estado global por módulo). |
| `brickset.py` / `calendario.py` | Llaman a `requests.get` directamente (sin sesión inyectable). |
| Fixtures | Ya existe `entradas_simuladas()` (modo `--simulate`): es una fábrica de datos reutilizable. |
| Reloj | `datetime.now()` / `date.today()` llamados en línea (difíciles de "congelar"). |

**Conclusión:** el riesgo no es la falta de *ganas* de testear, sino que la
lógica está acoplada a la E/S. Por eso el plan separa la lógica de la E/S
**antes** de pedir TDD estricto.

---

## 2. Principios rectores

1. **Primero seguridad, luego velocidad.** Antes de refactorizar, se escribe una
   red de tests de *caracterización* que fija el comportamiento actual
   (técnica de "Working Effectively with Legacy Code").
2. **Testear el dominio, no la red.** La mayor parte de la lógica es pura
   (regex, finanzas, scoring): es la de mayor valor y la más barata de testear.
3. **Inyectar dependencias, no mockear a lo bruto.** Reloj, sesión HTTP y
   rutas se pasan como parámetros con valores por defecto (seams).
4. **Los tests no tocan la red.** Todo test corre offline y determinista.
5. **TDD estricto solo para código nuevo/cambiado.** No se reescribe todo con
   TDD; se aplica a cada cambio que toque una función.

---

## 3. Fases del plan

### Fase 0 — Infraestructura de testing *(1 sesión)*

Entregables:

- `requirements-dev.txt` con `pytest`, `pytest-cov`, `freezegun` (congelar el
  reloj) y `responses` (simular HTTP sin red).
- `pyproject.toml` (o `pytest.ini`) con la configuración de pytest:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-ra --strict-markers"
```

- `tests/conftest.py` con fixtures reutilizables:
  - `watchlist` (subconjunto mínimo de `cfg.WATCHLIST` para no depender de
    todos los sets).
  - `entradas` (usa `entradas_simuladas()`).
- Un workflow de CI `test.yml` que corra los tests en cada `push` y `pull_request`.

**Criterio de aceptación:** `pytest` ejecuta y pasa (aunque aún haya pocos
tests), y el CI falla si un test falla.

### Fase 1 — Tests de caracterización (red de seguridad) *(1-2 sesiones)*

Para cada función pura, se escribe un test que **documenta el comportamiento
actual** (sin juzgar si es correcto). Así, cualquier refactor posterior que
cambie el resultado romperá el test y nos avisará.

Prioridad de cobertura (de mayor a menor riesgo de regresión):

1. Extracción y parsing: `normalizar`, `_parse_numero_eur`, `extraer_precios`,
   `extraer_descuento_pct`, `extraer_referencias`, `parse_fecha`.
2. Motor financiero: `calcular_financiero`.
3. Watchlist: `_validar_set`, `cargar_watchlist`, `detectar_referencias_meta`,
   `confianza_cruce`, `elegir_precio_compra`.
4. Scoring: `evaluar_coleccionista`, `evaluar_retirada`, `calcular_score`.
5. Serialización/exportación: `serializar_candidate`, `estimar_reventa`,
   `referencia_huella`, `normalize_url`, `calcular_suelo_historico`,
   `_ordenar_y_ranquear`, `actualizar_historico`.
6. Formato: `tabla_oportunidades`, `escribir_csv`, `escribir_reporte_md`
   (verificando contenido de texto, no el fichero en disco).
7. `brickset._normalizar`, `brickset._pvp_europeo`, `brickset._estado_desde_fecha`,
   `calendario._normalizar_eol`, `calendario.tabla_calendario`.

**Criterio de aceptación:** hay una cobertura inicial > 60 % en las funciones
puras y el equipo confía en la red de seguridad para tocar código.

### Fase 2 — Aislar E/S (seams) *(1 sesión)*

Cambios **mecánicos y seguros** (protegidos por la Fase 1):

- **Reloj:** convertir `datetime.now(...)` y `date.today()` en parámetros con
  valor por defecto, p. ej. `def dias_desde(fecha, *, ahora=None)` y
  `def evaluar_retirada(set_, *, hoy=None)`. Alternativa: usar `freezegun`.
- **HTTP en `brickset.py` / `calendario.py`:** permitir inyectar una función
  `get=` (por defecto `requests.get`) para que los tests la reemplacen por
  `responses` o un doble.
- **Sesión RSS:** `rastrear_feeds` ya recibe `sesion` internamente; aislar
  `descargar_feed`/`_descargar_bytes` detrás de un parámetro inyectable.
- **Ficheros:** las funciones de exportación ya reciben `ruta: Path`; los
  tests usan `tmp_path` (fixture de pytest) en lugar del disco real.

**Criterio de aceptación:** toda función de dominio es testeable sin red y con
reloj controlado.

### Fase 3 — TDD en bucle para cambios nuevos *(permanente)*

A partir de aquí, **toda modificación de una función** sigue el ciclo:

1. **Rojo:** escribir el test que describe el comportamiento deseado. Fallará.
2. **Verde:** implementar lo mínimo para que pase.
3. **Refactor:** limpiar sin cambiar comportamiento (los tests siguen en verde).

Reglas de equipo:

- No se acepta un cambio de lógica sin su test.
- Si se corrige un bug, primero se escribe un test de regresión que lo
  reproduzca, luego se corrige.
- Los tests deben ser **rápidos** (< 1 s por módulo) y **deterministas**
  (sin depender de la fecha del sistema ni de la red).

**Criterio de aceptación:** en cada PR nuevo, el diff incluye tests.

### Fase 4 — Integración continua y "gates" *(1 sesión)*

- Nuevo workflow `.github/workflows/test.yml` (independiente de `monitor.yml`):

```yaml
name: test
on:
  push:
  pull_request:
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: actions/setup-python@v7
        with:
          python-version: "3.11"
          cache: pip
      - name: Install
        run: |
          pip install -r requirements.txt
          pip install -r requirements-dev.txt
      - name: Run tests
        run: pytest --cov=. --cov-report=term-missing --cov-fail-under=60
```

- Bloquear el merge de `main` si los tests fallan (branch protection).
- El `monitor.yml` puede añadir un paso previo que corra los tests antes de
  escanear (opcional, evita desplegar código roto).

**Criterio de aceptación:** ningún merge a `main` con tests en rojo.

### Fase 5 — Cobertura y calidad *(continuo)*

- Umbral de cobertura progresivo: 60 % → 70 % → 80 % en lógica de dominio.
- El informe de cobertura (`--cov-report=term-missing`) identifica ramas sin
  testear para priorizar.
- Revisión periódica de los "desechos" que la cobertura delata (ramas muertas).

---

## 4. Mapa de qué probar (priorizado)

| Módulo | Función | Tipo | Prioridad | Dificultad |
|---|---|---|---|---|
| main | `normalizar` | unit | Alta | Baja |
| main | `_parse_numero_eur` | unit | Alta | Baja |
| main | `extraer_precios` | unit | Alta | Baja |
| main | `extraer_descuento_pct` | unit | Alta | Baja |
| main | `extraer_referencias` | unit | Alta | Baja |
| main | `parse_fecha` | unit | Media | Baja |
| main | `calcular_financiero` | unit | Alta | Baja |
| main | `_validar_set` / `cargar_watchlist` | unit | Alta | Baja |
| main | `evaluar_coleccionista` | unit | Media | Baja |
| main | `evaluar_retirada` | unit | Alta | Media (reloj) |
| main | `calcular_score` | unit | Media | Baja |
| main | `elegir_precio_compra` | unit | Alta | Baja |
| main | `detectar_referencias_meta` | unit | Alta | Baja |
| main | `confianza_cruce` | unit | Media | Baja |
| main | `serializar_candidate` | unit | Alta | Media |
| main | `estimar_reventa` | unit | Media | Baja |
| main | `actualizar_historico` | unit | Alta | Media |
| main | `calcular_suelo_historico` / `_ordenar_y_ranquear` | unit | Media | Baja |
| main | `escribir_csv` / `escribir_reporte_md` / `tabla_oportunidades` | unit (texto) | Media | Baja |
| main | `rastrear_feeds` / `descargar_feed` | integración (doble) | Media | Alta |
| brickset | `_normalizar` / `_pvp_europeo` / `_estado_desde_fecha` | unit | Media | Baja |
| calendario | `_normalizar_eol` / `tabla_calendario` | unit | Media | Baja |
| publicacion | `_cabeceras` / `_url_contenido` / `_marca` | unit | Baja | Baja |

---

## 5. Ejemplos concretos de tests (a escribir en Fase 1)

```python
# tests/test_extraccion.py
from main import normalizar, _parse_numero_eur, extraer_precios, extraer_referencias

def test_normalizar_quita_html_y_acentos():
    assert normalizar("<p>Millennium Fálcon</p>") == "millennium falcon"

def test_parse_numero_eur_decimal_espanol():
    assert _parse_numero_eur("599,99") == 599.99

def test_parse_numero_eur_miles_con_punto():
    assert _parse_numero_eur("1.499") == 1499.0

def test_extraer_precios_clasifica_oferta():
    precios = extraer_precios("solo 449,99 €")
    assert len(precios) == 1
    assert precios[0].valor == 449.99
    assert precios[0].etiqueta == "oferta"

def test_extraer_referencias_descarta_anios():
    assert "1999" not in extraer_referencias("oferta lego del año 1999")
    assert "75192" in extraer_referencias("lego 75192 falcon")
```

```python
# tests/test_financiero.py
from main import calcular_financiero

def test_calcular_financiero_basico():
    f = calcular_financiero(precio_compra=600.0, precio_venta=1150.0)
    # gastos plataforma 10% = 115, envío 5% = 57.5
    assert f.gastos_plataforma == 115.0
    assert f.gastos_envio == 57.5
    assert f.coste_total == 772.5
    assert f.beneficio_neto == 377.5
```

```python
# tests/test_retirada.py  (congelando la fecha)
from freezegun import freeze_time
from main import evaluar_retirada

@freeze_time("2026-11-16")
def test_retirada_inminente():
    score, _ = evaluar_retirada({"exit_date": "2026-12-31", "estado_eol": ""})
    assert score == 2
```

---

## 6. Estrategia para dependencias difíciles

| Dependencia | Técnica |
|---|---|
| **Reloj** (`datetime.now`, `date.today`) | Parámetro `now`/`hoy` con default, o `freezegun`. |
| **Red** (`requests.get` en brickset/calendario) | Parámetro `get=` inyectable + `responses`. |
| **curl** (`subprocess` en `_descargar_bytes`) | Mock de `subprocess.run` o extraer a función. |
| **Sesión RSS** (`requests.Session`) | `responses` o inyectar `descargar_feed`. |
| **Ficheros** | Fixture `tmp_path` de pytest (nunca el disco real). |
| **Config** (`import config as cfg`) | `monkeypatch.setitem(cfg.FINANZAS, ...)` por test. |
| **Fixtures de datos** | Reutilizar `entradas_simuladas()`. |

---

## 7. Estructura de carpetas propuesta

```
LegoSearch/
├── main.py
├── config.py
├── brickset.py
├── calendario.py
├── publicacion.py
├── requirements.txt          # dependencias de ejecución (sin cambios)
├── requirements-dev.txt      # pytest, pytest-cov, freezegun, responses  ← nuevo
├── pyproject.toml            # configuración de pytest                    ← nuevo
├── tests/                    # ← nuevo
│   ├── conftest.py           # fixtures compartidos
│   ├── test_extraccion.py
│   ├── test_financiero.py
│   ├── test_watchlist.py
│   ├── test_scoring.py
│   ├── test_serializacion.py
│   ├── test_brickset.py
│   ├── test_calendario.py
│   └── test_publicacion.py
└── .github/workflows/
    ├── monitor.yml           # escaneo (existente)
    └── test.yml              # CI de tests  ← nuevo
```

---

## 8. Roadmap temporal (orden recomendado)

1. **Fase 0** — `requirements-dev.txt`, `pyproject.toml`, `tests/conftest.py`,
   workflow `test.yml`.
2. **Fase 1** — tests de caracterización, empezando por `extraccion` y
   `financiero` (el núcleo del valor), luego `watchlist`, `scoring`,
   `serializacion`, `brickset`, `calendario`.
3. **Fase 2** — seams de reloj y HTTP (protegidos por la Fase 1).
4. **Fase 4** — activar branch protection y `--cov-fail-under`.
5. **Fase 3 + 5** — TDD en cada cambio nuevo y subida progresiva de cobertura.

---

## 9. Riesgos y mitigación

| Riesgo | Mitigación |
|---|---|
| Tests de caracterización que "congelan" un bug | Escribir los tests **describiendo** el comportamiento, no el valor exacto, cuando sea ambiguo; documentar los casos dudosos. |
| Mockear demasiado y testear el mock en lugar de la lógica | Limitar los dobles a las fronteras de E/S (red, reloj, ficheros); probar siempre la lógica pura directamente. |
| Tests frágiles por el reloj | Usar siempre `freezegun` o parámetros `now`/`hoy`. |
| Romper el flujo actual de `monitor.yml` | `test.yml` es independiente; el escaneo no depende de los tests hasta que se decida. |
| Refactor prematuro del monolito | **No** dividir `main.py` en módulos hasta que la Fase 1 dé seguridad; primero tests, luego extracción. |

---

## 10. Criterios de éxito (KPIs)

- [x] `pytest` pasa localmente en < 10 s con toda la suite (**~1 s**).
- [x] Cobertura de la lógica de dominio ≥ 80 % (**93 %**).
- [x] `test.yml` corre en cada PR; el umbral de cobertura falla el job.
- [x] Bloquear merges en rojo con **branch protection** (activa en `main`, check `test`).
- [x] Todo cambio nuevo de lógica viene con su test (red → verde → refactor).
- [x] Los tests no requieren red ni son sensibles a la fecha del sistema.

---

## 11. Estado final

Todas las fases están implementadas y la protección de rama está **activa** en
`main`: exige que pase el check `test`, prohíbe force-push y borrado, y no
requiere revisiones (trabajo en solitario).

Verificación aplicada:

- `required_status_checks`: `strict: true`, `contexts: ["test"]`
- `allow_force_pushes`: `false`
- `allow_deletions`: `false`
- `enforce_admins`: `false` (los administradores pueden saltárselo)

Si algún día hay que reaplicarla o moverla a otra rama:

```bash
GITHUB_ADMIN_TOKEN=ghp_xxx GITHUB_BRANCH=main ./scripts/proteger_rama.sh
```

Alternativa manual: *Settings → Branches → Add branch ruleset*, marcar
`Require status checks to pass` y seleccionar el job **test**.
