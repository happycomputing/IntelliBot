#!/usr/bin/env python3
import os, re, time, json, urllib.parse, hashlib, sys
import requests
from bs4 import BeautifulSoup
import trafilatura

def crawl_site(start_url, max_pages=500, timeout=12, progress_callback=None, output_dir="kb/raw"):
    """Crawl a website and extract clean text from pages"""
    allowed_host = urllib.parse.urlparse(start_url).netloc
    out_dir = output_dir
    headers = {"User-Agent": "IntelliBot/1.0"}
    
    def normalize_url(url, base):
        u = urllib.parse.urljoin(base, url.split("#")[0])
        p = urllib.parse.urlparse(u)
        if p.netloc != allowed_host:
            return None
        if p.scheme not in ("http", "https"):
            return None
        return urllib.parse.urlunparse((p.scheme, p.netloc, p.path.rstrip("/"), "", "", ""))

    def fetch(url):
        try:
            r = requests.get(url, headers=headers, timeout=timeout)
            if r.status_code == 200 and "text/html" in r.headers.get("Content-Type",""):
                return r.text
        except requests.RequestException as e:
            if progress_callback:
                progress_callback('warning', f"Failed to fetch {url}: {str(e)}")
        return None

    def extract_clean(html, url):
        txt = trafilatura.extract(html, url=url, output_format="txt", include_links=False) or ""
        if not txt.strip():
            soup = BeautifulSoup(html, "html.parser")
            for s in soup(["script","style","noscript"]): s.decompose()
            txt = soup.get_text(" ", strip=True)
        txt = re.sub(r"\s+\n", "\n", re.sub(r"[ \t]+", " ", txt)).strip()
        return txt

    def save_doc(url, text):
        os.makedirs(out_dir, exist_ok=True)
        hid = hashlib.sha1(url.encode()).hexdigest()[:16]
        with open(os.path.join(out_dir, f"{hid}.json"), "w", encoding="utf-8") as f:
            json.dump({"url": url, "text": text}, f, ensure_ascii=False)

    normalized_start = normalize_url(start_url, start_url)
    to_visit = [normalized_start] if normalized_start else [start_url]
    seen = set()
    pages = 0
    crawled_urls = []
    
    if progress_callback:
        progress_callback('info', f"Starting crawl of {start_url} (max {max_pages} pages)")
    
    while to_visit and pages < max_pages:
        url = to_visit.pop(0)
        if url in seen: 
            continue
        seen.add(url)
        
        if progress_callback and len(seen) % 5 == 0:
            progress_callback('info', f"Crawling... {pages} pages saved, {len(seen)} URLs visited, {len(to_visit)} queued")
        
        html = fetch(url)
        if not html: 
            continue
        text = extract_clean(html, url)
        if len(text) > 50:
            save_doc(url, text)
            pages += 1
            crawled_urls.append({"url": url, "chars": len(text)})
            print(f"[{pages}] {url} ({len(text)} chars)")
            if progress_callback:
                progress_callback('success', f"Saved page {pages}: {url[:60]}... ({len(text)} chars)")
        elif len(text) > 0 and progress_callback:
            progress_callback('warning', f"Skipped {url[:50]}... (only {len(text)} chars - may be JS-rendered)")
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a", href=True):
            nu = normalize_url(a["href"], url)
            if nu and nu not in seen:
                to_visit.append(nu)
        time.sleep(0.25)
    
    final_msg = f"Done. Saved {pages} pages to {out_dir}"
    print(final_msg)
    if progress_callback:
        progress_callback('complete', final_msg)
    return {"pages": pages, "urls": crawled_urls}

if __name__ == "__main__":
    start_url = sys.argv[1] if len(sys.argv) > 1 else "https://www.officems.co.za/"
    max_pages = int(sys.argv[2]) if len(sys.argv) > 2 else 500
    crawl_site(start_url, max_pages)
