#!/usr/bin/env python3
"""Generate curated static SEO news pages from trusted RSS/Atom feeds.

The generator deliberately avoids mass-producing thin pages. It selects only
relevant entries, limits each source/category, remembers previously published
items, and writes source-health information for failed feeds.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import email.utils
import hashlib
import html
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")
WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9+#.\-]{1,40}")
TECH_TOKEN_RE = re.compile(
    r"\b(?:IL2CPP|Unity|WebGL|WebAssembly|WASM|ARM64|ARM32|APK|IPA|AOT|"
    r"C\#|C\+\+|Ghidra|Frida|BepInEx|MelonLoader|AssetRipper|Cpp2IL|"
    r"Il2CppDumper|Il2CppInterop|ILSpy|dnSpy|Emscripten|Binaryen|Wasmtime|WABT|"
    r"JADX|Apktool|APKiD|MobSF|radare2|Rizin|Cutter|RetDec|angr|LIEF|"
    r"Capstone|Unicorn|Keystone|capa|FLOSS|Wasmer|WasmEdge|WAMR|wasm-tools|WASI|"
    r"decompil(?:e|er|ation)|reverse engineering|source recovery|program analysis|"
    r"metadata|binary analysis|dynamic instrumentation|scripting backend)\b",
    re.IGNORECASE,
)
STOPWORDS = {
    "about", "after", "again", "against", "also", "among", "and", "are",
    "because", "been", "before", "being", "between", "but", "can", "could",
    "for", "from", "has", "have", "into", "its", "more", "new", "not",
    "now", "of", "on", "or", "our", "over", "release", "released", "the",
    "their", "this", "through", "to", "update", "updated", "using", "version",
    "was", "were", "will", "with", "you", "your",
}


@dataclass(frozen=True)
class Keyword:
    term: str
    aliases: tuple[str, ...]
    priority: int
    internal_url: str


@dataclass(frozen=True)
class FeedSpec:
    name: str
    url: str
    category: str
    trust: int
    enabled: bool
    max_items: int
    source_boost: int
    topic_terms: tuple[str, ...]
    include_any: tuple[str, ...]
    exclude_any: tuple[str, ...]


@dataclass(frozen=True)
class GithubSearchSpec:
    name: str
    query: str
    category: str
    trust: int
    enabled: bool
    max_items: int
    source_boost: int
    topic_terms: tuple[str, ...]
    include_any: tuple[str, ...]
    exclude_any: tuple[str, ...]
    lookback_days: int
    min_stars: int
    per_page: int
    sort: str
    order: str


@dataclass(frozen=True)
class HackerNewsSpec:
    name: str
    list_name: str
    category: str
    trust: int
    enabled: bool
    max_items: int
    source_boost: int
    topic_terms: tuple[str, ...]
    include_any: tuple[str, ...]
    exclude_any: tuple[str, ...]
    scan_limit: int
    min_points: int
    min_comments: int


@dataclass
class Entry:
    entry_id: str
    title: str
    url: str
    summary: str
    source: str
    source_url: str
    category: str
    published: dt.datetime
    trust: int
    source_boost: int
    topic_terms: list[str]
    matched_terms: list[str]
    score: int


def eprint(*args: object) -> None:
    print(*args, file=sys.stderr)


def clean_text(value: str | None, limit: int | None = None) -> str:
    if not value:
        return ""
    value = TAG_RE.sub(" ", value)
    value = html.unescape(value)
    value = SPACE_RE.sub(" ", value).strip()
    if limit and len(value) > limit:
        value = value[: max(0, limit - 1)].rstrip(" ,.;:-") + "…"
    return value


def slugify(value: str, max_length: int = 80) -> str:
    value = value.lower().replace("c#", "c-sharp").replace("c++", "cpp")
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return value[:max_length].rstrip("-") or "news"


def safe_http_url(value: str) -> str:
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"Unsupported URL: {value!r}")
    if parsed.username or parsed.password:
        raise ValueError("Feed URLs must not contain credentials.")
    return value


def normalize_url(value: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    query = [(k, v) for k, v in query if not k.lower().startswith("utm_")]
    return urllib.parse.urlunsplit(
        (parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/"), urllib.parse.urlencode(query), "")
    )


def stable_entry_id(url: str, title: str) -> str:
    normalized_title = SPACE_RE.sub(" ", title.casefold()).strip()
    material = f"{normalize_url(url)}|{normalized_title}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def parse_datetime(value: str | None, fallback: dt.datetime | None = None) -> dt.datetime:
    if not value:
        return fallback or dt.datetime.now(dt.timezone.utc)
    value = value.strip()
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        parsed = email.utils.parsedate_to_datetime(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def child_text(node: ET.Element, local_names: Sequence[str]) -> str:
    wanted = {name.lower() for name in local_names}
    for child in node.iter():
        local = child.tag.rsplit("}", 1)[-1].lower()
        if local in wanted and child.text:
            return child.text.strip()
    return ""


def entry_link(node: ET.Element) -> str:
    fallback = ""
    for child in node.iter():
        if child.tag.rsplit("}", 1)[-1].lower() != "link":
            continue
        href = child.attrib.get("href", "").strip()
        rel = child.attrib.get("rel", "alternate").strip().lower()
        if href and rel in {"alternate", ""}:
            return href
        if not fallback and href:
            fallback = href
        if not fallback and child.text and child.text.strip():
            fallback = child.text.strip()
    return fallback


def load_keywords(raw: Sequence[dict]) -> list[Keyword]:
    result: list[Keyword] = []
    seen: set[str] = set()
    for item in raw:
        term = clean_text(str(item["term"]))
        if not term or term.casefold() in seen:
            raise ValueError(f"Duplicate or empty keyword term: {term!r}")
        seen.add(term.casefold())
        aliases = tuple(
            dict.fromkeys(
                alias for alias in [term] + [clean_text(str(x)) for x in item.get("aliases", [])] if alias
            )
        )
        result.append(
            Keyword(
                term=term,
                aliases=aliases,
                priority=max(1, int(item.get("priority", 1))),
                internal_url=str(item.get("internal_url", "/")),
            )
        )
    return result


def load_feeds(raw: Sequence[dict]) -> list[FeedSpec]:
    result: list[FeedSpec] = []
    seen_urls: set[str] = set()
    for item in raw:
        url = safe_http_url(str(item["url"]).strip())
        normalized = normalize_url(url)
        if normalized in seen_urls:
            raise ValueError(f"Duplicate feed URL: {url}")
        seen_urls.add(normalized)
        result.append(
            FeedSpec(
                name=clean_text(str(item["name"])),
                url=url,
                category=slugify(str(item.get("category", "other")), 50),
                trust=max(0, min(10, int(item.get("trust", 5)))),
                enabled=bool(item.get("enabled", True)),
                max_items=max(1, int(item.get("max_items", 2))),
                source_boost=int(item.get("source_boost", 0)),
                topic_terms=tuple(clean_text(str(x)) for x in item.get("topic_terms", []) if clean_text(str(x))),
                include_any=tuple(clean_text(str(x)) for x in item.get("include_any", []) if clean_text(str(x))),
                exclude_any=tuple(clean_text(str(x)) for x in item.get("exclude_any", []) if clean_text(str(x))),
            )
        )
    return result


def load_github_searches(raw: Sequence[dict]) -> list[GithubSearchSpec]:
    result: list[GithubSearchSpec] = []
    seen_names: set[str] = set()
    for item in raw:
        name = clean_text(str(item["name"]))
        if not name or name.casefold() in seen_names:
            raise ValueError(f"Duplicate or empty GitHub discovery name: {name!r}")
        seen_names.add(name.casefold())
        query = clean_text(str(item["query"]))
        if not query:
            raise ValueError(f"GitHub discovery {name!r} has an empty query")
        sort = str(item.get("sort", "stars"))
        if sort not in {"stars", "forks", "help-wanted-issues", "updated"}:
            raise ValueError(f"Unsupported GitHub search sort for {name!r}: {sort}")
        order = str(item.get("order", "desc"))
        if order not in {"asc", "desc"}:
            raise ValueError(f"Unsupported GitHub search order for {name!r}: {order}")
        result.append(
            GithubSearchSpec(
                name=name,
                query=query,
                category=slugify(str(item.get("category", "github-hot")), 50),
                trust=max(0, min(10, int(item.get("trust", 7)))),
                enabled=bool(item.get("enabled", True)),
                max_items=max(1, int(item.get("max_items", 3))),
                source_boost=int(item.get("source_boost", 0)),
                topic_terms=tuple(clean_text(str(x)) for x in item.get("topic_terms", []) if clean_text(str(x))),
                include_any=tuple(clean_text(str(x)) for x in item.get("include_any", []) if clean_text(str(x))),
                exclude_any=tuple(clean_text(str(x)) for x in item.get("exclude_any", []) if clean_text(str(x))),
                lookback_days=max(1, int(item.get("lookback_days", 45))),
                min_stars=max(0, int(item.get("min_stars", 2))),
                per_page=max(1, min(100, int(item.get("per_page", 20)))),
                sort=sort,
                order=order,
            )
        )
    return result


def load_hacker_news_sources(raw: Sequence[dict]) -> list[HackerNewsSpec]:
    result: list[HackerNewsSpec] = []
    seen_names: set[str] = set()
    allowed_lists = {"topstories", "beststories", "newstories", "showstories", "askstories"}
    for item in raw:
        name = clean_text(str(item["name"]))
        if not name or name.casefold() in seen_names:
            raise ValueError(f"Duplicate or empty Hacker News source name: {name!r}")
        seen_names.add(name.casefold())
        list_name = str(item.get("list", "topstories"))
        if list_name not in allowed_lists:
            raise ValueError(f"Unsupported Hacker News list for {name!r}: {list_name}")
        result.append(
            HackerNewsSpec(
                name=name,
                list_name=list_name,
                category=slugify(str(item.get("category", "community-hot")), 50),
                trust=max(0, min(10, int(item.get("trust", 6)))),
                enabled=bool(item.get("enabled", True)),
                max_items=max(1, int(item.get("max_items", 3))),
                source_boost=int(item.get("source_boost", 0)),
                topic_terms=tuple(clean_text(str(x)) for x in item.get("topic_terms", []) if clean_text(str(x))),
                include_any=tuple(clean_text(str(x)) for x in item.get("include_any", []) if clean_text(str(x))),
                exclude_any=tuple(clean_text(str(x)) for x in item.get("exclude_any", []) if clean_text(str(x))),
                scan_limit=max(10, min(200, int(item.get("scan_limit", 80)))),
                min_points=max(0, int(item.get("min_points", 15))),
                min_comments=max(0, int(item.get("min_comments", 0))),
            )
        )
    return result


def validate_config(config: dict) -> tuple[list[Keyword], list[FeedSpec], list[GithubSearchSpec], list[HackerNewsSpec]]:
    site = config.get("site", {})
    for key in ("name", "base_url", "publisher_name"):
        if not site.get(key):
            raise ValueError(f"Missing site.{key}")
    safe_http_url(site["base_url"])
    normalize_url_prefix(str(site.get("url_prefix", "")))
    keywords = load_keywords(config.get("keywords", []))
    feeds = load_feeds(config.get("feeds", []))
    github_searches = load_github_searches(config.get("github_discovery", []))
    hacker_news = load_hacker_news_sources(config.get("hacker_news", []))
    if not keywords:
        raise ValueError("At least one keyword is required.")
    if not any(feed.enabled for feed in feeds) and not any(source.enabled for source in github_searches) and not any(source.enabled for source in hacker_news):
        raise ValueError("At least one enabled source is required.")
    configured_terms = {keyword.term.casefold() for keyword in keywords}
    for source in [*feeds, *github_searches, *hacker_news]:
        unknown = [term for term in source.topic_terms if term.casefold() not in configured_terms]
        if unknown:
            raise ValueError(f"Source {source.name!r} references unknown topic terms: {unknown}")
    return keywords, feeds, github_searches, hacker_news


def fetch_feed(
    url: str,
    user_agent: str,
    timeout: int,
    max_bytes: int,
    retries: int,
    retry_delay: float,
) -> bytes:
    safe_http_url(url)
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent": user_agent,
                    "Accept": "application/atom+xml, application/rss+xml, application/xml, text/xml;q=0.9, */*;q=0.1",
                    "Accept-Encoding": "identity",
                },
            )
            with urllib.request.urlopen(request, timeout=timeout) as response:
                content_type = response.headers.get("Content-Type", "").lower()
                if "html" in content_type:
                    raise ValueError(f"Feed returned HTML instead of XML: {url}")
                data = response.read(max_bytes + 1)
                if len(data) > max_bytes:
                    raise ValueError(f"Feed exceeds maximum size of {max_bytes} bytes: {url}")
                return data
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(retry_delay * (attempt + 1))
    assert last_error is not None
    raise last_error


def fetch_json(
    url: str,
    user_agent: str,
    timeout: int,
    max_bytes: int,
    retries: int,
    retry_delay: float,
    headers: dict[str, str] | None = None,
) -> object:
    safe_http_url(url)
    last_error: Exception | None = None
    request_headers = {
        "User-Agent": user_agent,
        "Accept": "application/vnd.github+json, application/json;q=0.9, */*;q=0.1",
        "Accept-Encoding": "identity",
    }
    if headers:
        request_headers.update(headers)
    for attempt in range(retries + 1):
        try:
            request = urllib.request.Request(url, headers=request_headers)
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = response.read(max_bytes + 1)
                if len(data) > max_bytes:
                    raise ValueError(f"JSON response exceeds maximum size of {max_bytes} bytes: {url}")
                return json.loads(data.decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(retry_delay * (attempt + 1))
    assert last_error is not None
    raise last_error


def github_search_url(spec: GithubSearchSpec, now: dt.datetime) -> str:
    since = (now - dt.timedelta(days=spec.lookback_days)).date().isoformat()
    query = spec.query.replace("{since}", since)
    if "created:" not in query and "pushed:" not in query and "updated:" not in query:
        query += f" created:>={since}"
    if "stars:" not in query and spec.min_stars > 0:
        query += f" stars:>={spec.min_stars}"
    params = urllib.parse.urlencode(
        {
            "q": query,
            "sort": spec.sort,
            "order": spec.order,
            "per_page": spec.per_page,
        }
    )
    return f"https://api.github.com/search/repositories?{params}"


def parse_github_search_result(raw: object, spec: GithubSearchSpec, now: dt.datetime) -> list[Entry]:
    if not isinstance(raw, dict) or not isinstance(raw.get("items"), list):
        raise ValueError(f"Unexpected GitHub search response for {spec.name}")
    entries: list[Entry] = []
    for item in raw["items"]:
        if not isinstance(item, dict):
            continue
        url = str(item.get("html_url", ""))
        full_name = clean_text(str(item.get("full_name", "")))
        description = clean_text(str(item.get("description") or ""), 500)
        if not url or not full_name or item.get("archived") or item.get("disabled"):
            continue
        stars = int(item.get("stargazers_count", 0) or 0)
        if stars < spec.min_stars:
            continue
        forks = int(item.get("forks_count", 0) or 0)
        issues = int(item.get("open_issues_count", 0) or 0)
        language = clean_text(str(item.get("language") or "Unknown"))
        topics = [clean_text(str(x)) for x in item.get("topics", []) if clean_text(str(x))]
        created = parse_datetime(str(item.get("created_at", "")), now)
        pushed = parse_datetime(str(item.get("pushed_at", "")), created)
        published = created if created >= now - dt.timedelta(days=spec.lookback_days) else pushed
        popularity_boost = min(12, int(math.log2(stars + 1) * 1.6))
        freshness_days = max(0, (now - published).days)
        freshness_boost = max(0, 5 - freshness_days // 7)
        summary_parts = []
        if description:
            summary_parts.append(description)
        summary_parts.append(
            f"GitHub discovery signal: {stars:,} stars, {forks:,} forks, {issues:,} open issues; "
            f"language {language}; last pushed {pushed.date().isoformat()}."
        )
        if topics:
            summary_parts.append("Topics: " + ", ".join(topics[:10]) + ".")
        entries.append(
            Entry(
                entry_id=stable_entry_id(url, full_name),
                title=f"{full_name} — trending GitHub repository",
                url=url,
                summary=" ".join(summary_parts),
                source=spec.name,
                source_url=github_search_url(spec, now),
                category=spec.category,
                published=published,
                trust=spec.trust,
                source_boost=spec.source_boost + popularity_boost + freshness_boost,
                topic_terms=list(spec.topic_terms),
                matched_terms=[],
                score=0,
            )
        )
    return entries


def fetch_github_search(
    spec: GithubSearchSpec,
    now: dt.datetime,
    user_agent: str,
    timeout: int,
    max_bytes: int,
    retries: int,
    retry_delay: float,
    token: str,
) -> list[Entry]:
    headers = {"X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    raw = fetch_json(
        github_search_url(spec, now),
        user_agent,
        timeout,
        max_bytes,
        retries,
        retry_delay,
        headers,
    )
    return parse_github_search_result(raw, spec, now)


def hacker_news_item_url(item_id: int) -> str:
    return f"https://hacker-news.firebaseio.com/v0/item/{item_id}.json"


def fetch_hacker_news(
    spec: HackerNewsSpec,
    now: dt.datetime,
    user_agent: str,
    timeout: int,
    max_bytes: int,
    retries: int,
    retry_delay: float,
    workers: int,
) -> list[Entry]:
    list_url = f"https://hacker-news.firebaseio.com/v0/{spec.list_name}.json"
    ids_raw = fetch_json(list_url, user_agent, timeout, max_bytes, retries, retry_delay)
    if not isinstance(ids_raw, list):
        raise ValueError(f"Unexpected Hacker News list response for {spec.name}")
    ids = [int(value) for value in ids_raw[: spec.scan_limit] if isinstance(value, int)]

    def load_item(item_id: int) -> object:
        return fetch_json(
            hacker_news_item_url(item_id),
            user_agent,
            timeout,
            max_bytes,
            retries,
            retry_delay,
        )

    raw_items: list[object] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(workers, 12))) as executor:
        futures = [executor.submit(load_item, item_id) for item_id in ids]
        for future in concurrent.futures.as_completed(futures):
            try:
                raw_items.append(future.result())
            except Exception as exc:
                eprint(f"Warning: Hacker News item fetch failed: {clean_text(str(exc), 180)}")

    entries: list[Entry] = []
    for item in raw_items:
        if not isinstance(item, dict) or item.get("deleted") or item.get("dead"):
            continue
        if item.get("type") not in {"story", "job"}:
            continue
        title = clean_text(str(item.get("title") or ""))
        if not title:
            continue
        points = int(item.get("score", 0) or 0)
        comments = int(item.get("descendants", 0) or 0)
        if points < spec.min_points or comments < spec.min_comments:
            continue
        item_id = int(item.get("id", 0) or 0)
        discussion_url = f"https://news.ycombinator.com/item?id={item_id}"
        story_url = str(item.get("url") or discussion_url)
        try:
            safe_http_url(story_url)
        except ValueError:
            story_url = discussion_url
        author = clean_text(str(item.get("by") or "unknown"))
        text = clean_text(str(item.get("text") or ""), 300)
        domain = urllib.parse.urlparse(story_url).netloc or "news.ycombinator.com"
        summary = (
            (text + " " if text else "")
            + f"Hacker News signal: {points} points and {comments} comments; submitted by {author}; source {domain}. "
            + f"Discussion: {discussion_url}"
        )
        popularity_boost = min(10, points // 30) + min(4, comments // 40)
        entries.append(
            Entry(
                entry_id=stable_entry_id(discussion_url, title),
                title=title,
                url=story_url,
                summary=summary,
                source=spec.name,
                source_url=f"https://news.ycombinator.com/{'show' if spec.list_name == 'showstories' else 'news'}",
                category=spec.category,
                published=dt.datetime.fromtimestamp(int(item.get("time", now.timestamp())), tz=dt.timezone.utc),
                trust=spec.trust,
                source_boost=spec.source_boost + popularity_boost,
                topic_terms=list(spec.topic_terms),
                matched_terms=[],
                score=0,
            )
        )
    return entries


def parse_feed(xml_bytes: bytes, spec: FeedSpec, fallback_time: dt.datetime | None = None) -> list[Entry]:
    root = ET.fromstring(xml_bytes)
    nodes = [
        node for node in root.iter()
        if node.tag.rsplit("}", 1)[-1].lower() in {"item", "entry"}
    ]
    entries: list[Entry] = []
    for node in nodes:
        title = clean_text(child_text(node, ["title"]))
        url = entry_link(node)
        summary = clean_text(child_text(node, ["summary", "description", "content", "encoded"]))
        published_raw = child_text(node, ["published", "updated", "pubDate", "date"])
        if not title or not url:
            continue
        try:
            safe_http_url(url)
        except ValueError:
            continue
        entries.append(
            Entry(
                entry_id=stable_entry_id(url, title),
                title=title,
                url=url,
                summary=summary,
                source=spec.name,
                source_url=spec.url,
                category=spec.category,
                published=parse_datetime(published_raw, fallback_time),
                trust=spec.trust,
                source_boost=spec.source_boost,
                topic_terms=list(spec.topic_terms),
                matched_terms=[],
                score=0,
            )
        )
    return entries


def contains_any(haystack_folded: str, terms: Iterable[str]) -> bool:
    return any(term.casefold() in haystack_folded for term in terms if term)


def score_entry(
    entry: Entry,
    keywords: Sequence[Keyword],
    include_any: Sequence[str] = (),
    exclude_any: Sequence[str] = (),
    global_exclude_terms: Sequence[str] = (),
) -> Entry:
    title_folded = entry.title.casefold()
    summary_folded = entry.summary.casefold()
    haystack = f"{title_folded}\n{summary_folded}"

    if contains_any(haystack, list(global_exclude_terms) + list(exclude_any)):
        entry.score = -1000
        entry.matched_terms = []
        return entry
    if include_any and not contains_any(haystack, include_any):
        entry.score = -1000
        entry.matched_terms = []
        return entry

    score = entry.trust // 2 + entry.source_boost
    matched: list[str] = []
    topic_folded = {term.casefold() for term in entry.topic_terms}

    for keyword in keywords:
        keyword_score = 0
        if keyword.term.casefold() in topic_folded:
            keyword_score += keyword.priority

        title_hits = 0
        summary_hits = 0
        for alias in keyword.aliases:
            folded = alias.casefold()
            if folded in title_folded:
                title_hits += 1
            elif folded in summary_folded:
                summary_hits += 1
        if title_hits:
            keyword_score += keyword.priority * 2 + min(title_hits - 1, 2)
        if summary_hits:
            keyword_score += keyword.priority + min(summary_hits - 1, 2)

        if keyword_score:
            matched.append(keyword.term)
            score += keyword_score

    entry.matched_terms = list(dict.fromkeys(matched))
    entry.score = score
    return entry


def deduplicate(entries: Iterable[Entry]) -> list[Entry]:
    best: dict[str, Entry] = {}
    for entry in entries:
        key = normalize_url(entry.url) or re.sub(r"\W+", "", entry.title.casefold())[:160]
        current = best.get(key)
        if current is None or (entry.score, entry.published) > (current.score, current.published):
            best[key] = entry
    return list(best.values())


def select_diverse(
    entries: Sequence[Entry],
    max_items: int,
    max_items_per_source: int,
    max_items_per_category: int,
) -> list[Entry]:
    source_counts: dict[str, int] = {}
    category_counts: dict[str, int] = {}
    selected: list[Entry] = []
    for entry in sorted(entries, key=lambda item: (item.score, item.published), reverse=True):
        if source_counts.get(entry.source, 0) >= max_items_per_source:
            continue
        if category_counts.get(entry.category, 0) >= max_items_per_category:
            continue
        selected.append(entry)
        source_counts[entry.source] = source_counts.get(entry.source, 0) + 1
        category_counts[entry.category] = category_counts.get(entry.category, 0) + 1
        if len(selected) >= max_items:
            break
    return selected


def discover_phrases(entries: Sequence[Entry], seed_terms: set[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for entry in entries:
        text = f"{entry.title} {entry.summary}"
        technical = [m.group(0).strip() for m in TECH_TOKEN_RE.finditer(text)]
        words = [word for word in WORD_RE.findall(entry.title) if word.casefold() not in STOPWORDS]
        candidates: set[str] = {clean_text(value) for value in technical}
        for size in (2, 3):
            for index in range(0, len(words) - size + 1):
                phrase = " ".join(words[index:index + size]).strip()
                if 6 <= len(phrase) <= 64 and (TECH_TOKEN_RE.search(phrase) or entry.matched_terms):
                    candidates.add(phrase)
        for phrase in candidates:
            folded = phrase.casefold()
            if folded in seed_terms or folded in STOPWORDS:
                continue
            counts[phrase] = counts.get(phrase, 0) + 1
    return counts


def load_json(path: Path, default: object) -> object:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        eprint(f"Warning: unable to read {path}: {exc}")
        return default


def write_if_changed(path: Path, content: str) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    old = path.read_text(encoding="utf-8") if path.exists() else None
    if old == content:
        return False
    path.write_text(content, encoding="utf-8", newline="\n")
    return True


def absolute_url(base_url: str, path: str) -> str:
    return urllib.parse.urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))


def normalize_url_prefix(value: str) -> str:
    value = value.strip()
    if not value or value == "/":
        return ""
    if "://" in value or "?" in value or "#" in value or "\\" in value:
        raise ValueError("site.url_prefix must be a URL path such as /public")
    parts = [part for part in value.split("/") if part]
    if any(part in {".", ".."} for part in parts):
        raise ValueError("site.url_prefix cannot contain . or .. segments")
    return "/" + "/".join(parts)


def site_url_path(site: dict, path: str) -> str:
    prefix = normalize_url_prefix(str(site.get("url_prefix", "")))
    suffix = "/" + str(path).lstrip("/")
    if prefix and (suffix == prefix or suffix.startswith(prefix + "/")):
        return suffix
    return prefix + suffix


def migrate_page_path(site: dict, path: str) -> str:
    # Upgrades state written by older versions where /public was missing.
    return site_url_path(site, path)


def xml_escape(value: str) -> str:
    return html.escape(value, quote=True)


def category_label(value: str) -> str:
    return value.replace("-", " ").title()


def serialize_entry(entry: Entry) -> dict:
    return {
        "entry_id": entry.entry_id,
        "title": entry.title,
        "url": entry.url,
        "summary": entry.summary,
        "source": entry.source,
        "source_url": entry.source_url,
        "category": entry.category,
        "published": entry.published.isoformat(),
        "trust": entry.trust,
        "source_boost": entry.source_boost,
        "topic_terms": entry.topic_terms,
        "matched_terms": entry.matched_terms,
        "score": entry.score,
    }


def deserialize_entry(raw: dict) -> Entry:
    return Entry(
        entry_id=str(raw["entry_id"]),
        title=str(raw["title"]),
        url=str(raw["url"]),
        summary=str(raw.get("summary", "")),
        source=str(raw.get("source", "Unknown")),
        source_url=str(raw.get("source_url", "")),
        category=str(raw.get("category", "other")),
        published=parse_datetime(str(raw.get("published", ""))),
        trust=int(raw.get("trust", 5)),
        source_boost=int(raw.get("source_boost", 0)),
        topic_terms=list(raw.get("topic_terms", [])),
        matched_terms=list(raw.get("matched_terms", [])),
        score=int(raw.get("score", 0)),
    )


def historical_entry_ids(pages: Sequence[dict], current_date: str) -> set[str]:
    ids: set[str] = set()
    for page in pages:
        if page.get("date") == current_date:
            continue
        for item in page.get("entries", []):
            entry_id = item.get("entry_id")
            if entry_id:
                ids.add(str(entry_id))
    return ids


def update_health_record(previous: dict | None, status: str, item_count: int, error: str, now: dt.datetime) -> dict:
    previous = previous or {}
    old_status = previous.get("status")
    old_error = previous.get("last_error", "")
    old_count = int(previous.get("item_count", -1))
    changed = old_status != status or old_error != error or old_count != item_count

    if status == "ok":
        failures = 0
    else:
        failures = min(3, int(previous.get("consecutive_failures", 0)) + 1)
        changed = changed or failures != int(previous.get("consecutive_failures", 0))

    return {
        "status": status,
        "item_count": item_count,
        "consecutive_failures": failures,
        "last_error": error,
        "last_changed_at": now.isoformat() if changed else previous.get("last_changed_at", now.isoformat()),
    }


def page_title_and_description(site: dict, keywords: Sequence[Keyword], entries: Sequence[Entry], generated_at: dt.datetime) -> tuple[str, str, list[str]]:
    top_terms = list(dict.fromkeys(term for entry in entries for term in entry.matched_terms))
    top_terms = top_terms[: int(site.get("title_keyword_count", 2)) or 2]
    if not top_terms:
        top_terms = [keyword.term for keyword in keywords[:2]]
    title = f"{' & '.join(top_terms)} News — {generated_at.strftime('%B %d, %Y')}"
    description = clean_text(
        f"Curated {', '.join(top_terms)} updates for developers and security researchers. "
        f"{len(entries)} relevant releases and technical developments with original source links.",
        158,
    )
    return title, description, top_terms


def html_page(
    *,
    site: dict,
    keywords: Sequence[Keyword],
    entries: Sequence[Entry],
    discovered: Sequence[str],
    page_url: str,
    canonical_path: str,
    generated_at: dt.datetime,
) -> tuple[str, str, str]:
    language = html.escape(site.get("language", "en"))
    site_name = html.escape(site["name"])
    base_url = site["base_url"].rstrip("/")
    title, description, _ = page_title_and_description(site, keywords, entries, generated_at)

    keyword_links: list[str] = []
    keyword_by_term = {keyword.term: keyword for keyword in keywords}
    used_terms = list(dict.fromkeys(
        [term for entry in entries for term in entry.matched_terms] + list(discovered)
    ))[:14]
    for term in used_terms:
        item = keyword_by_term.get(term)
        href = item.internal_url if item else site_url_path(site, "news/")
        keyword_links.append(
            f'<a class="tag" href="{html.escape(href, quote=True)}">{html.escape(term)}</a>'
        )

    cards: list[str] = []
    item_list: list[dict] = []
    for position, entry in enumerate(entries, start=1):
        matched = ", ".join(entry.matched_terms[:3]) or "related technical tooling"
        excerpt = clean_text(entry.summary, int(site.get("excerpt_length", 320)))
        if not excerpt:
            excerpt = f"Technical update from {entry.source}. Open the original source for the complete release notes."
        cards.append(f"""
        <article class="card">
          <div class="meta">
            <span><span class="category">{html.escape(category_label(entry.category))}</span> {html.escape(entry.source)}</span>
            <time datetime="{entry.published.isoformat()}">{entry.published.strftime('%Y-%m-%d')}</time>
          </div>
          <h2><a href="{html.escape(entry.url, quote=True)}" rel="noopener noreferrer">{html.escape(entry.title)}</a></h2>
          <p>{html.escape(excerpt)}</p>
          <p class="why"><strong>Why it matters:</strong> Relevant to {html.escape(matched)}.</p>
          <a class="source" href="{html.escape(entry.url, quote=True)}" rel="noopener noreferrer">Read the original source →</a>
        </article>""")
        item_list.append({"@type": "ListItem", "position": position, "url": entry.url, "name": entry.title})

    schema = {
        "@context": "https://schema.org",
        "@type": "CollectionPage",
        "name": title,
        "description": description,
        "url": page_url,
        "dateModified": generated_at.isoformat(),
        "isPartOf": {"@type": "WebSite", "name": site["name"], "url": base_url + "/"},
        "mainEntity": {"@type": "ItemList", "numberOfItems": len(entries), "itemListElement": item_list},
        "publisher": {
            "@type": "Organization",
            "name": site.get("publisher_name", site["name"]),
            "url": base_url + "/",
            "logo": {"@type": "ImageObject", "url": site.get("logo_url", base_url + "/logo.png")},
        },
    }

    page = f"""<!doctype html>
