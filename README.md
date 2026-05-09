# Global Beverage News Monitor

Monitor privado y automatizado de noticias del sector global de bebidas. Recopila, filtra, clasifica y presenta los artículos más relevantes de fuentes curadas en Argentina, Latinoamérica y el mundo.

## 🔗 Ver el monitor

**[→ Abrir monitor de noticias](https://msmassolo.github.io/Noticias-del-sector---Ult-momento/)**

---

## Qué hace

- Descubre noticias desde RSS de trade media global, medios locales y regionales, y búsquedas en Google News
- Filtra por empresas del sector (Coca-Cola, AB InBev, Diageo, Grupo Cepas, y 50+ más) y por categorías temáticas (resultados financieros, M&A, innovación, regulación, etc.)
- Clasifica y prioriza los artículos más relevantes con un sistema de scoring por fuente, tema, empresa y recencia
- Genera un reporte HTML interactivo con búsqueda y filtros por tema, cobertura, empresa, país e idioma
- Se actualiza automáticamente 6 veces por día (6, 8, 11, 14, 17 y 20 hs ART) vía GitHub Actions
- Evita republicar artículos ya mostrados (deduplicación rolling de 7 días)

## Cobertura

| Región | Fuentes |
|---|---|
| Local (Argentina) | Infobae, La Nación, Clarín, Ámbito, iProfesional, Cronista, BAE Negocios |
| Regional (LATAM) | Valor Econômico, Diario Financiero, Portafolio, Gestión, El Observador, Exame |
| Mundial | Just Drinks, The Drinks Business, FoodBev Media, Beverage Daily, Financial Times, Reuters, Bloomberg |

## Temas monitoreados

Financial Results · Marketing & Innovation · M&A / Strategy · Regulation & Policy · Consumer & Market Trends · Risk & Reputation · Company News

## Estructura del proyecto

```
Noticias del sector/
├── main.py                  ← Punto de entrada
├── beverage_news/           ← Módulo principal (discovery, filtering, ranking, extraction, web)
├── config/                  ← companies.json, keywords.json, sources.json
├── .github/workflows/       ← GitHub Actions (cron 6x/día)
├── index.html               ← Salida generada (se sobreescribe en cada corrida)
└── published_urls.json      ← Historial de URLs publicadas (deduplicación cross-run)
```

## Ejecución local

```powershell
pip install -r requirements.txt

python main.py
python main.py --target-count 40 --max-search-queries 55
python main.py --no-search   # Solo RSS, sin Google News
```

## Tests

```powershell
python -m unittest discover -s tests -v
```
