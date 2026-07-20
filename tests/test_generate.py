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
        keywords, feeds = MODULE.validate_config(config)
        enabled = [feed for feed in feeds if feed.enabled]
        self.assertGreaterEqual(len(keywords), 10)
        self.assertGreaterEqual(len(enabled), 20)
        self.assertEqual(len({MODULE.normalize_url(feed.url) for feed in enabled}), len(enabled))
        categories = {feed.category for feed in enabled}
        self.assertGreaterEqual(len(categories), 8)


if __name__ == "__main__":
    unittest.main()
