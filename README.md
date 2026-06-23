# Global Beverage News Monitor

Monitor automatizado de noticias del sector de bebidas de consumo masivo. Recolecta candidatos desde fuentes directas y Google News, filtra por relevancia sectorial, extrae el contenido publicable, rankea y genera un `index.html` navegable servido por GitHub Pages.

El foco es consumo masivo de bebidas — gaseosas, cerveza, vino, espirituosos, agua, energizantes, RTD, bebidas funcionales — más temas que afectan ese mercado: ingredientes, envases, distribución, regulación, resultados financieros y movimientos corporativos.

Objetivo de uso: lectura diaria rápida del equipo, para tener una visión de actualidad e innovación de competidores y del mercado.

## Monitor publicado

[Abrir monitor de noticias](https://msmassolo.github.io/Noticias-del-sector/)

## Flujo del pipeline

```text
Config -> Discovery -> Filtering -> Ranking -> Balanced Queue -> Extraction -> Validation -> Selection -> HTML
```

1. Carga fuentes, empresas y keywords desde `config/`.
2. Descubre candidatos desde RSS, feeds detectados, secciones HTML y Google News RSS.
3. En Google News intenta usar la URL original del medio (parsea el HTML del item RSS) antes de quedarse con un redirect de `news.google.com`.
4. Normaliza URLs, remueve tracking params y deduplica por URL/título.
5. Filtra candidatos viejos (ventana de 36 h en todas las regiones), repetidos, ya publicados, de login/newsletter/suscripción, off-topic (otros rubros) y títulos histórico/clickbait.
6. Rechaza contextos de baja utilidad (salud, recetas, clima, lifestyle, curiosidades) cuando no hay ángulo de industria, negocio, mercado, regulación, marca o empresa.
7. Rankea por fuente, recencia, tema, empresa, prioridad estratégica y motivo de aceptación.
8. Construye una cola de extracción balanceada por región para que Regional y Local tengan intentos reales antes de completar con Global.
9. Extrae contenido desde JSON-LD, metadatos y párrafos del artículo.
10. Valida título, paywall, repetición, **antigüedad real (36 h con la fecha extraída de JSON-LD/OG)** y relevancia industrial post-extracción.
11. Selecciona hasta `target-count` artículos respetando cuota regional cuando hay disponibilidad. El número es un tope flexible, no una obligación de relleno.
12. Genera `index.html` y actualiza `published_urls.json` para evitar republicaciones durante 7 días.

## Volumen esperado

La salida razonable es **25–60 notas por corrida** según la actividad real del día. Cuando hay poco contenido relevante el monitor publica menos; no rellena para alcanzar un mínimo.

## Filtros clave (estado actual)

- **Ventana de antigüedad: 36 h** uniforme para Local, Regional y Mundial. Se aplica dos veces: en `filtering.py` con la fecha del candidato cuando existe, y en `validation.py` con la fecha real extraída del artículo (cubre listings de sección sin fecha visible). Si la extracción no devuelve fecha, el artículo se descarta con `missing_publish_date`.
- **Rechazo por rubro off-topic**: repuestos/autopartes, ferretería, electrodomésticos, farmacia, indumentaria, inmobiliaria, tecnología de consumo, cripto, deportes/espectáculos — para evitar que keywords genéricas (mayorista, distribución, marca) traigan notas de rubros ajenos.
- **Rechazo de títulos histórico/clickbait**: "¿Sabías que…?", "La historia de…", "El origen de…", "Un día como hoy", "Así nació…", "Hace X años…", "Efemérides" — salvo en fuentes trade.
- Deduplicación rolling de 7 días por URL + título normalizado.
- Bloqueo de paths de login/newsletter/registro/suscripción.
- Penalización a redirects `news.google.com` no resueltos.

## Cobertura configurada

El proyecto monitorea fuentes globales, argentinas y regionales LATAM.

### Global

Just Drinks, FoodBev Media, The Drinks Business, The Spirits Business, Brewbound, VinePair, Wine Business, Beverage Industry, Beverage Daily, Harpers Wine & Spirit, Food Navigator, Reuters Business, Bloomberg, Financial Times.

### Argentina

Infobae, La Nación, Clarín, Ámbito, Cronista, iProfesional, Los Andes, MDZ, La Voz, Mercado, El Economista, Forbes Argentina, BAE Negocios, Perfil, Wines of Argentina, Coviar, Vinos y Bodegas, Planeta Joy, Ciudadano News, Vinetur Argentina.

### Regional LATAM

Valor Econômico, Exame, Infomoney, DFSud, América Economía, America Retail, Diario Financiero, La Tercera, El Mostrador, Gestión, El Comercio Perú, Portafolio, La República Colombia, El Financiero México, Expansión México, El Observador, BN Americas.

## Temas monitoreados

- `financial_results`
- `product_innovation`
- `ma_and_strategy`
- `marketing_innovation`
- `distribution_execution`
- `regulation_tax_policy`
- `risk_crisis_reputation`
- `non_alcoholic_beverages`
- `packaging_sustainability`
- `alternative_ingredients`
- `consumer_market_trends`
- `supply_chain_commodities`

## Estructura

```text
Noticias del sector/
├── main.py                    # CLI del pipeline
├── publish_github.py          # Publicación opcional vía GitHub Contents API
├── index.html                 # Salida generada; no editar a mano
├── published_urls.json        # Historial rolling de URLs/títulos publicados
├── requirements.txt
├── beverage_news/
│   ├── config.py              # Carga y valida JSON de configuración
│   ├── discovery.py           # RSS, secciones HTML y Google News RSS
│   ├── filtering.py           # Reglas de aceptación/rechazo
│   ├── ranking.py             # Scoring, cola balanceada y selección final
│   ├── extraction.py          # Extracción de título, resumen, fecha y body
│   ├── validation.py          # Depuración post-extracción
│   ├── web.py                 # Generación del HTML
│   ├── http.py                # Cliente HTTP
│   ├── text.py                # Limpieza, normalización y matching
│   ├── urls.py                # Normalización de URLs
│   └── models.py              # Dataclasses
├── config/
│   ├── sources.json
│   ├── companies.json
│   └── keywords.json
├── data/                      # Diagnósticos e intermedios generados
└── tests/
    └── test_core.py
```

## Ejecución local

```powershell
pip install -r requirements.txt
python main.py
```

Opciones útiles:

```powershell
python main.py --target-count 50 --limit 120 --max-search-queries 90
python main.py --min-per-region 4
python main.py --no-search
python main.py --debug
python main.py --min-body-length 80
```

## Automatización

GitHub Actions ejecuta `.github/workflows/update-news.yml` 6 veces por día:

- 06:00 ART
- 08:00 ART
- 11:00 ART
- 14:00 ART
- 17:00 ART
- 20:00 ART

El workflow instala dependencias, corre `python main.py`, agrega `index.html` y `published_urls.json`, commitea si hubo cambios y hace `git push`.

## Diagnósticos

Cada ejecución escribe JSON intermedios en `data/`:

- `candidates.json`: candidatos descubiertos.
- `accepted_candidates.json`: candidatos aceptados y enviados a extracción.
- `articles.json`: artículos finales seleccionados.
- `diagnostics.json`: métricas de discovery, filtering, queue, extraction, validation y selection.

Los diagnósticos incluyen conteos por fuente, región, motivo de aceptación, motivo de descarte, errores de extracción y rechazos de validación. Esto permite detectar si un problema viene de fuentes rotas, filtros demasiado estrictos, mala extractabilidad o falta de candidatos regionales.

## Tests

```powershell
python -m unittest discover -s tests -v
```

Los tests cubren normalización de URLs, matching accent-insensitive, deduplicación, filtros de baja calidad, extracción, resolución de enlaces originales de Google News, cola regional balanceada y validación post-extracción.

---

## Mejoras pendientes (no aplicadas)

Anotadas para evaluar más adelante. Requieren decisiones de configuración o costos antes de implementar:

1. **Filtro semántico con LLM en cola corta.** Pasar los top-N candidatos rankeados por Claude Haiku (con prompt cacheado) para clasificar relevancia y descartar falsos positivos que las reglas no atrapan. Bloqueado por: definir API key de Anthropic y aceptar costo recurrente estimado de USD 0,02–0,05 por corrida.
8. **Twitter/X o LinkedIn de cuentas oficiales** (vía Nitter, scraping o API oficial). Las marcas grandes anuncian primero por redes. Bloqueado por: el scraping es frágil y viola ToS. Sustituto recomendado: feeds RSS de newsrooms corporativos oficiales (Coca-Cola, ABInBev, Diageo, Pernod, Heineken).
10. **Resumen one-liner con LLM por nota.** Reemplazar el snippet RSS por un resumen accionable de qué pasó y por qué importa. Caché por URL para acotar costo. Misma dependencia que (1).
