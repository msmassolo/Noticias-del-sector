# Noticias del Sector — Monitor de Noticias de Bebidas

## Qué hace este proyecto

Pipeline ETL automatizado que recopila, filtra, clasifica y presenta noticias del sector global de bebidas (soft drinks, cerveza, espirituosos, vino, agua, energy drinks, etc.). Se ejecuta automáticamente 6 veces por día vía GitHub Actions y publica un `index.html` interactivo en GitHub Pages.

**Flujo completo:**
```
Discovery → Filtering → Ranking → Extraction → Selection → HTML
```

Produce ~40 artículos por corrida, equilibrados entre cobertura Local (Argentina), Regional (LATAM) y Mundial.

---

## Estructura del proyecto

```
Noticias del sector/
├── main.py                  ← Punto de entrada (args: --limit, --target-count, --min-per-region, --max-search-queries, --no-search, --output)
├── publish_github.py        ← Publica index.html via GitHub Contents API
├── requirements.txt         ← requests, beautifulsoup4, python-dotenv
├── index.html               ← Salida generada (NO editar a mano)
├── published_urls.json      ← Historial de URLs publicadas (deduplicación cross-run, rolling 7 días)
├── .github/workflows/       ← GitHub Actions: cron 6x/día (6, 8, 11, 14, 17, 20 hs ART)
├── beverage_news/           ← Módulo principal
│   ├── config.py            ← Carga companies/keywords/sources desde config/
│   ├── discovery.py         ← RSS, secciones HTML, Google News RSS (multithreaded)
│   ├── filtering.py         ← Acepta/rechaza candidatos; deduplica
│   ├── ranking.py           ← Scoring (0–100+) y selección balanceada por región
│   ├── extraction.py        ← Descarga HTML, extrae título/resumen/body (multithreaded)
│   ├── pipeline.py          ← Orquesta todo; escribe JSON intermedios en data/
│   ├── web.py               ← Genera index.html con JS/CSS embebido
│   ├── http.py              ← HTTP client (UA spoofing, encoding, timeout 4s)
│   ├── text.py              ← Normalización, limpieza, word-boundary matching
│   ├── urls.py              ← Normalización URLs, strip tracking params
│   └── models.py            ← Dataclasses: Source, Company, Candidate, Article
├── config/
│   ├── companies.json       ← 55 empresas con aliases (Coca-Cola, AB InBev, Grupo Cepas…)
│   ├── keywords.json        ← 11 categorías temáticas, términos EN/ES/PT
│   └── sources.json         ← 30+ fuentes (trade global + Argentina + LATAM)
├── data/                    ← Salidas intermedias por ejecución (en .gitignore)
│   ├── candidates.json
│   ├── accepted_candidates.json
│   ├── articles.json
│   └── diagnostics.json
└── tests/
    └── test_core.py         ← Tests unitarios (URLs, filtering, extraction, matching)
```

---

## Cómo correr el proyecto

### Ejecución local
```powershell
# Desde la carpeta del proyecto
C:\Bibliotecas\Python312\python.exe -m pip install -r requirements.txt --target "c:\Proyectos Claude\Bibliotecas\Bibliotecas py\"

# Agregar sys.path al inicio de main.py si se usa la biblioteca compartida
# O instalar en venv local

C:\Bibliotecas\Python312\python.exe main.py
C:\Bibliotecas\Python312\python.exe main.py --target-count 40 --max-search-queries 55
C:\Bibliotecas\Python312\python.exe main.py --no-search  # Solo RSS, sin Google News
```

### Publicar en GitHub
```powershell
$env:GITHUB_TOKEN="ghp_..."
C:\Bibliotecas\Python312\python.exe publish_github.py --owner USER --repo REPO --branch main --local-path index.html --remote-path index.html
```

### Tests
```powershell
C:\Bibliotecas\Python312\python.exe -m unittest discover -s tests -v
```

---

## Lógica de negocio clave

### Scoring (ranking.py)
| Factor | Puntos |
|---|---|
| financial_results | +32 |
| product_innovation | +28 |
| ma_and_strategy | +26 |
| Company match | +28 (+10 si priority company) |
| Trade source (Just Drinks, FoodBev…) | +18 |
| Noticia ≤1 día | +12 |
| Google News query | +5 |

### Criterios de aceptación de candidatos (filtering.py)
- `company_match`: cualquier empresa/alias encontrada en el texto
- `strong_keyword_match`: ≥2 keywords + ≥1 no-genérica + contexto bebidas + (mundial o contexto negocio)
- `trade_source_keyword_match`: fuente trade + ≥1 keyword no-genérica + contexto bebidas
- `local_regional_beverage_section`: región Local/Regional + sección discovery + bebidas + negocios
- Todo lo demás → rechazado (`weak_match`)

