# CLAUDE.md

Guía técnica para trabajar en este repositorio.

## Propósito

Monitor automatizado de noticias del sector de bebidas de consumo masivo. Recolecta artículos de fuentes Local (Argentina), Regional (LATAM) y Mundial, los filtra por relevancia industrial, los rankea y genera un dashboard HTML estático (`index.html`) listo para servir desde GitHub Pages.

Está pensado para lectura rápida diaria del equipo: visión de actualidad e innovación del mercado y empresas competidoras, con foco en lo que efectivamente afecta el negocio. Cada área de la empresa (Finanzas, Marketing, Supply Chain, Ventas) tiene su propio perfil de filtro y briefing ejecutivo diario.

No hay backend ni base de datos. El estado operativo vive en archivos versionados:

- `config/*.json`: fuentes, empresas y keywords.
- `published_urls.json`: deduplicación rolling (7 días) de URLs y títulos publicados.
- `weekly_log.json`: artículos publicados acumulados (rolling 7 días), base del resumen semanal.
- `weekly_summary.json`: resumen semanal generado por LLM (se regenera los viernes o al cruzar 4 días mínimos).
- `data/*.json`: intermedios y diagnósticos por corrida (no commiteado). Incluye `candidates.json`, `accepted_candidates.json`, `articles.json`, `diagnostics.json`, `llm_cache.json` y `google_cse_usage.json`.
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
- `--max-search-queries`: límite de búsquedas (Google CSE o Google News RSS). Default: `90`.
- `--no-search`: desactiva búsqueda activa (CSE y Google News RSS).
- `--min-body-length`: mínimo de caracteres de body para publicar. Default: `80`.
- `--production`: activa defaults de producción (limit=120, target=50, local=8, regional=8, mundial=34, queries=90).
- `--debug`: logging debug.

## Pipeline

Orquestación en [beverage_news/pipeline.py](beverage_news/pipeline.py):

1. Carga `sources`, `companies` y `keywords`.
2. `discover_candidates` recolecta candidatos (RSS + secciones + Google CSE o Google News RSS).
3. Escribe `data/candidates.json`.
4. `filter_candidates` rechaza ruido, viejos, duplicados, off-topic, etc.
5. `rank_items` calcula score por candidato.
6. `build_extraction_queue` arma cola balanceada por región.
7. Extrae artículos en paralelo con `extract_article_item`.
8. `validate_articles` filtra paywall / títulos genéricos / baja relevancia post-extracción.
9. `select_balanced_articles` aplica cuota regional y `target-count`.
10. Dedup Jaccard + dedup semántica LLM (Haiku, batch por empresa).
11. `summarize_articles` — clasifica relevancia ejecutiva y genera resumen LLM (Haiku, con caché diaria).
12. `review_dashboard` — QA editorial (Sonnet, 1 llamada).
13. `generate_area_briefings` — briefing por área (Finanzas/Marketing/Supply Chain/Ventas), Sonnet, 1 llamada.
14. Actualiza `weekly_log.json` + genera `weekly_summary.json` si hay ≥ 4 días acumulados (Sonnet, viernes).
15. Escribe `data/articles.json` y `data/diagnostics.json`.
16. Actualiza `published_urls.json`.
17. `generate_web` produce `index.html`.

## Módulos clave

### `discovery.py`

Canales:

- RSS explícitos declarados en `sources.json`.
- RSS detectados desde `<link rel="alternate">`.
- Secciones HTML configuradas (recorre `<a>`).
- **Google Custom Search API** (CSE) si `GOOGLE_API_KEY` y `GOOGLE_CSE_ID` están en `.env`. Quota guard: cap en 95 queries/día (free tier = 100), rastreado en `data/google_cse_usage.json`. Si quota agotada, cae automáticamente a Google News RSS. Ante **HTTP 403** (típicamente "Custom Search JSON API" no habilitada en el proyecto GCP de la key, key inválida o restricción referer/IP) aborta el CSE en la primera query —no reintenta las ~28— y cae a RSS; el motivo real de Google se guarda en `diagnostics.search_errors[].api_reason` / `api_message`. Habilitar la API: https://console.cloud.google.com/apis/library/customsearch.googleapis.com
- **Google News RSS** como fallback si CSE no configurado o quota agotada.

Para Google News se parsea el HTML del item RSS y se prefiere el primer enlace al medio original antes que el redirect `news.google.com`.

