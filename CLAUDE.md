# CLAUDE.md

Guía técnica para trabajar en este repositorio.

## Propósito

Monitor automatizado de noticias del sector de bebidas de consumo masivo. Recolecta artículos de fuentes Local (Argentina), Regional (LATAM) y Mundial, los filtra por relevancia industrial, los rankea y genera un dashboard HTML estático (`index.html`) listo para servir desde GitHub Pages.

Está pensado para lectura rápida diaria del equipo: visión de actualidad e innovación del mercado y empresas competidoras, con foco en lo que efectivamente afecta el negocio.

No hay backend ni base de datos. El estado operativo vive en archivos versionados:

- `config/*.json`: fuentes, empresas y keywords.
- `published_urls.json`: deduplicación rolling (7 días) de URLs y títulos publicados.
- `data/*.json`: intermedios y diagnósticos por corrida (no commiteado). Incluye `candidates.json`, `accepted_candidates.json`, `articles.json` y `diagnostics.json`.
- `index.html`: salida web generada.

## Volumen esperado

La salida razonable está entre **25 y 60 notas por corrida** según la actividad real del día. No se rellena para alcanzar un mínimo: si hay menos contenido relevante, se publican menos notas. `--target-count` (default 50) es solo un tope superior.

## Entrada principal

```powershell
python main.py
```

Argumentos:

- `--output`: ruta del HTML generado. Default: `index.html`.
- `--limit`: máximo de candidatos que se intentan extraer. Default: `120`.
- `--target-count`: tope superior flexible. Default: `50`.
- `--min-per-region`: piso por Local/Regional/Mundial cuando hay calidad. Default: `4`.
- `--max-search-queries`: límite de búsquedas globales por empresa en Google News. Default: `90`.
- `--no-search`: desactiva Google News RSS.
- `--min-body-length`: mínimo de caracteres de body para publicar. Default: `80`.
- `--debug`: logging debug.

## Pipeline

Orquestación en [beverage_news/pipeline.py](beverage_news/pipeline.py):

1. Carga `sources`, `companies` y `keywords`.
2. `discover_candidates` recolecta candidatos.
3. Escribe `data/candidates.json`.
4. `filter_candidates` rechaza ruido, viejos, duplicados, off-topic, etc.
5. `rank_items` calcula score por candidato.
6. `build_extraction_queue` arma cola balanceada por región.
7. Extrae artículos en paralelo con `extract_article_item`.
8. `validate_articles` filtra paywall / títulos genéricos / baja relevancia post-extracción.
9. `select_balanced_articles` aplica cuota regional y `target-count`.
10. Escribe `data/articles.json` y `data/diagnostics.json`.
11. Actualiza `published_urls.json`.
12. `generate_web` produce `index.html`.

## Módulos clave

### `discovery.py`

Canales:

- RSS explícitos declarados en `sources.json`.
- RSS detectados desde `<link rel="alternate">`.
- Secciones HTML configuradas (recorre `<a>`).
- Google News RSS con queries globales, locales y regionales en español y portugués/BR.

Para Google News se parsea el HTML del item RSS y se prefiere el primer enlace al medio original antes que el redirect `news.google.com`.

### `filtering.py`

Filtra antes de gastar extracción:

- Deduplicación por URL y título normalizados.
- Rechazo de URLs ya publicadas (rolling 7 días).
- Rechazo de paths de login, registro, newsletter, suscripción.
- **Ventana de antigüedad 36 h** uniforme para Local, Regional y Mundial.
- Límite por fuente para evitar que una sola fuente domine la cola.
- **Rechazo por rubro off-topic**: notas de repuestos/autopartes, ferretería, electrodomésticos, indumentaria, farmacia, inmobiliaria, tecnología de consumo, deportes/espectáculos, cripto, etc. que disparan keywords genéricas (mayorista, distribución, marca) son descartadas si no hay match de empresa ni fuente trade.
- **Rechazo de títulos histórico/clickbait**: "¿Sabías que…?", "La historia de…", "El origen de…", "Un día como hoy", "Así nació…" son rechazados salvo en fuentes trade.
- Aceptación por: match de empresa con contexto de industria/canal; doble hit de keyword no genérica con contexto de bebidas + intención de industria; título suficientemente específico; fuente trade + keyword no genérica; sección local/regional con contexto de bebidas + negocio; sección local/regional con contexto de canal de distribución.
- Rechazo de contextos de baja utilidad (salud, recetas, clima, lifestyle) cuando no hay señales fuertes de industria, canal ni categoría estratégica.