### Selección balanceada
- Mínimo 7 artículos por región (Local/Regional/Mundial)
- Target total: 40 artículos

---

## Categorías temáticas (keywords.json)

| Categoría | Peso |
|---|---|
| financial_results | 32 |
| product_innovation | 28 |
| ma_and_strategy | 26 |
| marketing_innovation | 24 |
| distribution_execution | 22 |
| regulation_tax_policy | 21 |
| risk_crisis_reputation | 20 |
| packaging_sustainability | 18 |
| consumer_market_trends | 16 |
| supply_chain_commodities | 14 |
| company_news | 10 (fallback) |

---

## Datos de configuración

### companies.json
Cada empresa tiene: `name`, `country`, `segments[]`, `aliases[]`

Empresas incluidas (muestra): The Coca-Cola Company, PepsiCo, AB InBev, Heineken, Diageo, Pernod Ricard, Red Bull, Monster Beverage, Nestlé Waters, Ambev, **Grupo Cepas** (empresa del usuario, con aliases: Gancia, Terma, Bodega Graffigna).

Para agregar una empresa, editar directamente `config/companies.json`.

### sources.json
Cada fuente tiene: `name`, `url`, `rss[]`, `sections[]`, `country`, `region` (Local/Regional/Mundial), `language`, `trade` (bool).

Para agregar una fuente, editar directamente `config/sources.json`.

### keywords.json
Diccionario `{categoria: [término1, término2, ...]}`. Términos en EN/ES/PT. El matching es accent-insensitive y word-boundary.

---

## Salida generada (index.html)

- Búsqueda full-text instantánea (accent-insensitive, tokenizada por espacios, mínimo 2 chars)
- Filtros interactivos: **Coverage** (Todo/Local/Regional/Global) + **Topic** (7 categorías)
  - Company, Country, Source, Language están como `data-*` en cada card pero no como botones de filtro
- Cards organizadas por sección de topic; secciones vacías se ocultan automáticamente
- Cada card muestra: badge de región, fuente, fecha, título, resumen, botones "Read original" y "Translate to Spanish", cuerpo completo colapsable (`<details>`)
- Contador de artículos visibles en tiempo real
- Grid 2 columnas, responsive (1 columna ≤860px)
- Timestamp Buenos Aires, conteo por región en el header

---

## Convenciones del proyecto

- `index.html` se regenera en cada ejecución; nunca editarlo a mano
- `data/` está en `.gitignore`; los JSON intermedios son efímeros
- El token de GitHub **siempre** por env var, nunca hardcodeado
- Encoding: UTF-8 en todo el pipeline
- Multithreading: discovery y extraction usan 10 workers (ThreadPoolExecutor)
- Timeout HTTP: 4 segundos
- Body mínimo para publicar: 180 caracteres; máximo: 12.000 caracteres

---

## Estado actual y mejoras posibles

### Problemas conocidos (observados en producción)
- **Falsos positivos de fuentes genéricas**: El Financiero México está generando artículos de clima (pronóstico del tiempo) y OVNIS vinculados a AJE Group por match superficial. El filtro `company_match` es demasiado permisivo para fuentes con secciones no-bebidas.
- **Artículos de Brewbound con paywall**: los cards muestran "Log in to your Insider account..." como body — el contenido no se puede extraer. Considerar filtrar o marcar artículos de newsletter gateados.
- **Categorías del HTML vs keywords.json**: el HTML solo muestra 7 tópicos (los con artículos); las 4 categorías sin artículos frecuentes (`product_innovation`, `distribution_execution`, `packaging_sustainability`, `supply_chain_commodities`) raramente aparecen como botones activos.

### Mejoras posibles
1. **Fuentes**: agregar más RSS regionales o trade media europeo
2. **Empresas**: ampliar aliases o agregar empresas relevantes faltantes
3. **Scoring**: ajustar pesos de categorías o añadir nuevas señales
4. **Extracción**: mejorar fallbacks para sitios que bloquean scraping
5. **Filtering**: afinar criterios para reducir falsos positivos en fuentes generalistas (El Financiero México, Google News)
6. **HTML**: agregar filtros por Company/Country/Source (los `data-*` ya están, falta el UI)
7. **HTML**: agregar nuevas vistas, gráficos de tendencias, exportación CSV
