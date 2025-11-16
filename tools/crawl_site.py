#!/usr/bin/env python3
import os
import re
import sys
import time
import json
import hashlib
import urllib.parse
import datetime
import collections
import xml.etree.ElementTree as ET
import requests
import trafilatura
from typing import Optional, Callable, Dict, Any, Iterable, List
from urllib import robotparser
from bs4 import BeautifulSoup


UserAgent = "IntelliBot/1.0"
ProgressCallback = Optional[Callable[[str, str], None]]


def _notify(callback: ProgressCallback, kind: str, message: str) -> None:
  if callback:
    callback(kind, message)


def _clean_text(html: str, url: str) -> str:
  text = trafilatura.extract(
    html,
    url=url,
    output_format="txt",
    include_links=False
  ) or ""
  if text.strip():
    return _normalize_whitespace(text)
  soup = BeautifulSoup(html, "html.parser")
  for node in soup(["script", "style", "noscript"]):
    node.decompose()
  text = soup.get_text(" ", strip=True)
  return _normalize_whitespace(text)


def _normalize_whitespace(text: str) -> str:
  text = re.sub(r"[ \t]+", " ", text)
  text = re.sub(r"\s+\n", "\n", text)
  return text.strip()


def _save_document(out_dir: str, payload: Dict[str, Any]) -> None:
  os.makedirs(out_dir, exist_ok=True)
  content_hash = payload.get("content_hash")
  hash_prefix = content_hash[:16] if content_hash else hashlib.sha1(
    payload["url"].encode()
  ).hexdigest()[:16]
  path = os.path.join(out_dir, f"{hash_prefix}.json")
  with open(path, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, ensure_ascii=False, indent=2)


def _canonical_url(soup: BeautifulSoup, url: str) -> str:
  link = soup.find("link", {"rel": "canonical"})
  if not link or not link.get("href"):
    return url
  candidate = urllib.parse.urljoin(url, link["href"])
  return candidate.strip() or url


def _collect_headings(soup: BeautifulSoup) -> Dict[str, List[str]]:
  headings: Dict[str, List[str]] = {"h1": [], "h2": [], "h3": []}
  for level in headings.keys():
    for node in soup.find_all(level):
      text = _normalize_whitespace(node.get_text(" ", strip=True))
      if text:
        headings[level].append(text)
  return headings


def _allowed_url(parser: Optional[robotparser.RobotFileParser], url: str) -> bool:
  if not parser:
    return True
  try:
    return parser.can_fetch(UserAgent, url)
  except Exception:
    return True


def _load_robot_parser(start_url: str, timeout: int) -> Optional[robotparser.RobotFileParser]:
  parsed = urllib.parse.urlparse(start_url)
  robots_url = urllib.parse.urlunparse((parsed.scheme, parsed.netloc, "/robots.txt", "", "", ""))
  parser = robotparser.RobotFileParser()
  try:
    parser.set_url(robots_url)
    parser.read()
    return parser
  except Exception:
    return None


def _iter_sitemap_urls(parser: Optional[robotparser.RobotFileParser], timeout: int) -> Iterable[str]:
  if not parser:
    return []
  sitemap_urls = parser.site_maps() or []
  for sitemap_url in sitemap_urls:
    try:
      response = requests.get(sitemap_url, headers={"User-Agent": UserAgent}, timeout=timeout)
      if response.status_code != 200:
        continue
      for url in _parse_sitemap(response.text):
        yield url
    except requests.RequestException:
      continue


def _parse_sitemap(data: str) -> Iterable[str]:
  try:
    tree = ET.fromstring(data)
  except ET.ParseError:
    return []
  namespace = ""
  if tree.tag.startswith("{"):
    namespace = tree.tag.split("}", 1)[0] + "}"
  urls = []
  for elem in tree.findall(f"{namespace}url/{namespace}loc"):
    if elem.text:
      urls.append(elem.text.strip())
  return urls


def _same_host(candidate: str, host: str) -> bool:
  parsed = urllib.parse.urlparse(candidate)
  return parsed.netloc == host and parsed.scheme in ("http", "https")


def _normalize_url(url: str, base: str) -> Optional[str]:
  merged = urllib.parse.urljoin(base, url.split("#")[0])
  parsed = urllib.parse.urlparse(merged)
  if not parsed.scheme or not parsed.netloc:
    return None
  return urllib.parse.urlunparse(
    (parsed.scheme, parsed.netloc, parsed.path.rstrip("/") or "/", "", "", "")
  )