### `filtering.py`

Filtra antes de gastar extracción:

- Deduplicación por URL y título normalizados.
- Rechazo de URLs ya publicadas (rolling 7 días).
- Rechazo de paths de login, registro, newsletter, suscripción.
- **Ventana de antigüedad 36 h** uniforme para Local, Regional y Mundial.
- Límite por fuente para evitar que una sola fuente domine la cola.
- **Rechazo por rubro off-topic**: notas de repuestos/autopartes, ferretería, electrodomésticos, indumentaria, farmacia, inmobiliaria, tecnología de consumo, deportes/espectáculos, cripto, etc.
- **Rechazo de títulos histórico/clickbait**: "¿Sabías que…?", "La historia de…", etc., salvo en fuentes trade.
- Aceptación por: match de empresa con contexto de industria/canal; doble hit de keyword no genérica; fuente trade + keyword; sección local/regional con contexto de bebidas + negocio.
- **Deduplicación semántica temprana**: candidatos con las mismas 7 primeras palabras significativas se colapsan.

### `ranking.py`

Score por: fuente, recencia, tópicos, empresas, prioridad estratégica, motivo de aceptación. Penaliza redirects de `news.google.com` sin resolver.

`build_extraction_queue` reserva intentos por región para que candidatos regionales no queden tapados por fuentes globales prolíficas.

### `extraction.py`

- Resuelve URL final, incluyendo casos donde todavía llega `news.google.com`.
- Lee JSON-LD para título, resumen, fecha y body.
- OpenGraph/Twitter como fallback.
- Recolecta párrafos dentro de `<article>`, `<main>` o el documento.
- Usa el summary del candidato como fallback si el body no alcanza el mínimo.
- Body recortado a `MAX_BODY_CHARS = 12000` con remoción de boilerplate.
- **Timeout: 10 segundos** (subido de 7s para capturar sitios lentos).

### `validation.py`

Post-extracción rechaza:

- Título vacío, corto, demasiado largo, genérico o truncado.
- **Antigüedad > 36 h** medida sobre la fecha real extraída de JSON-LD/OG.
- **`missing_publish_date`**: sin fecha se descarta.
- Body de paywall, login o newsletter.
- Body repetitivo (scraping fallido).
- **Body histórico**: ≥2 marcadores de historia/antigüedad sin marcadores de actualidad.
- Falta de relevancia industrial.

### `llm.py`

Cinco funciones principales, todas con prompt caching:

- **`classify_and_summarize(articles)`** — Haiku 4.5. Clasifica relevancia ejecutiva y genera resumen de 3-5 oraciones. Caché local por URL en `data/llm_cache.json` (invalida al día siguiente). Rate limit: 13s entre llamadas.
- **`semantic_dedup_articles(articles)`** — Haiku 4.5. Batch único que detecta artículos del mismo evento. Agrupa por empresa (incluyendo menciones en título). Retry automático en fallo de API. **Contrato de formato (crítico):** el system prompt (`_DEDUP_SYSTEM`), el user message y el parser `_resolve` deben coincidir en el formato de salida `"grupo.posición"` (ej. `[["3.1","3.3"]]`). Si se cambia uno, cambiar los tres. Un desajuste hace que `_resolve` falle con `IndexError` (capturado y logueado) y la corrida termine con `dedup_merged=0` sin mergear nada — clusters obvios del mismo evento quedan como tarjetas separadas. El `except` loguea el `pair` que no pudo resolver.
- **`review_dashboard(articles)`** — Sonnet 4.6. QA editorial: detecta dominancia de empresa, ausencia de temas clave, baja calidad general.
- **`generate_area_briefings(articles)`** — Sonnet 4.6. Una llamada genera briefing para las 4 áreas (Finanzas, Marketing, Supply Chain, Ventas). Input: título + tópico + empresa de cada artículo.
- **`generate_weekly_summary(weekly_log)`** — Sonnet 4.6. Resumen semanal con top eventos priorizados por importancia. Solo se genera con ≥ 4 días de datos (`WEEKLY_SUMMARY_MIN_DAYS = 4`).

### `web.py`

HTML estático con:

- Búsqueda client-side y filtros por cobertura y tópico.
- **Perfiles de área** (Finanzas / Marketing / Supply Chain / Ventas): botones que activan filtro multitópico con lógica OR y muestran el briefing ejecutivo del área.
- Agrupación por tópico principal, bloque **Destacados de hoy** (top 5).
- Botón "También en: [fuente]" en artículos mergeados por dedup.
- **Resumen del período**: sección colapsable al pie. Visible solo con ≥ 4 días acumulados; antes muestra "faltan X días".
- **Sanitización de salida**: todo contenido dinámico pasa por `html.escape`. Además, los `href` (nota original, traducción, "También en") pasan por `_safe_url`, que solo admite esquemas `http`/`https` — bloquea `javascript:`, `data:`, etc. para evitar XSS almacenado en la página pública.
- No editar `index.html` a mano.

### Utilidades

- `http.py`: cliente HTTP con headers, timeouts y manejo de redirects. **Guard SSRF** (`_is_safe_url`): solo `http`/`https` y bloqueo de IPs literales privadas/loopback/link-local/reservadas (ej. `127.0.0.1`, `169.254.169.254`, `10.x`, `192.168.x`). Cap de respuesta `MAX_RESPONSE_BYTES = 5 MB` vía `Content-Length`.
- `text.py`: limpieza, normalización accent-insensitive y matching de keywords/empresas.
- `urls.py`: normalización de URLs, remoción de tracking params.
- `models.py`: dataclasses (`Candidate`, `Article`, `Source`, etc.).
- `config.py`: carga y validación de los JSON de configuración.

### `publish_github.py`

CLI auxiliar para publicar `index.html` a un repo de GitHub vía Contents API. Lee `GITHUB_TOKEN` desde `.env`.

## Configuración

### Variables de entorno (`.env`)

```
GITHUB_TOKEN=...
GITHUB_OWNER=...
GITHUB_REPO=...
ANTHROPIC_API_KEY=...
GOOGLE_API_KEY=...       # Google Custom Search API (opcional, 100 queries/día gratis)
GOOGLE_CSE_ID=...        # ID del Custom Search Engine
```

Las mismas variables deben existir como **GitHub Secrets** para que el workflow de Actions funcione.

### `config/sources.json`

Campos: `name`, `url`, `rss`, `sections`, `country`, `region` (`Local`/`Regional`/`Mundial`), `language`, `trade`, `require_section` (opcional). Actualmente **106 fuentes** (41 Mundial, 47 Local Argentina, 18 Regional LATAM).

### `config/companies.json`

Campos: `name`, `country`, `segments`, `aliases`, `requires_industry_context` (opcional). 67 empresas. `requires_industry_context: true` exige contexto fuerte de industria/negocio además del match por nombre.

### `config/keywords.json`

Diccionario `categoría → términos`. 12 categorías. Matching accent-insensitive. Incluye términos en español, inglés y portugués. Categorías estratégicas con mayor peso en ranking: `financial_results`, `ma_and_strategy`, `risk_crisis_reputation`.

## Google Custom Search API

CSE configurado con los 40 dominios de mayor prioridad (sin RSS propio o RSS débil). El plan gratuito permite 50 sitios máximo en el CSE.

Quota guard: `data/google_cse_usage.json` registra `{date, count}`. Límite operativo: 95 queries/día. Si se agota o Google devuelve 429, el pipeline cae automáticamente a Google News RSS para esa corrida. Al día siguiente el contador se resetea.

## Diagnóstico operativo

Después de cada corrida revisar `data/diagnostics.json`:

- `discovery.source_errors` / `section_errors`: fuentes rotas o bloqueadas.
- `filtering.discarded`: filtros demasiado estrictos o candidatos basura.
- `filtering.by_source`: fuentes que aportan ruido o señal.
- `queue.queued_by_region`: distribución pre-extracción.
- `extraction.by_source` / `by_region`: extractabilidad real.
- `validation.by_reason`: paywalls, títulos genéricos, baja relevancia.
- `selection.selected_by_region`: balance final.
- `llm.rejected_not_relevant`: artículos eliminados por filtro editorial LLM.

## Automatización

GitHub Actions ejecuta [.github/workflows/update-news.yml](.github/workflows/update-news.yml) **2 veces por día** (08:00 y 12:00 ART). Cada corrida:

1. Instala dependencias: `requests beautifulsoup4 python-dotenv anthropic`
2. Corre `python main.py --production`
3. Commitea `index.html`, `published_urls.json`, `weekly_log.json`, `weekly_summary.json` si hubo cambios
4. Hace `git push`