<html lang="{language}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{html.escape(title)} | {site_name}</title>
  <meta name="description" content="{html.escape(description, quote=True)}">
  <link rel="canonical" href="{html.escape(page_url, quote=True)}">
  <link rel="alternate" type="application/rss+xml" title="{site_name} News RSS" href="{absolute_url(base_url, site_url_path(site, 'news.xml'))}">
  <meta property="og:type" content="website">
  <meta property="og:title" content="{html.escape(title, quote=True)}">
  <meta property="og:description" content="{html.escape(description, quote=True)}">
  <meta property="og:url" content="{html.escape(page_url, quote=True)}">
  <script type="application/ld+json">{json.dumps(schema, ensure_ascii=False).replace('</', '<\\/')}</script>
  <style>
    :root {{ color-scheme:dark; --bg:#080b12; --panel:#111725; --text:#eef3ff; --muted:#9da9bd; --line:#263146; --accent:#72a7ff; }}
    * {{ box-sizing:border-box; }} body {{ margin:0; background:var(--bg); color:var(--text); font:16px/1.65 system-ui,-apple-system,Segoe UI,sans-serif; }}
    a {{ color:var(--accent); }} .wrap {{ width:min(1080px,calc(100% - 32px)); margin:auto; }}
    header {{ padding:64px 0 28px; border-bottom:1px solid var(--line); }} .eyebrow {{ color:var(--accent); font-weight:700; letter-spacing:.08em; text-transform:uppercase; }}
    h1 {{ margin:.25em 0; font-size:clamp(2rem,6vw,4.3rem); line-height:1.04; }} .lede {{ max-width:820px; color:var(--muted); font-size:1.08rem; }}
    .tags {{ display:flex; flex-wrap:wrap; gap:8px; margin-top:22px; }} .tag {{ text-decoration:none; padding:6px 11px; border:1px solid var(--line); border-radius:999px; background:#0d1320; }}
    main {{ display:grid; gap:18px; padding:32px 0 64px; }} .card {{ padding:24px; border:1px solid var(--line); border-radius:18px; background:var(--panel); }}
    .card h2 {{ margin:.45em 0; line-height:1.25; }} .card h2 a {{ color:var(--text); text-decoration:none; }} .card h2 a:hover {{ color:var(--accent); }}
    .meta {{ display:flex; justify-content:space-between; gap:12px; color:var(--muted); font-size:.9rem; }} .category {{ color:#c6d6f3; border:1px solid var(--line); border-radius:6px; padding:2px 6px; margin-right:6px; }}
    .why {{ color:#c6d1e5; }} .source {{ font-weight:700; text-decoration:none; }} footer {{ padding:28px 0 50px; color:var(--muted); border-top:1px solid var(--line); }}
  </style>
</head>
<body>
  <header><div class="wrap">
    <div class="eyebrow">{site_name} Technical News</div>
    <h1>{html.escape(title)}</h1>
    <p class="lede">{html.escape(description)} This page links to original publishers, uses short excerpts, and does not reproduce complete articles.</p>
    <div class="tags">{''.join(keyword_links)}</div>
  </div></header>
  <main class="wrap">{''.join(cards)}</main>
  <footer><div class="wrap">
    <a href="{html.escape(site.get('home_path', '/'), quote=True)}">{site_name}</a> · <a href="{site_url_path(site, 'news/')}">News archive</a> · Updated {generated_at.strftime('%Y-%m-%d %H:%M UTC')}
  </div></footer>
</body>
</html>
"""
    return page, title, description


def render_hot_page(site: dict, entries: Sequence[Entry], generated_at: dt.datetime) -> str:
    base_url = site["base_url"].rstrip("/")
    archive_path = site_url_path(site, "news/")
    hot_path = site_url_path(site, "news/github-hot.html")
    sources_path = site_url_path(site, "news/sources.html")
    hot_url = absolute_url(base_url, hot_path)
    cards: list[str] = []
    for entry in entries:
        cards.append(
            f"""<article><div class="meta"><span>{html.escape(category_label(entry.category))}</span><time datetime="{entry.published.isoformat()}">{entry.published.strftime('%Y-%m-%d')}</time></div><h2><a href="{html.escape(entry.url, quote=True)}" rel="noopener noreferrer">{html.escape(entry.title)}</a></h2><p>{html.escape(clean_text(entry.summary, 420))}</p><p class="tags">{html.escape(', '.join(entry.matched_terms[:4]))}</p></article>"""
        )
    description = (
        "Current GitHub repositories and community stories discovered from focused IL2CPP, Unity, "
        "reverse engineering, decompiler, mobile analysis and WebAssembly searches."
    )
    schema = {
        "@context": "https://schema.org",
        "@type": "CollectionPage",
        "name": "Trending GitHub Reverse Engineering Projects",
        "description": description,
        "url": hot_url,
        "dateModified": generated_at.isoformat(),
        "mainEntity": {
            "@type": "ItemList",
            "numberOfItems": len(entries),
            "itemListElement": [
                {"@type": "ListItem", "position": index, "url": entry.url, "name": entry.title}
                for index, entry in enumerate(entries, start=1)
            ],
        },
    }
    return f"""<!doctype html><html lang="{html.escape(site.get('language', 'en'))}"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Trending GitHub Reverse Engineering Projects | {html.escape(site['name'])}</title>
<meta name="description" content="{html.escape(description, quote=True)}"><link rel="canonical" href="{hot_url}">
<script type="application/ld+json">{json.dumps(schema, ensure_ascii=False).replace('</', '<\\/')}</script>
<style>:root{{color-scheme:dark;--bg:#080b12;--panel:#111725;--text:#eef3ff;--muted:#9da9bd;--line:#263146;--accent:#72a7ff}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);font:16px/1.65 system-ui,sans-serif}}main{{width:min(1080px,calc(100% - 32px));margin:auto;padding:52px 0}}a{{color:var(--accent)}}h1{{font-size:clamp(2.2rem,6vw,4.4rem);line-height:1.04}}.lead,.meta,.tags{{color:var(--muted)}}.grid{{display:grid;gap:16px}}article{{padding:22px;border:1px solid var(--line);border-radius:16px;background:var(--panel)}}article h2{{margin:.35em 0;line-height:1.25}}article h2 a{{color:var(--text);text-decoration:none}}.meta{{display:flex;justify-content:space-between;gap:12px;font-size:.9rem}}nav{{display:flex;gap:16px;flex-wrap:wrap}}</style>
</head><body><main><nav><a href="/">← {html.escape(site['name'])}</a><a href="{archive_path}">News archive</a><a href="{sources_path}">Sources</a></nav><h1>Trending GitHub & Community Signals</h1><p class="lead">{html.escape(description)} Updated {generated_at.strftime('%Y-%m-%d %H:%M UTC')}.</p><div class="grid">{''.join(cards)}</div></main></body></html>"""

def render_sources_page(
    site: dict,
    feeds: Sequence[FeedSpec],
    github_searches: Sequence[GithubSearchSpec],
    hacker_news: Sequence[HackerNewsSpec],
    source_health: dict[str, dict],
    generated_at: dt.datetime,
) -> str:
    rows: list[str] = []
    all_sources: list[tuple[str, str, str, str, bool]] = []
    for source in feeds:
        all_sources.append((source.name, "RSS / Atom", source.category, source.url, source.enabled))
    for source in github_searches:
        all_sources.append((source.name, "GitHub Search API", source.category, "https://github.com/search?type=repositories", source.enabled))
    for source in hacker_news:
        all_sources.append((source.name, "Hacker News API", source.category, "https://news.ycombinator.com/", source.enabled))
    for name, kind, category, url, enabled in sorted(all_sources, key=lambda item: (item[2], item[0].casefold())):
        record = source_health.get(name, {})
        status = str(record.get("status", "pending" if enabled else "disabled"))
        count = int(record.get("item_count", 0) or 0)
        error = clean_text(str(record.get("last_error", "")), 120)
        detail = f"{count} fetched" if status == "ok" else (error or status)
        rows.append(
            f'<tr><td><a href="{html.escape(url, quote=True)}" rel="noopener noreferrer">{html.escape(name)}</a></td><td>{html.escape(kind)}</td><td>{html.escape(category_label(category))}</td><td class="status {html.escape(status)}">{html.escape(status)}</td><td>{html.escape(detail)}</td></tr>'
        )
    base_url = site["base_url"].rstrip("/")
    archive_path = site_url_path(site, "news/")
    hot_path = site_url_path(site, "news/github-hot.html")
    sources_path = site_url_path(site, "news/sources.html")
    sources_url = absolute_url(base_url, sources_path)
    description = "Transparent list of feeds and discovery APIs used by the CPP2IL technical news curator."
    return f"""<!doctype html><html lang="{html.escape(site.get('language', 'en'))}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>News Sources | {html.escape(site['name'])}</title><meta name="description" content="{html.escape(description, quote=True)}"><link rel="canonical" href="{sources_url}"><style>:root{{color-scheme:dark;--bg:#080b12;--text:#eef3ff;--muted:#9da9bd;--line:#263146;--accent:#72a7ff}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);font:15px/1.55 system-ui,sans-serif}}main{{width:min(1180px,calc(100% - 28px));margin:auto;padding:48px 0}}a{{color:var(--accent)}}h1{{font-size:clamp(2rem,6vw,4rem)}}p{{color:var(--muted)}}.table{{overflow:auto;border:1px solid var(--line);border-radius:14px}}table{{width:100%;border-collapse:collapse;min-width:850px}}th,td{{padding:13px 14px;text-align:left;border-bottom:1px solid var(--line)}}th{{position:sticky;top:0;background:#111725}}.status.ok{{color:#74d99f}}.status.error{{color:#ff8b8b}}.status.disabled{{color:#9da9bd}}nav{{display:flex;gap:16px;flex-wrap:wrap}}</style></head><body><main><nav><a href="/">← {html.escape(site['name'])}</a><a href="{archive_path}">News archive</a><a href="{hot_path}">GitHub hot</a></nav><h1>News Sources</h1><p>{html.escape(description)} Last checked {generated_at.strftime('%Y-%m-%d %H:%M UTC')}.</p><div class="table"><table><thead><tr><th>Source</th><th>Type</th><th>Category</th><th>Status</th><th>Latest check</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div></main></body></html>"""

def render_index(site: dict, pages: Sequence[dict]) -> str:
    base_url = site["base_url"].rstrip("/")
    archive_path = site_url_path(site, "news/")
    hot_path = site_url_path(site, "news/github-hot.html")
    sources_path = site_url_path(site, "news/sources.html")
    rss_path = site_url_path(site, "news.xml")
    rows = []
    for page in pages[:90]:
        page_path = migrate_page_path(site, str(page["path"]))
        rows.append(
            f'<li><a href="{html.escape(page_path, quote=True)}">{html.escape(page["title"])}</a>'
            f'<span><small>{int(page.get("item_count", 0))} items</small> <time datetime="{html.escape(page["date"])}">{html.escape(page["date"])}</time></span></li>'
        )
    description = html.escape(site.get("description", "Curated technical news."))
    schema = {
        "@context": "https://schema.org", "@type": "CollectionPage",
        "name": f'{site["name"]} Technical News', "description": site.get("description", "Curated technical news."),
        "url": absolute_url(base_url, archive_path),
    }
    return f"""<!doctype html>
<html lang="{html.escape(site.get('language', 'en'))}"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Technical News | {html.escape(site['name'])}</title><meta name="description" content="{description}">
<link rel="canonical" href="{absolute_url(base_url, archive_path)}"><link rel="alternate" type="application/rss+xml" href="{absolute_url(base_url, rss_path)}">
<script type="application/ld+json">{json.dumps(schema, ensure_ascii=False)}</script>
<style>body{{margin:0;background:#080b12;color:#eef3ff;font:16px/1.6 system-ui,sans-serif}}main{{width:min(960px,calc(100% - 32px));margin:auto;padding:64px 0}}a{{color:#85b2ff}}h1{{font-size:clamp(2.2rem,7vw,4.8rem);line-height:1}}p,small{{color:#a7b2c5}}ul{{list-style:none;padding:0;border-top:1px solid #263146}}li{{display:flex;justify-content:space-between;gap:18px;padding:18px 0;border-bottom:1px solid #263146}}li span{{display:flex;gap:12px;white-space:nowrap}}time{{color:#8d99ac}}</style>
</head><body><main><a href="/">← {html.escape(site['name'])}</a><p><a href="{hot_path}">Trending GitHub</a> · <a href="{sources_path}">Sources & health</a> · <a href="{rss_path}">RSS</a></p><h1>Technical News</h1><p>{description}</p><ul>{''.join(rows)}</ul></main></body></html>
"""

def render_rss(site: dict, pages: Sequence[dict], generated_at: dt.datetime) -> str:
    base_url = site["base_url"].rstrip("/")
    archive_url = absolute_url(base_url, site_url_path(site, "news/"))
    items = []
    for page in pages[:30]:
        path = migrate_page_path(site, str(page["path"]))
        url = absolute_url(base_url, path)
        page_date = dt.datetime.fromisoformat(page["date"]).replace(tzinfo=dt.timezone.utc)
        items.append(f"""<item><title>{xml_escape(page['title'])}</title><link>{xml_escape(url)}</link><guid isPermaLink="true">{xml_escape(url)}</guid><pubDate>{email.utils.format_datetime(page_date)}</pubDate><description>{xml_escape(page.get('description', ''))}</description></item>""")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>{xml_escape(site['name'])} Technical News</title><link>{xml_escape(archive_url)}</link><description>{xml_escape(site.get('description', 'Curated technical news.'))}</description><lastBuildDate>{email.utils.format_datetime(generated_at)}</lastBuildDate>{''.join(items)}</channel></rss>
"""

def render_standard_sitemap(site: dict, pages: Sequence[dict]) -> str:
    base_url = site["base_url"].rstrip("/")
    urls = [
        f"<url><loc>{xml_escape(absolute_url(base_url, site_url_path(site, 'news/')))}</loc></url>",
        f"<url><loc>{xml_escape(absolute_url(base_url, site_url_path(site, 'news/github-hot.html')))}</loc></url>",
        f"<url><loc>{xml_escape(absolute_url(base_url, site_url_path(site, 'news/sources.html')))}</loc></url>",
    ]
    for page in pages[:365]:
        path = migrate_page_path(site, str(page["path"]))
        loc = absolute_url(base_url, path)
        urls.append(f"<url><loc>{xml_escape(loc)}</loc><lastmod>{xml_escape(page.get('updated_at', page['date']))}</lastmod></url>")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{''.join(urls)}</urlset>
"""

def render_google_news_sitemap(site: dict, pages: Sequence[dict], now: dt.datetime) -> str:
    base_url = site["base_url"].rstrip("/")
    recent_cutoff = (now - dt.timedelta(days=2)).date()
    urls = []
    for page in pages:
        page_date = dt.date.fromisoformat(page["date"])
        if page_date < recent_cutoff:
            continue
        loc = absolute_url(base_url, migrate_page_path(site, str(page["path"])))
        urls.append(f"""<url><loc>{xml_escape(loc)}</loc><news:news><news:publication><news:name>{xml_escape(site['publisher_name'])}</news:name><news:language>{xml_escape(site.get('language', 'en'))}</news:language></news:publication><news:publication_date>{xml_escape(page['date'])}</news:publication_date><news:title>{xml_escape(page['title'])}</news:title></news:news></url>""")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">{''.join(urls)}</urlset>
"""


def set_github_output(name: str, value: str) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if output_path:
        with open(output_path, "a", encoding="utf-8") as handle:
            handle.write(f"{name}={value}\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--config", required=True)
    parser.add_argument("--validate-config", action="store_true")
    args = parser.parse_args()

    workspace = Path(args.workspace).resolve()
    config_path = (workspace / args.config).resolve()
    if workspace not in config_path.parents:
        raise ValueError("Config must be inside the repository workspace.")
    raw_config = json.loads(config_path.read_text(encoding="utf-8"))
    keywords, feeds, github_searches, hacker_news_sources = validate_config(raw_config)
    if args.validate_config:
        enabled_feeds = sum(source.enabled for source in feeds)
        enabled_github = sum(source.enabled for source in github_searches)
        enabled_hn = sum(source.enabled for source in hacker_news_sources)
        print(
            f"Config valid: {len(keywords)} keywords, {enabled_feeds} feeds, "
            f"{enabled_github} GitHub discovery queries, {enabled_hn} Hacker News sources."
        )
        return 0

    site = dict(raw_config["site"])
    generation = raw_config.get("generation", {})
    site["title_keyword_count"] = int(generation.get("page_title_keyword_count", 2))
    site["excerpt_length"] = int(generation.get("excerpt_length", 320))

    output_dir = (workspace / site.get("output_dir", ".")).resolve()
    if output_dir != workspace and workspace not in output_dir.parents:
        raise ValueError("site.output_dir must remain inside the repository workspace")
    news_dir = output_dir / site.get("news_path", "news")
    seo_dir = workspace / "seo"
    state_path = seo_dir / "news-state.json"
    discovered_path = seo_dir / "discovered-keywords.json"
    health_path = seo_dir / "source-health.json"

    now = dt.datetime.now(dt.timezone.utc)
    today = now.date().isoformat()
    cutoff = now - dt.timedelta(days=int(generation.get("max_age_days", 45)))
    timeout = int(generation.get("request_timeout_seconds", 20))
    user_agent = generation.get("user_agent", "SEO-News-Generator/3.0")
    max_feed_bytes = int(generation.get("max_feed_bytes", 2_000_000))
    max_json_bytes = int(generation.get("max_json_bytes", 5_000_000))
    retries = int(generation.get("feed_fetch_retries", 1))
    retry_delay = float(generation.get("feed_retry_delay_seconds", 1.5))
    source_workers = max(1, min(20, int(generation.get("source_fetch_workers", 10))))
    hn_workers = max(1, min(12, int(generation.get("hacker_news_workers", 8))))
    min_score = int(generation.get("min_score", 6))
    max_items = int(generation.get("max_items", 24))
    min_items = int(generation.get("min_items", 1))
    min_new_items = int(generation.get("min_new_items", 1))
    max_per_source = int(generation.get("max_items_per_source", 3))
    max_per_category = int(generation.get("max_items_per_category", 5))
    hot_page_max_items = int(generation.get("hot_page_max_items", 30))
    global_excludes = [str(x) for x in generation.get("global_exclude_terms", [])]
    github_token = os.environ.get("GITHUB_API_TOKEN") or os.environ.get("GITHUB_TOKEN", "")

    previous_health = load_json(health_path, {"sources": {}})
    previous_sources = previous_health.get("sources", {}) if isinstance(previous_health, dict) else {}
    source_health: dict[str, dict] = {}
    all_candidates: list[Entry] = []
    hot_candidates: list[Entry] = []
    source_errors: list[str] = []
    success_count = 0

    def curate(
        entries: Sequence[Entry],
        include_any: Sequence[str],
        exclude_any: Sequence[str],
        max_source_items: int,
    ) -> list[Entry]:
        relevant: list[Entry] = []
        for entry in entries:
            if entry.published < cutoff:
                continue
            scored = score_entry(entry, keywords, include_any, exclude_any, global_excludes)
            if scored.score >= min_score and scored.matched_terms:
                relevant.append(scored)
        relevant.sort(key=lambda item: (item.score, item.published), reverse=True)
        return relevant[:max_source_items]

    # RSS/Atom is fetched concurrently because this project intentionally monitors
    # many independent official repositories and blogs. Each failure remains isolated.
    for spec in feeds:
        if not spec.enabled:
            source_health[spec.name] = update_health_record(previous_sources.get(spec.name), "disabled", 0, "", now)

    enabled_feeds = [spec for spec in feeds if spec.enabled]

    def process_feed(spec: FeedSpec) -> tuple[FeedSpec, list[Entry], list[Entry], str]:
        try:
            xml_bytes = fetch_feed(spec.url, user_agent, timeout, max_feed_bytes, retries, retry_delay)
            parsed = parse_feed(xml_bytes, spec, now)
            relevant = curate(parsed, spec.include_any, spec.exclude_any, spec.max_items)
            return spec, parsed, relevant, ""
        except Exception as exc:
            return spec, [], [], clean_text(str(exc), 300)

    with concurrent.futures.ThreadPoolExecutor(max_workers=min(source_workers, max(1, len(enabled_feeds)))) as executor:
        futures = [executor.submit(process_feed, spec) for spec in enabled_feeds]
        for future in concurrent.futures.as_completed(futures):
            spec, parsed, relevant, error = future.result()
            if error:
                source_errors.append(f"{spec.name}: {error}")
                source_health[spec.name] = update_health_record(previous_sources.get(spec.name), "error", 0, error, now)
                eprint(f"Warning: {spec.name}: {error}")
                continue
            all_candidates.extend(relevant)
            success_count += 1
            source_health[spec.name] = update_health_record(previous_sources.get(spec.name), "ok", len(parsed), "", now)
            print(f"Fetched {len(parsed):>3} entries, kept {len(relevant):>2}: {spec.name}")

    # GitHub does not provide a public API for the personalized home-page recommendations.
    # Focused repository search is used as a stable, authenticated trending approximation.
    for spec in github_searches:
        if not spec.enabled:
            source_health[spec.name] = update_health_record(previous_sources.get(spec.name), "disabled", 0, "", now)
            continue
        try:
            parsed = fetch_github_search(
                spec,
                now,
                user_agent,
                timeout,
                max_json_bytes,
                retries,
                retry_delay,
                github_token,
            )
            relevant = curate(parsed, spec.include_any, spec.exclude_any, spec.max_items)
            all_candidates.extend(relevant)
            hot_candidates.extend(relevant)
            success_count += 1
            source_health[spec.name] = update_health_record(previous_sources.get(spec.name), "ok", len(parsed), "", now)
            print(f"GitHub search returned {len(parsed):>3}, kept {len(relevant):>2}: {spec.name}")
        except Exception as exc:
            error = clean_text(str(exc), 300)
            source_errors.append(f"{spec.name}: {error}")
            source_health[spec.name] = update_health_record(previous_sources.get(spec.name), "error", 0, error, now)
            eprint(f"Warning: {spec.name}: {error}")

    for spec in hacker_news_sources:
        if not spec.enabled:
            source_health[spec.name] = update_health_record(previous_sources.get(spec.name), "disabled", 0, "", now)
            continue
        try:
            parsed = fetch_hacker_news(
                spec,
                now,
                user_agent,
                timeout,
                max_json_bytes,
                retries,
                retry_delay,
                hn_workers,
            )
            relevant = curate(parsed, spec.include_any, spec.exclude_any, spec.max_items)
            all_candidates.extend(relevant)
            hot_candidates.extend(relevant)
            success_count += 1
            source_health[spec.name] = update_health_record(previous_sources.get(spec.name), "ok", len(parsed), "", now)
            print(f"Hacker News returned {len(parsed):>3}, kept {len(relevant):>2}: {spec.name}")
        except Exception as exc:
            error = clean_text(str(exc), 300)
            source_errors.append(f"{spec.name}: {error}")
            source_health[spec.name] = update_health_record(previous_sources.get(spec.name), "error", 0, error, now)
            eprint(f"Warning: {spec.name}: {error}")

    health_document = {
        "version": 2,
        "last_checked_at": now.isoformat(),
        "summary": {
            "configured": len(feeds) + len(github_searches) + len(hacker_news_sources),
            "successful": success_count,
            "failed": len(source_errors),
        },
        "sources": source_health,
    }
    write_if_changed(health_path, json.dumps(health_document, ensure_ascii=False, indent=2) + "\n")

    state = load_json(state_path, {"version": 3, "pages": []})
    if not isinstance(state, dict):
        state = {"version": 3, "pages": []}
    pages = list(state.get("pages", []))
    for page in pages:
        if page.get("path"):
            page["path"] = migrate_page_path(site, str(page["path"]))

    # These evergreen pages are refreshed even when there is not enough new material
    # for a new daily digest.
    hot_selected = select_diverse(
        deduplicate(hot_candidates),
        hot_page_max_items,
        max_items_per_source=max(3, max_per_source),
        max_items_per_category=max(10, max_per_category),
    )
    if hot_selected:
        write_if_changed(news_dir / "github-hot.html", render_hot_page(site, hot_selected, now))
    write_if_changed(
        news_dir / "sources.html",
        render_sources_page(site, feeds, github_searches, hacker_news_sources, source_health, now),
    )
    write_if_changed(news_dir / "index.html", render_index(site, pages))
    write_if_changed(output_dir / "sitemap-news.xml", render_standard_sitemap(site, pages))

    today_page = next((page for page in pages if page.get("date") == today), None)
    existing_today = [deserialize_entry(item) for item in today_page.get("entries", [])] if today_page else []
    previous_ids = historical_entry_ids(pages, today)
    existing_ids = {entry.entry_id for entry in existing_today}

    deduped_candidates = deduplicate(all_candidates)
    fresh_candidates = [entry for entry in deduped_candidates if entry.entry_id not in previous_ids and entry.entry_id not in existing_ids]
    fresh_candidates.sort(key=lambda item: (item.score, item.published), reverse=True)

    if not today_page and len(fresh_candidates) < min_new_items:
        print(f"Only {len(fresh_candidates)} new relevant entries found; minimum is {min_new_items}. Daily page not generated.")
        set_github_output("generated_count", "0")
        set_github_output("new_item_count", str(len(fresh_candidates)))
        set_github_output("source_success_count", str(success_count))
        set_github_output("source_failure_count", str(len(source_errors)))
        return 2 if success_count == 0 else 0

    merged_map = {entry.entry_id: entry for entry in existing_today}
    for entry in fresh_candidates:
        merged_map[entry.entry_id] = entry
    selected = select_diverse(list(merged_map.values()), max_items, max_per_source, max_per_category)
    selected_ids = {entry.entry_id for entry in selected}
    new_item_count = len(selected_ids - existing_ids)

    if len(selected) < min_items:
        print(f"Only {len(selected)} curated entries available; minimum is {min_items}. Daily page not generated.")
        return 0
    if today_page and new_item_count == 0:
        print("No new publishable entries. Evergreen GitHub/source pages were still refreshed.")
        set_github_output("generated_count", str(len(selected)))
        set_github_output("new_item_count", "0")
        set_github_output("page_path", str(today_page.get("path", "")))
        set_github_output("source_success_count", str(success_count))
        set_github_output("source_failure_count", str(len(source_errors)))
        return 0

    seed_folded = {alias.casefold() for keyword in keywords for alias in keyword.aliases}
    phrase_counts = discover_phrases(selected, seed_folded)
    old_discovered = load_json(discovered_path, {"keywords": {}})
    store = old_discovered.get("keywords", {}) if isinstance(old_discovered, dict) else {}
    for phrase, count in phrase_counts.items():
        record = store.get(phrase, {})
        store[phrase] = {
            "count": int(record.get("count", 0)) + count,
            "first_seen": record.get("first_seen", today),
            "last_seen": today,
        }
    min_occurrences = int(generation.get("min_discovered_occurrences", 2))
    max_discovered = int(generation.get("max_discovered_keywords", 60))
    ranked_discovered = sorted(
        ((name, record) for name, record in store.items() if int(record.get("count", 0)) >= min_occurrences),
        key=lambda item: (int(item[1].get("count", 0)), item[1].get("last_seen", ""), item[0].casefold()),
        reverse=True,
    )[:max_discovered]
    discovered_document = {"version": 3, "keywords": dict(ranked_discovered)}

    slug_prefix = slugify(str(generation.get("daily_slug", "unity-il2cpp-reverse-engineering-news")))
    filename = f"{today}-{slug_prefix}.html"
    relative_path = site_url_path(site, f"{site.get('news_path', 'news').strip('/')}/{filename}")
    page_url = absolute_url(site["base_url"], relative_path)
    page_html, title, description = html_page(
        site=site,
        keywords=keywords,
        entries=selected,
        discovered=[name for name, _ in ranked_discovered[:12]],
        page_url=page_url,
        canonical_path=relative_path,
        generated_at=now,
    )
    page_file = news_dir / filename
    write_if_changed(page_file, page_html)

    page_record = {
        "date": today,
        "path": relative_path,
        "title": title,
        "description": description,
        "item_count": len(selected),
        "new_item_count": new_item_count,
        "updated_at": now.isoformat(),
        "entries": [serialize_entry(entry) for entry in selected],
    }
    pages = [page for page in pages if page.get("date") != today]
    pages.append(page_record)
    pages.sort(key=lambda page: (page.get("date", ""), page.get("updated_at", "")), reverse=True)
    pages = pages[:365]
    state_document = {
        "version": 3,
        "last_generated_at": now.isoformat(),
        "source_errors": source_errors,
        "pages": pages,
    }

    write_if_changed(news_dir / "index.html", render_index(site, pages))
    write_if_changed(output_dir / "news.xml", render_rss(site, pages, now))
    write_if_changed(output_dir / "sitemap-news.xml", render_standard_sitemap(site, pages))
    if bool(generation.get("google_news_sitemap", False)):
        write_if_changed(output_dir / "sitemap-google-news.xml", render_google_news_sitemap(site, pages, now))
    write_if_changed(discovered_path, json.dumps(discovered_document, ensure_ascii=False, indent=2) + "\n")
    write_if_changed(state_path, json.dumps(state_document, ensure_ascii=False, indent=2) + "\n")

    print(f"Generated {len(selected)} curated entries ({new_item_count} new): {page_file.relative_to(workspace)}")
    set_github_output("generated_count", str(len(selected)))
    set_github_output("new_item_count", str(new_item_count))
    set_github_output("page_path", str(page_file.relative_to(workspace)))
    set_github_output("source_success_count", str(success_count))
    set_github_output("source_failure_count", str(len(source_errors)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