def crawl_site(
  start_url: str,
  max_pages: int = 500,
  timeout: int = 12,
  progress_callback: ProgressCallback = None,
  output_dir: str = "kb/raw",
  include_sitemaps: bool = True,
  respect_robots: bool = True
) -> Dict[str, Any]:
  """Crawl a website and extract structured page metadata."""
  parsed_start = urllib.parse.urlparse(start_url)
  allowed_host = parsed_start.netloc
  if not allowed_host:
    raise ValueError(f"Invalid start URL: {start_url}")

  parser = _load_robot_parser(start_url, timeout) if respect_robots else None
  queue = collections.deque()
  seen: Dict[str, str] = {}

  start_normalized = _normalize_url(start_url, start_url)
  if start_normalized:
    queue.append(start_normalized)

  if include_sitemaps:
    for sitemap_url in _iter_sitemap_urls(parser, timeout):
      normalized = _normalize_url(sitemap_url, sitemap_url)
      if normalized and _same_host(normalized, allowed_host):
        queue.append(normalized)

  stored_pages = 0
  crawled = []

  _notify(progress_callback, "info", f"Starting crawl of {start_url} (max {max_pages} pages)")

  while queue and stored_pages < max_pages:
    url = queue.popleft()
    if url in seen:
      continue
    seen[url] = "queued"

    if respect_robots and not _allowed_url(parser, url):
      _notify(progress_callback, "warning", f"Skipped {url} (robots.txt)")
      continue

    try:
      response = requests.get(url, headers={"User-Agent": UserAgent}, timeout=timeout)
    except requests.RequestException as exc:
      _notify(progress_callback, "warning", f"Failed to fetch {url}: {exc}")
      continue

    if response.status_code != 200:
      _notify(progress_callback, "warning", f"Skipped {url}: HTTP {response.status_code}")
      continue

    content_type = response.headers.get("Content-Type", "")
    if "text/html" not in content_type:
      _notify(progress_callback, "info", f"Ignored {url}: unsupported content-type {content_type}")
      continue

    html = response.text
    soup = BeautifulSoup(html, "html.parser")
    canonical = _canonical_url(soup, url)
    if canonical != url and canonical in seen:
      continue
    seen[canonical] = "fetched"
    seen[url] = "fetched"

    text = _clean_text(html, canonical)
    if len(text) < 80:
      _notify(progress_callback, "warning", f"Skipped {canonical} (insufficient text)")
      continue

    title = soup.title.get_text(strip=True) if soup.title else ""
    meta_description = ""
    meta = soup.find("meta", attrs={"name": "description"})
    if meta and meta.get("content"):
      meta_description = _normalize_whitespace(meta["content"])

    headings = _collect_headings(soup)
    content_hash = hashlib.sha1(text.encode("utf-8")).hexdigest()

    payload = {
      "url": canonical,
      "original_url": url,
      "title": title,
      "meta_description": meta_description,
      "headings": headings,
      "extracted_at": datetime.datetime.utcnow().isoformat() + "Z",
      "text": text,
      "content_hash": content_hash,
      "source_type": "crawl",
      "content_type": content_type,
      "status_code": response.status_code
    }
    _save_document(output_dir, payload)
    stored_pages += 1
    crawled.append({"url": canonical, "chars": len(text), "title": title})

    _notify(progress_callback, "success", f"Saved page {stored_pages}: {canonical[:70]} ({len(text)} chars)")

    for link in soup.find_all("a", href=True):
      normalized = _normalize_url(link["href"], canonical)
      if not normalized:
        continue
      if not _same_host(normalized, allowed_host):
        continue
      if normalized in seen:
        continue
      queue.append(normalized)

    time.sleep(0.2)

    if len(seen) % 10 == 0:
      _notify(
        progress_callback,
        "info",
        f"Crawling... {stored_pages} pages saved, {len(seen)} URLs visited, {len(queue)} queued"
      )

  final_msg = f"Done. Saved {stored_pages} pages to {output_dir}"
  print(final_msg)
  _notify(progress_callback, "complete", final_msg)
  return {"pages": stored_pages, "urls": crawled}


if __name__ == "__main__":
  start = sys.argv[1] if len(sys.argv) > 1 else "https://www.officems.co.za/"
  limit = int(sys.argv[2]) if len(sys.argv) > 2 else 500
  crawl_site(start, limit)
