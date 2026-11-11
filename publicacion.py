"""Publicación del resultado del escaneo en la rama ``runtime-data`` de GitHub.

Por qué existe
--------------
El superagente de Telegram lee **siempre** de:

    https://raw.githubusercontent.com/<owner>/<repo>/runtime-data/opportunities.json

El worker de Railway escribe en un volumen privado (``/app/data``) que el bot
no puede leer. Sin este módulo, Railway acumularía histórico que nadie ve y el
bot se quedaría con datos de GitHub Actions.

Contrato de diseño
------------------
La publicación es **best-effort y nunca letal**: si falta el token, la red
falla o GitHub rechaza el push, se registra un AVISO y el escaneo se da por
bueno. El JSON ya está escrito en el volumen, así que no se pierde nada; el
siguiente ciclo vuelve a intentarlo. Eso mantiene la promesa del worker de no
tumbarse por errores transitorios.

Configuración (variables de entorno)
------------------------------------
``GITHUB_PUBLISH_TOKEN``
    Token con permiso de escritura de contenido sobre el repo. Es el único
    secreto necesario. Si no está, el módulo queda inactivo.
``GITHUB_PUBLISH_REPO``
    ``owner/repo``. Por defecto ``mpinartr/LegoSearch``.
``GITHUB_PUBLISH_BRANCH``
    Rama destino. Por defecto ``runtime-data``.
"""

from __future__ import annotations

import base64
import json
import logging
import os
from pathlib import Path
from typing import Any

import requests

log = logging.getLogger(__name__)

API_URL = "https://api.github.com"
DEFAULT_REPO = "mpinartr/LegoSearch"
DEFAULT_BRANCH = "runtime-data"
# Mensaje de commit del superagente: identifica el origen del escaneo.
COMMIT_PREFIX = "chore(worker)"


def configuracion() -> dict[str, str] | None:
    """Lee la configuración del entorno. ``None`` si no hay token."""
    token = (os.environ.get("GITHUB_PUBLISH_TOKEN") or "").strip()
    if not token:
        return None
    return {
        "token": token,
        "repo": (os.environ.get("GITHUB_PUBLISH_REPO") or DEFAULT_REPO).strip(),
        "branch": (os.environ.get("GITHUB_PUBLISH_BRANCH") or DEFAULT_BRANCH).strip(),
    }


def _cabeceras(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "LegoSearch-worker",
    }


def _url_contenido(repo: str, ruta: str) -> str:
    return f"{API_URL}/repos/{repo}/contents/{ruta}"


def _sha_actual(repo: str, ruta: str, rama: str, token: str,
                *, timeout: int) -> tuple[str | None, bool]:
    """Devuelve ``(sha, ok)``.

    ``sha`` es el del fichero en la rama, o ``None`` si aún no existe.
    ``ok`` es ``False`` solo cuando la consulta falló de verdad (credenciales
    inválidas, error del servidor). Distinguirlo evita un segundo intento
    inútil de escritura cuando el token ya sabemos que no sirve.

    La API de contenidos exige el SHA para sobrescribir un fichero. Sin él,
    GitHub responde 422 ("sha wasn't supplied").
    """
    try:
        respuesta = requests.get(
            _url_contenido(repo, ruta),
            params={"ref": rama},
            headers=_cabeceras(token),
            timeout=timeout,
        )
    except requests.RequestException as exc:
        log.warning("GitHub: no se pudo consultar el SHA de %s (%s)", ruta, exc)
        return None, False

    if respuesta.status_code == 404:
        return None, True
    if respuesta.status_code >= 400:
        log.warning("GitHub: consulta de SHA devolvió %d (%s)",
                    respuesta.status_code, respuesta.text[:200])
        return None, False

    try:
        return respuesta.json().get("sha"), True
    except ValueError:
        return None, False


def _subir_fichero(cfg: dict[str, str], ruta: str, contenido: bytes,
                   mensaje: str, *, timeout: int) -> bool:
    """Crea o actualiza un fichero en la rama destino."""
    sha, consulta_ok = _sha_actual(cfg["repo"], ruta, cfg["branch"], cfg["token"],
                                   timeout=timeout)
    if not consulta_ok:
        # Token inválido o GitHub caído: no merece la pena intentar escribir.
        return False

    cuerpo: dict[str, Any] = {
        "message": mensaje,
        "content": base64.b64encode(contenido).decode("ascii"),
        "branch": cfg["branch"],
    }
    if sha:
        cuerpo["sha"] = sha

    try:
        respuesta = requests.put(
            _url_contenido(cfg["repo"], ruta),
            headers=_cabeceras(cfg["token"]),
            data=json.dumps(cuerpo),
            timeout=timeout,
        )
    except requests.RequestException as exc:
        log.warning("GitHub: fallo de red al subir %s (%s)", ruta, exc)
        return False

    if respuesta.status_code in (200, 201):
        accion = "actualizado" if sha else "creado"
        log.info("Publicado en %s@%s: %s (%s)", cfg["repo"], cfg["branch"], ruta, accion)
        return True

    log.warning("GitHub: %s rechazó %s con %d (%s)", cfg["repo"], ruta,
                respuesta.status_code, respuesta.text[:200])
    return False


def publicar(json_path: Path, csv_path: Path | None = None, *,
             timeout: int = 20) -> bool:
    """Publica el JSON (y el CSV si existe) en la rama ``runtime-data``.

    Devuelve ``True`` si el JSON se publicó. Nunca lanza: cualquier problema se
    registra como aviso, porque la publicación no puede tumbar el escaneo.
    """
    cfg = configuracion()
    if cfg is None:
        log.debug("GITHUB_PUBLISH_TOKEN no definido: se omite la publicación.")
        return False

    if not json_path.exists():
        log.warning("Publicación omitida: no existe %s", json_path)
        return False

    try:
        js = json_path.read_bytes()
    except OSError as exc:
        log.warning("No se pudo leer %s (%s)", json_path, exc)
        return False

    mensaje = f"{COMMIT_PREFIX}: scan {_marca(json_path)}"

    # El JSON manda: si falla, no tiene sentido publicar el CSV. Así nunca
    # queda un CSV de un escaneo distinto al JSON con el que se empareja.
    if not _subir_fichero(cfg, "opportunities.json", js, mensaje, timeout=timeout):
        return False

    if csv_path is not None and csv_path.exists():
        try:
            _subir_fichero(cfg, "oportunidades.csv", csv_path.read_bytes(),
                           mensaje, timeout=timeout)
        except OSError as exc:
            log.warning("No se pudo leer %s (%s)", csv_path, exc)

    return True


def _marca(json_path: Path) -> str:
    """Fecha del escaneo tomada del propio JSON (para el mensaje de commit)."""
    try:
        with json_path.open(encoding="utf-8") as fh:
            datos = json.load(fh)
        return str(datos.get("generado_en") or "") or "now"
    except (OSError, ValueError):
        return "now"
