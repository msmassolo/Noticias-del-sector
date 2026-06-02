import argparse
import logging
import shutil
import sys
from pathlib import Path

# Bibliotecas instaladas localmente (no en el intérprete del sistema)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "Bibliotecas" / "Bibliotecas py"))

from dotenv import load_dotenv

from beverage_news.pipeline import run_pipeline


def _clear_pycache():
    for cache_dir in Path(".").rglob("__pycache__"):
        shutil.rmtree(cache_dir, ignore_errors=True)


def _setup_logging(level=logging.INFO):
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description="Build a private global beverage news monitor.")
    parser.add_argument("--output", default="index.html", help="HTML output path.")
    parser.add_argument("--limit", type=int, default=120, help="Maximum ranked candidates to attempt extracting.")
    parser.add_argument("--target-count", type=int, default=50, help="Tope superior flexible. Salida real esperada: 25–60 segun la calidad del dia; no se rellena para llegar al tope.")
    parser.add_argument("--min-per-region", type=int, default=4, help="Piso por Local/Regional/Mundial cuando hay calidad suficiente. Bajo a proposito para no forzar relleno.")
    parser.add_argument("--max-search-queries", type=int, default=90, help="Maximum global company/search queries for Google News RSS.")
    parser.add_argument("--no-search", action="store_true", help="Disable Google News RSS discovery and use configured RSS only.")
    parser.add_argument("--min-body-length", type=int, default=80, help="Minimum body characters required to publish an article (default: 80).")
    parser.add_argument("--debug", action="store_true", help="Enable debug-level logging.")
    args = parser.parse_args()

    _setup_logging(level=logging.DEBUG if args.debug else logging.INFO)

    articles, diagnostics = run_pipeline(
        output_path=args.output,
        enable_search=not args.no_search,
        limit=args.limit,
        target_count=args.target_count,
        min_per_region=args.min_per_region,
        max_search_queries=args.max_search_queries,
        min_body_chars=args.min_body_length,
    )
    _clear_pycache()
    print(f"Generated {args.output} with {len(articles)} articles.")
    print(f"Diagnostics written to data/diagnostics.json.")


if __name__ == "__main__":
    main()