El diagnóstico devuelve `by_source`, motivos de descarte y motivos de aceptación.

### `ranking.py`

Score por: fuente, recencia, tópicos, empresas, prioridad estratégica, motivo de aceptación. Penaliza redirects de `news.google.com` que quedaron sin resolver.

`build_extraction_queue` reserva intentos por región antes de completar por score para que los candidatos regionales no queden tapados por fuentes globales más prolíficas.

### `extraction.py`

- Resuelve URL final, incluyendo casos donde todavía llega `news.google.com`.
- Lee JSON-LD para título, resumen, fecha y body.
- OpenGraph/Twitter como fallback.
- Recolecta párrafos dentro de `<article>`, `<main>` o el documento.
- Usa el summary del candidato como fallback si el body no alcanza el mínimo.
- Body recortado a `MAX_BODY_CHARS = 12000` con remoción de boilerplate.

### `validation.py`

Post-extracción rechaza:

- Título vacío, corto, demasiado largo, genérico o truncado.
- **Antigüedad > 36 h** medida sobre la fecha real (`published`) extraída de JSON-LD/OG. Cubre candidatos descubiertos desde listings de sección sin fecha visible que pasaban el filtro temporal en `filtering.py` con `published=""`.
- **`missing_publish_date`**: si la extracción no devuelve fecha, se descarta. Sin fecha no se puede garantizar frescura.
- Body de paywall, login o newsletter.
- Body repetitivo (scraping fallido).
- Falta de relevancia industrial sobre título + resumen + body.

Permite publicar notas con empresa o fuente trade; exige señales de negocio/industria para notas generalistas.

### `web.py`

HTML estático con: búsqueda client-side, filtros por cobertura y tópico, agrupación por tópico principal, bloque **Destacados de hoy** (top 5), enlaces a original y traducción, body expandible. No editar `index.html` a mano.

### Utilidades

- `http.py`: cliente HTTP con headers, timeouts y manejo de redirects.
- `text.py`: limpieza, normalización accent-insensitive y matching de keywords/empresas.
- `urls.py`: normalización de URLs, remoción de tracking params, resolución de redirects de Google News.
- `models.py`: dataclasses (`Candidate`, `Article`, `Source`, etc.).
- `config.py`: carga y validación de los JSON de configuración.

### `publish_github.py`

CLI auxiliar para publicar `index.html` a un repo de GitHub vía Contents API cuando no se usa el workflow. Lee `GITHUB_TOKEN` desde `.env`.

## Configuración

### `config/sources.json`

Campos: `name`, `url`, `rss`, `sections`, `country`, `region` (`Local`/`Regional`/`Mundial`), `language`, `trade`, `require_section` (opcional).

### `config/companies.json`

Campos: `name`, `country`, `segments`, `aliases`. Los aliases son críticos para Brasil/LATAM (nombres legales, marcas, embotelladoras, traducciones, variantes sin acentos).

### `config/keywords.json`

Diccionario `categoría → términos`. Matching accent-insensitive con límites de palabra.

## Diagnóstico operativo

Después de cada corrida revisar:

- `discovery.source_errors` / `section_errors`: fuentes rotas o bloqueadas.
- `filtering.discarded`: filtros demasiado estrictos o candidatos basura.
- `filtering.by_source`: fuentes que aportan ruido o señal.
- `queue.queued_by_region`: distribución pre-extracción.
- `extraction.by_source` / `by_region`: extractabilidad real.
- `validation.by_reason`: paywalls, títulos genéricos, baja relevancia.
- `selection.selected_by_region`: balance final.

## Automatización

GitHub Actions ejecuta [.github/workflows/update-news.yml](.github/workflows/update-news.yml) **6 veces por día** (06, 08, 11, 14, 17 y 20 ART). Cada corrida instala dependencias, corre `python main.py`, commitea `index.html` + `published_urls.json` si hubo cambios y hace `git push`. La salida queda servida desde GitHub Pages.

## Tests

```powershell
python -m unittest discover -s tests -v
```

Agregar tests cuando se modifiquen: reglas de aceptación/rechazo, keywords/aliases ambiguos, resolución de Google News, cola regional, extractores, validación post-extracción.

## Convenciones

- No editar `index.html` a mano.
- No commitear `data/`.
- UTF-8 en todo.
- No hardcodear tokens.
- Limpiar `__pycache__` después de pruebas si quedan.
- Preservar diagnósticos: son la herramienta principal para mejorar cobertura y calidad.