La salida queda servida desde GitHub Pages. **Nota**: GitHub pausa workflows programados si el repo no tiene actividad por ~60 días. Un push manual reactiva el schedule.

## Seguridad

Modelo de amenaza: el pipeline ingiere contenido no confiable (RSS, scraping, redirects de Google News) y produce una página **pública** en GitHub Pages. Los controles vigentes:

| Vector | Control | Dónde |
|---|---|---|
| Secretos | `.env` en `.gitignore`, nunca commiteado; sin secretos en el historial git. CI usa el `GITHUB_TOKEN` por defecto con `permissions: contents: write` (mínimo). API keys solo vía env/Secrets. | `.gitignore`, `update-news.yml` |
| XSS almacenado | Todo contenido dinámico vía `html.escape`; `href` vía `_safe_url` (solo `http`/`https`). | `web.py` |
| SSRF | `_is_safe_url`: solo `http`/`https`, bloqueo de IPs privadas/loopback/link-local/reservadas. Cap de respuesta 5 MB. | `http.py` |
| Crecimiento sin límite | Ventanas rolling podadas: `published_urls.json` y `weekly_log.json` a 7 días en cada guardado. | `pipeline.py` |
| Quota / costo APIs | CSE: cap 95 queries/día (`google_cse_usage.json`), fallback a Google News RSS al agotarse o ante 429. Anthropic: delay 13 s entre llamadas; volumen acotado por `--target-count`; caché diaria `llm_cache.json`. | `discovery.py`, `llm.py` |

**Residuales conocidos (aceptados):**

- El guard SSRF valida la URL inicial, no cada salto de redirect (requests sigue redirects automáticamente). Blast radius bajo: el runner de GitHub Actions no tiene red interna sensible.
- El repo es **público** por diseño (GitHub Pages gratis): `published_urls.json` y `weekly_log.json` exponen el historial de curaduría. No hay secretos ahí, pero sí la lista de foco competitivo. Si se quiere privado, mover Pages a repo privado (requiere plan pago) o publicar solo `index.html` a un repo separado vía `publish_github.py`.
- El `GITHUB_TOKEN` local del `.env` (usado solo por `publish_github.py`, no por el CI) debería ser un PAT fine-grained con scope mínimo (`contents:write` sobre ese único repo). Rotarlo si alguna vez se expuso.
- Sin tope duro de llamadas LLM más allá de `--target-count`; un día anómalo no puede dispararse de costo de forma significativa, pero no hay corte explícito.

## Tests

```powershell
python -m unittest discover -s tests -v
```

Agregar tests cuando se modifiquen: reglas de aceptación/rechazo, keywords/aliases ambiguos, cola regional, extractores, validación post-extracción, **y los guards de seguridad (`_safe_url`, `_is_safe_url`)**.

## Convenciones

- No editar `index.html` a mano.
- No commitear `data/` (excepto `weekly_log.json` y `weekly_summary.json` que van en raíz).
- UTF-8 en todo.
- No hardcodear tokens.
- Limpiar `__pycache__` después de pruebas si quedan.
- Preservar diagnósticos: son la herramienta principal para mejorar cobertura y calidad.

## LLM — Estado actual y costos

### Implementado (operativo)

| Función | Modelo | Cuándo | Costo aprox/corrida |
|---|---|---|---|
| Clasificación + resumen | Haiku 4.5 | Cada artículo nuevo | ~$0.05 |
| Dedup semántica | Haiku 4.5 | 1 batch/corrida | ~$0.003 |
| QA editorial | Sonnet 4.6 | 1 llamada/corrida | ~$0.02 |
| Briefing por área | Sonnet 4.6 | 1 llamada/corrida | ~$0.02 |
| Resumen semanal | Sonnet 4.6 | 1 llamada/semana | ~$0.05 |
| **Total estimado** | | **2 corridas/día** | **~$0.19/día / ~$5.7/mes** |

Todas las llamadas usan **prompt caching** (ephemeral). La caché local `data/llm_cache.json` evita reprocesar artículos ya analizados en el mismo día.

### Próximas mejoras posibles

- Aumentar cuerpo enviado al LLM de 500 a 1500 chars para mejores resúmenes.
- Dedup cruzada entre empresas (mismo evento con dos compañías involucradas).
- Briefing de apertura del día en la cabecera del dashboard.
