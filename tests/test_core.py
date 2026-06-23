import unittest
from datetime import datetime, timezone

from beverage_news.extraction import extract_title_summary_body
from beverage_news.discovery import _original_url_from_google_description
from beverage_news.filtering import filter_candidates, match_companies, match_keyword_categories
from beverage_news.models import Article, Candidate, Company
from beverage_news.ranking import build_extraction_queue, rank_items
from beverage_news.text import normalize_text, term_in_text
from beverage_news.urls import normalize_url
from beverage_news.validation import validate_article
from beverage_news.web import TOPIC_LABELS, _article_dict, _article_html, _filter_buttons, _safe_url, _sort_topics, _uniq
from beverage_news.http import _is_safe_url


class CoreTest(unittest.TestCase):
    def setUp(self):
        self.companies = [
            Company(
                name="The Coca-Cola Company",
                country="United States",
                segments=["soft_drinks", "water"],
                aliases=["Coca-Cola", "Coke", "Sprite"],
            ),
            Company(
                name="Heineken",
                country="Netherlands",
                segments=["beer"],
                aliases=["Heineken NV", "Amstel"],
            ),
        ]
        self.keywords = {
            "beer": ["beer", "brewer", "cerveza"],
            "soft_drinks": ["soft drinks", "soda", "gaseosas"],
            "packaging_regulation": ["sugar tax", "deposit return", "regulation"],
        }

    def test_normalize_url_removes_tracking_and_fragment(self):
        url = normalize_url("https://example.com/news/", "/article?utm_source=x&id=7#comments")
        self.assertEqual(url, "https://example.com/article?id=7")

    def test_term_matching_is_word_based_and_accent_insensitive(self):
        self.assertTrue(term_in_text("cerveza", "La cervecera lanzo una cerveza sin alcohol"))
        self.assertTrue(term_in_text("sugar tax", "New Sugar Tax rules hit soft drinks"))
        self.assertFalse(term_in_text("rum", "rumors about unrelated markets"))

    def test_match_companies_uses_aliases(self):
        matches = match_companies("Sprite sales rose in Mexico", self.companies)
        self.assertEqual([company.name for company in matches], ["The Coca-Cola Company"])

    def test_match_keywords_multilanguage(self):
        matches = match_keyword_categories("Nueva regulation para cerveza y sugar tax", self.keywords)
        self.assertEqual(sorted(matches), ["beer", "packaging_regulation"])

    def test_filter_accepts_company_match_and_deduplicates(self):
        candidates = [
            Candidate(title="Coca-Cola FEMSA invests in bottling", url="https://example.com/a?utm_campaign=x"),
            Candidate(title="Coca-Cola FEMSA invests in bottling", url="https://example.com/a"),
        ]
        accepted, diagnostics = filter_candidates(candidates, self.companies, self.keywords)
        self.assertEqual(len(accepted), 1)
        self.assertEqual(diagnostics["duplicates"], 1)
        self.assertEqual(accepted[0]["reason"], "company_match")

    def test_filter_accepts_company_with_business_context_without_generic_beverage_word(self):
        candidates = [
            Candidate(
                title="Coca-Cola FEMSA arranca 2026 con impulso regional en retail",
                url="https://example.com/kof",
                region="Regional",
                discovery='google_news:"Coca-Cola FEMSA" bebidas',
            )
        ]
        accepted, _diagnostics = filter_candidates(candidates, self.companies, self.keywords)
        self.assertEqual(len(accepted), 1)
        self.assertEqual(accepted[0]["reason"], "company_match")

    def test_filter_accepts_two_sector_keywords_without_company(self):
        candidates = [
            Candidate(title="Sugar tax regulation targets soft drinks", url="https://example.com/tax"),
        ]
        accepted, _diagnostics = filter_candidates(candidates, self.companies, self.keywords)
        self.assertEqual(len(accepted), 1)
        self.assertEqual(accepted[0]["reason"], "strong_keyword_match")

    def test_filter_rejects_weak_generic_article(self):
        candidates = [Candidate(title="Retail market opens higher", url="https://example.com/market")]
        accepted, diagnostics = filter_candidates(candidates, self.companies, self.keywords)
        self.assertEqual(accepted, [])
        self.assertEqual(diagnostics["discarded"]["weak_match"], 1)

    def test_filter_rejects_low_value_health_context_without_industry_angle(self):
        candidates = [
            Candidate(
                title="Bebidas calientes que hidratan segun la ciencia",
                url="https://example.com/hydration",
                summary="Consejos de salud y nutricion sobre te, cafe y consumo de liquidos.",
                region="Local",
                discovery="section:https://example.com/bebidas",
            )
        ]
        accepted, diagnostics = filter_candidates(candidates, self.companies, self.keywords)
        self.assertEqual(accepted, [])
        self.assertEqual(diagnostics["discarded"]["low_value_context"], 1)

    def test_filter_accepts_regional_channel_context(self):
        candidates = [
            Candidate(
                title="Transportadora de cerveja vira supermercado regional",
                url="https://example.com/retail",
                source="Exame",
                country="Brazil",
                region="Regional",
                language="pt",
                discovery="section:https://example.com/negocios",
            )
        ]
        accepted, _diagnostics = filter_candidates(candidates, self.companies, self.keywords)
        self.assertEqual(len(accepted), 1)
        self.assertEqual(accepted[0]["reason"], "local_regional_channel_context")

    def test_google_news_description_prefers_original_article_link(self):
        description = '<a href="https://news.google.com/rss/articles/abc">Google</a><a href="https://example.com/original?utm_source=x">Publisher</a>'
        self.assertEqual(_original_url_from_google_description(description), "https://example.com/original")

    def test_extraction_queue_reserves_regional_attempts(self):
        candidates = []
        for index in range(12):
            candidates.append(
                {
                    "candidate": Candidate(title=f"Global beverage earnings {index}", url=f"https://g.example/{index}", region="Mundial"),
                    "companies": [],
                    "segments": ["financial_results"],
                    "keyword_categories": ["financial_results"],
                    "reason": "strong_keyword_match",
                }
            )
        for index in range(3):
            candidates.append(
                {
                    "candidate": Candidate(title=f"Brasil bebidas mercado {index}", url=f"https://br.example/{index}", region="Regional"),
                    "companies": [],
                    "segments": ["consumer_market_trends"],
                    "keyword_categories": ["consumer_market_trends"],
                    "reason": "local_regional_beverage_section",
                }
            )
        ranked = rank_items(candidates)
        queue, diagnostics = build_extraction_queue(ranked, limit=5, target_count=4, min_per_region=2)
        self.assertEqual(diagnostics["queued_by_region"].get("Regional"), 3)
        self.assertEqual([item["candidate"].region for item in queue[:3]], ["Regional", "Regional", "Regional"])

    def test_extracts_json_ld_article_body_summary_and_title(self):
        html = """
        <html><head>
            <script type="application/ld+json">
            {
              "@type": "NewsArticle",
              "headline": "Brewer reports higher sales",
              "description": "The company posted stronger beer revenue.",
              "datePublished": "2026-05-07T10:00:00Z",
              "articleBody": "The brewer said demand improved across several markets. The article body includes enough detail for extraction and continues with operational context, pricing and volume data."
            }
            </script>
        </head><body></body></html>
        """
        extracted = extract_title_summary_body(html)
        self.assertEqual(extracted["title"], "Brewer reports higher sales")
        self.assertEqual(extracted["summary"], "The company posted stronger beer revenue.")
        self.assertIn("demand improved", extracted["body"])
        self.assertEqual(extracted["published"], "2026-05-07T10:00:00Z")

    def test_extracts_meta_summary_and_article_paragraphs(self):
        html = """
        <html>
            <head>
                <meta property="og:title" content="Soft drinks group faces new rules">
                <meta name="description" content="Regulators are reviewing labels and packaging.">
            </head>
            <body>
                <nav>Menu item</nav>
                <article>
                    <p>Short.</p>
                    <p>The soft drinks company said it would adapt its packaging strategy after regulators introduced new requirements for labels and deposit return systems.</p>
                    <p>Executives added that the changes may affect bottlers, retailers and logistics partners during the next financial year.</p>
                </article>
            </body>
        </html>
        """
        extracted = extract_title_summary_body(html)
        self.assertEqual(extracted["title"], "Soft drinks group faces new rules")
        self.assertEqual(extracted["summary"], "Regulators are reviewing labels and packaging.")
        self.assertIn("packaging strategy", extracted["body"])
        self.assertNotIn("Short.", extracted["body"])

    def test_normalize_text_removes_punctuation_and_accents(self):
        self.assertEqual(normalize_text("Bebidas energéticas: regulación"), "bebidas energeticas regulacion")

    def test_extract_ignores_common_subscription_boilerplate(self):
        html = """
        <article>
            <p>The brewer said demand improved across several markets and that the new packaging plan would support growth in premium channels.</p>
            <p>Stay ahead with unbiased news, expert commentary, and in-depth features on global topics.</p>
            <p>Executives added that new capacity would be used for beer and soft drinks production in the next financial year.</p>
        </article>
        """
        extracted = extract_title_summary_body(html)
        self.assertIn("demand improved", extracted["body"])
        self.assertIn("new capacity", extracted["body"])
        self.assertNotIn("Stay ahead", extracted["body"])

    def test_validation_rejects_non_industry_lifestyle_article(self):
        article = Article(
            title="Bebidas calientes que hidratan segun la ciencia",
            url="https://example.com/a",
            source="Infobae",
            country="Argentina",
            region="Local",
            language="es",
            published=(datetime.now(timezone.utc)).isoformat(),
            summary="Consejos de salud y nutricion sobre liquidos.",
            body="El te y el cafe pueden hidratar segun medicos y nutricionistas. Es una nota de salud sin datos comerciales ni companias.",
            companies=[],
            segments=["consumer_market_trends"],
            keyword_categories=["consumer_market_trends"],
            discovery="section:https://example.com/bebidas",
        )
        ok, reason = validate_article(article)
        self.assertFalse(ok)
        self.assertEqual(reason, "not_industry_relevant")

    def test_web_topic_filters_use_primary_topic_only(self):
        article = Article(
            title="Brand launches new beverage",
            url="https://example.com/a",
            source="Example",
            country="Global",
            region="Mundial",
            language="en",
            published="",
            summary="A launch with financial context.",
            body="The beverage company launched a product and discussed sales.",
            companies=[],
            segments=["product_innovation", "financial_results"],
            keyword_categories=["product_innovation", "financial_results"],
            discovery="rss",
        )
        article_dict = _article_dict(article)
        primary_topics = _sort_topics(_uniq([article_dict["primary_topic"]]))
        html = _filter_buttons("topic", primary_topics, TOPIC_LABELS)
        self.assertEqual(article_dict["primary_topic"], "product_innovation")
        self.assertIn('data-filter="topic" data-value="product_innovation"', html)
        self.assertNotIn('data-filter="topic" data-value="financial_results"', html)


