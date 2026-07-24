import datetime as dt
import importlib.util
import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / ".github" / "actions" / "seo-news" / "generate.py"
SPEC = importlib.util.spec_from_file_location("seo_news_generate", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
assert SPEC.loader
SPEC.loader.exec_module(MODULE)


class GeneratorTests(unittest.TestCase):
    def test_slugify(self):
        self.assertEqual(
            MODULE.slugify("Unity IL2CPP + C# Recovery"),
            "unity-il2cpp-c-sharp-recovery",
        )

    def test_safe_http_url(self):
        self.assertEqual(
            MODULE.safe_http_url("https://example.com/feed.xml"),
            "https://example.com/feed.xml",
        )
        with self.assertRaises(ValueError):
            MODULE.safe_http_url("file:///etc/passwd")
        with self.assertRaises(ValueError):
            MODULE.safe_http_url("https://user:password@example.com/feed.xml")

    def test_stable_id_ignores_tracking_query_and_guid_changes(self):
        first = MODULE.stable_entry_id(
            "https://example.com/release/1?utm_source=rss", "Unity 6 release"
        )
        second = MODULE.stable_entry_id(
            "https://example.com/release/1", "Unity 6 release"
        )
        self.assertEqual(first, second)

    def test_atom_parser(self):
        spec = MODULE.FeedSpec(
            name="Test Releases",
            url="https://example.com/releases.atom",
            category="test",
            trust=10,
            enabled=True,
            max_items=2,
            source_boost=2,
            topic_terms=("Unity IL2CPP",),
            include_any=(),
            exclude_any=(),
        )
        xml = b"""<?xml version='1.0' encoding='utf-8'?>
        <feed xmlns='http://www.w3.org/2005/Atom'>
          <entry>
            <title>Version 2.0</title>
            <link rel='alternate' href='https://example.com/releases/2'/>
            <updated>2026-07-20T01:00:00Z</updated>
            <content type='html'>&lt;p&gt;IL2CPP metadata improvements&lt;/p&gt;</content>
          </entry>
        </feed>"""
        entries = MODULE.parse_feed(xml, spec)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].title, "Version 2.0")
        self.assertIn("IL2CPP metadata", entries[0].summary)
        self.assertEqual(entries[0].category, "test")

    def test_topic_scoring_and_hard_exclusion(self):
        keyword = MODULE.Keyword(
            term="Unity IL2CPP",
            aliases=("Unity IL2CPP", "IL2CPP"),
            priority=10,
            internal_url="/",
        )
        entry = MODULE.Entry(
            entry_id="1",
            title="Version 2.0",
            url="https://example.com/1",
            summary="Metadata improvements",
            source="Example",
            source_url="https://example.com/feed",
            category="il2cpp-core",
            published=dt.datetime.now(dt.timezone.utc),
            trust=8,
            source_boost=3,
            topic_terms=["Unity IL2CPP"],
            matched_terms=[],
            score=0,
        )
        scored = MODULE.score_entry(entry, [keyword])
        self.assertIn("Unity IL2CPP", scored.matched_terms)
        self.assertGreaterEqual(scored.score, 17)

        blocked = MODULE.Entry(**{**entry.__dict__, "summary": "Casino giveaway", "matched_terms": [], "score": 0})
        scored_blocked = MODULE.score_entry(blocked, [keyword], global_exclude_terms=["casino"])
        self.assertEqual(scored_blocked.score, -1000)

    def test_diversity_caps_source_and_category(self):
        now = dt.datetime.now(dt.timezone.utc)
        entries = []
        for index in range(6):
            entries.append(
                MODULE.Entry(
                    entry_id=str(index),
                    title=f"Entry {index}",
                    url=f"https://example.com/{index}",
                    summary="",
                    source="Same Source" if index < 4 else f"Source {index}",
                    source_url="https://example.com/feed",
                    category="same-category" if index < 5 else "other-category",
                    published=now,
                    trust=10,
                    source_boost=0,
                    topic_terms=[],
                    matched_terms=["Unity IL2CPP"],
                    score=100 - index,
                )
            )
        selected = MODULE.select_diverse(entries, 6, max_items_per_source=2, max_items_per_category=3)
        self.assertLessEqual(sum(item.source == "Same Source" for item in selected), 2)
        self.assertLessEqual(sum(item.category == "same-category" for item in selected), 3)

    def test_historical_entries_are_not_republished(self):
        pages = [
            {
                "date": "2026-07-19",
                "entries": [{"entry_id": "old-1"}, {"entry_id": "old-2"}],
            },
            {
                "date": "2026-07-20",
                "entries": [{"entry_id": "today-1"}],
            },
        ]
        result = MODULE.historical_entry_ids(pages, "2026-07-20")
        self.assertEqual(result, {"old-1", "old-2"})

    def test_project_config_has_many_unique_valid_sources(self):
        config = json.loads((ROOT / "seo" / "news-config.json").read_text(encoding="utf-8"))
        keywords, feeds, github_searches, hacker_news = MODULE.validate_config(config)
        enabled = [feed for feed in feeds if feed.enabled]
        self.assertGreaterEqual(len(keywords), 15)
        self.assertGreaterEqual(len(enabled), 60)
        self.assertGreaterEqual(sum(source.enabled for source in github_searches), 8)
        self.assertGreaterEqual(sum(source.enabled for source in hacker_news), 2)
        self.assertEqual(len({MODULE.normalize_url(feed.url) for feed in enabled}), len(enabled))
        categories = {feed.category for feed in enabled}
        self.assertGreaterEqual(len(categories), 15)

    def test_github_search_parser_adds_popularity_signal(self):
        spec = MODULE.GithubSearchSpec(
            name="GitHub Hot IL2CPP",
            query="il2cpp created:>={since}",
            category="github-hot-il2cpp",
            trust=8,
            enabled=True,
            max_items=3,
            source_boost=4,
            topic_terms=("Unity IL2CPP",),
            include_any=(),
            exclude_any=(),
            lookback_days=90,
            min_stars=2,
            per_page=20,
            sort="stars",
            order="desc",
        )
        now = dt.datetime(2026, 7, 24, tzinfo=dt.timezone.utc)
        raw = {"items": [{
            "html_url": "https://github.com/example/il2cpp-tool",
            "full_name": "example/il2cpp-tool",
            "description": "IL2CPP metadata recovery and analysis",
            "stargazers_count": 128,
            "forks_count": 20,
            "open_issues_count": 4,
            "language": "C++",
            "topics": ["il2cpp", "reverse-engineering"],
            "created_at": "2026-07-01T00:00:00Z",
            "pushed_at": "2026-07-23T00:00:00Z",
            "archived": False,
            "disabled": False,
        }]}
        entries = MODULE.parse_github_search_result(raw, spec, now)
        self.assertEqual(len(entries), 1)
        self.assertIn("128 stars", entries[0].summary)
        self.assertGreater(entries[0].source_boost, spec.source_boost)

    def test_hacker_news_source_validation(self):
        source = MODULE.load_hacker_news_sources([{
            "name": "HN Top",
            "list": "topstories",
            "include_any": ["WebAssembly"],
        }])[0]
        self.assertEqual(source.list_name, "topstories")
        with self.assertRaises(ValueError):
            MODULE.load_hacker_news_sources([{
                "name": "Invalid",
                "list": "unknown-list",
            }])

    def test_public_url_prefix_is_applied_without_duplication(self):
        site = {"url_prefix": "/public"}
        self.assertEqual(MODULE.site_url_path(site, "news/"), "/public/news/")
        self.assertEqual(
            MODULE.site_url_path(site, "/public/news/item.html"),
            "/public/news/item.html",
        )
        self.assertEqual(
            MODULE.migrate_page_path(site, "/news/item.html"),
            "/public/news/item.html",
        )

    def test_generated_navigation_uses_public_prefix(self):
        site = {
            "name": "CPP2IL",
            "base_url": "https://cpp2il.com",
            "language": "en",
            "description": "News",
            "url_prefix": "/public",
        }
        page = {
            "date": "2026-07-20",
            "path": "/news/test.html",
            "title": "Test News",
            "item_count": 1,
            "updated_at": "2026-07-20T00:00:00+00:00",
        }
        rendered = MODULE.render_index(site, [page])
        self.assertIn('href="/public/news/test.html"', rendered)
        self.assertIn('href="/public/news/github-hot.html"', rendered)
        self.assertIn('href="https://cpp2il.com/public/news.xml"', rendered)
        self.assertNotIn('href="/news/test.html"', rendered)


if __name__ == "__main__":
    unittest.main()