class SecurityTest(unittest.TestCase):
    def test_safe_url_blocks_dangerous_schemes(self):
        self.assertEqual(_safe_url("javascript:alert(1)"), "#")
        self.assertEqual(_safe_url("JavaScript:alert(1)"), "#")
        self.assertEqual(_safe_url("data:text/html,<script>1</script>"), "#")
        self.assertEqual(_safe_url("vbscript:msgbox(1)"), "#")
        self.assertEqual(_safe_url(""), "#")

    def test_safe_url_allows_http(self):
        self.assertEqual(_safe_url("https://example.com/x"), "https://example.com/x")
        self.assertEqual(_safe_url("http://example.com/x"), "http://example.com/x")

    def test_article_html_never_emits_javascript_href(self):
        article = _article_dict(
            Article(
                title="T", url="javascript:alert(document.domain)", source="S",
                country="AR", region="Local", language="es",
                published=datetime.now(timezone.utc).isoformat(),
                summary="s", body="b" * 100, companies=[], segments=["company_news"],
                keyword_categories=[], discovery="rss",
            )
        )
        html = _article_html(article)
        self.assertNotIn("javascript:", html)

    def test_is_safe_url_blocks_ssrf_targets(self):
        self.assertFalse(_is_safe_url("http://169.254.169.254/latest/meta-data/"))
        self.assertFalse(_is_safe_url("http://127.0.0.1:8080/"))
        self.assertFalse(_is_safe_url("http://10.0.0.5/"))
        self.assertFalse(_is_safe_url("http://192.168.1.1/"))
        self.assertFalse(_is_safe_url("file:///etc/passwd"))

    def test_is_safe_url_allows_public_hosts(self):
        self.assertTrue(_is_safe_url("https://www.reuters.com/markets"))
        self.assertTrue(_is_safe_url("http://example.org/feed.xml"))


if __name__ == "__main__":
    unittest.main()
