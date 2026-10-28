"""
Source fetchers untuk RemoteJobScraper.

Setiap fetcher adalah fungsi murni `fetch_x(ctx) -> list[dict]` yang hanya melakukan
network + parsing (tanpa menyentuh database), sehingga aman dijalankan paralel di thread.
Dict yang dikembalikan memakai kunci:
    title, company, url, description, [skills], [salary], [posted], [location]
Filter relevansi dan dedup dilakukan oleh orchestrator (remote_scraper.py).
"""
import html as htmllib
import json
import os
import random
import re
import time
import xml.etree.ElementTree as ET

import requests
from salary_utils import strict_salary_range
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Default company boards (ATS publik, tanpa API key). Semua slug sudah diverifikasi aktif.
# Bisa dioverride lewat file JSON: {"greenhouse": [...], "lever": [...], "ashby": [...]}
DEFAULT_BOARDS = {
    "greenhouse": [
        "gitlab", "databricks", "airbnb", "stripe", "datadog", "cloudflare", "mongodb",
        "elastic", "fivetran", "gusto", "coinbase", "reddit", "figma", "twilio", "okta",
        "asana", "pinterest", "instacart", "robinhood", "affirm", "samsara", "toast",
        "lyft", "wise", "grafanalabs", "anthropic",
    ],
    "lever": ["spotify", "palantir", "crypto", "zoox"],
    "ashby": [
        "ramp", "notion", "linear", "supabase", "snowflake", "cohere", "zapier",
        "posthog", "clickhouse", "motherduck",
    ],
}

REMOTE_LOCATION_RE = re.compile(r"remote|anywhere|worldwide|work from home|distributed|apac|asia|indonesia", re.I)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
class Http:
    """requests.Session dengan retry + exponential backoff (429/5xx, hormati Retry-After)."""

    def __init__(self, timeout=20):
        self.timeout = timeout
        self.session = requests.Session()
        retry = Retry(
            total=3, backoff_factor=1.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET",), respect_retry_after_header=True,
        )
        adapter = HTTPAdapter(max_retries=retry, pool_maxsize=16)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept": "*/*"})

    def get(self, url, **kw):
        resp = self.session.get(url, timeout=kw.pop("timeout", self.timeout), **kw)
        resp.raise_for_status()
        return resp

    def json(self, url, **kw):
        return self.get(url, **kw).json()

    def text(self, url, **kw):
        return self.get(url, **kw).text


def html_to_text(raw, escaped=False):
    if not raw:
        return ""
    if escaped:
        raw = htmllib.unescape(raw)
    return BeautifulSoup(raw, "html.parser").get_text(separator="\n")


def fmt_salary(lo, hi, currency="USD", per="year"):
    """Format konsisten: 'USD 120,000 - 150,000 per year'. Mengembalikan None jika kosong."""
    def num(v):
        try:
            v = float(v)
        except (TypeError, ValueError):
            return None
        return f"{int(v):,}" if v > 0 else None
    lo_s, hi_s = num(lo), num(hi)
    cur = (currency or "USD").upper()
    if lo_s and hi_s and lo_s != hi_s:
        body = f"{lo_s} - {hi_s}"
    elif lo_s or hi_s:
        body = lo_s or hi_s
    else:
        return None
    return f"{cur} {body}" + (f" per {per}" if per else "")


_PERIODS = {"hourly": "hour", "daily": "day", "weekly": "week", "monthly": "month", "yearly": "year", "annual": "year"}


def _period(p):
    p = (p or "year").lower()
    return _PERIODS.get(p, p)


def with_location(desc, location):
    return f"Location: {location}\n{desc}" if location and desc else desc


def is_remote_location(*parts):
    return any(p and REMOTE_LOCATION_RE.search(str(p)) for p in parts)


# ---------------------------------------------------------------------------
# Aggregator API / RSS
# ---------------------------------------------------------------------------
def fetch_remotive(ctx):
    jobs = ctx.http.json(f"https://remotive.com/api/remote-jobs?search={ctx.q}").get("jobs", [])
    if ctx.general:
        jobs += ctx.http.json("https://remotive.com/api/remote-jobs").get("jobs", [])
    data = {"jobs": jobs}
    return [{
        "title": j.get("title"), "company": j.get("company_name"), "url": j.get("url"),
        "description": html_to_text(j.get("description")), "skills": j.get("tags") or None,
        "salary": j.get("salary") or None, "posted": j.get("publication_date"),
        "location": j.get("candidate_required_location"),
    } for j in data.get("jobs", [])]


def fetch_weworkremotely(ctx):
    feeds = [
        "remote-programming-jobs", "remote-back-end-programming-jobs",
        "remote-full-stack-programming-jobs", "remote-devops-sysadmin-jobs",
    ]
    if ctx.general:
        feeds += ["remote-product-jobs", "remote-design-jobs", "remote-customer-support-jobs",
                  "remote-sales-and-marketing-jobs", "remote-management-and-finance-jobs",
                  "all-other-remote-jobs"]
    out = []
    for slug in feeds:
        try:
            root = ET.fromstring(ctx.http.get(f"https://weworkremotely.com/categories/{slug}.rss").content)
        except Exception as e:
            ctx.warn(f"feed {slug}: {e}")
            continue
        for item in root.findall(".//item"):
            g = lambda tag: (item.findtext(tag) or "").strip()
            raw_title = g("title")
            company, _, title = raw_title.partition(":") if ":" in raw_title else ("WWR Partner", "", raw_title)
            out.append({
                "title": title.strip(), "company": company.strip(), "url": g("link"),
                "description": html_to_text(g("description")), "posted": g("pubDate"),
                "location": g("region") or None,
            })
    return out


def fetch_remoteok(ctx):
    data = ctx.http.json("https://remoteok.com/api")
    out = []
    for j in data:
        if not isinstance(j, dict) or "position" not in j:
            continue
        out.append({
            "title": j.get("position"), "company": j.get("company"),
            "url": j.get("url") or (f"https://remoteok.com/remote-jobs/{j['id']}" if j.get("id") else None),
            "description": html_to_text(j.get("description")), "skills": j.get("tags") or None,
            "salary": fmt_salary(j.get("salary_min"), j.get("salary_max"), "USD", "year"),
            "posted": j.get("date"), "location": j.get("location"),
        })
    return out


def fetch_himalayas(ctx):
    out = []
    urls = [f"https://himalayas.app/jobs/api/search?q={ctx.q}&limit=20&offset={o}" for o in (0, 20, 40)]
    if ctx.general:
        urls += [f"https://himalayas.app/jobs/api?limit=20&offset={o}" for o in (0, 20, 40, 60, 80)]
    for url in urls:
        data = ctx.http.json(url)
        jobs = data.get("jobs", [])
        for j in jobs:
            out.append({
                "title": j.get("title"), "company": j.get("companyName"),
                "url": j.get("applicationLink") or j.get("guid"),
                "description": html_to_text(j.get("description") or j.get("excerpt")),
                "skills": j.get("categories") or None,
                "salary": fmt_salary(j.get("minSalary"), j.get("maxSalary"), j.get("currency", "USD"), "year"),
                "posted": j.get("pubDate"),
                "location": ", ".join(j.get("locationRestrictions") or []) or "Worldwide",
            })
    return out


def fetch_jobicy(ctx):
    out = []
    variants = ["tag=data", "industry=data-science", f"tag={ctx.q}"]
    if ctx.general:
        variants.append("")
    for params in variants:
        try:
            data = ctx.http.json(f"https://jobicy.com/api/v2/remote-jobs?count=50&{params}")
        except Exception as e:
            ctx.warn(f"{params}: {e}")
            continue
        for j in data.get("jobs", []):
            out.append({
                "title": j.get("jobTitle"), "company": j.get("companyName"), "url": j.get("url"),
                "description": html_to_text(j.get("jobDescription")),
                "salary": fmt_salary(j.get("annualSalaryMin") or j.get("salaryMin"),
                                     j.get("annualSalaryMax") or j.get("salaryMax"),
                                     j.get("salaryCurrency", "USD"), _period(j.get("salaryPeriod"))),
                "posted": j.get("pubDate"), "location": j.get("jobGeo"),
            })
    return out


def fetch_arbeitnow(ctx):
    out = []
    for page in (1, 2, 3):
        data = ctx.http.json(f"https://www.arbeitnow.com/api/job-board-api?page={page}")
        for j in data.get("data", []):
            if not j.get("remote"):
                continue
            out.append({
                "title": j.get("title"), "company": j.get("company_name"), "url": j.get("url"),
                "description": html_to_text(j.get("description")), "skills": j.get("tags") or None,
                "posted": j.get("created_at"), "location": j.get("location"),
            })
    return out


def fetch_workingnomads(ctx):
    out = []
    for j in ctx.http.json("https://www.workingnomads.com/api/exposed_jobs/"):
        tags = [t.strip() for t in (j.get("tags") or "").split(",") if t.strip()]
        out.append({
            "title": j.get("title"), "company": j.get("company_name"), "url": j.get("url"),
            "description": html_to_text(j.get("description")), "skills": tags or None,
            "posted": j.get("pub_date"), "location": j.get("location"),
        })
    return out


def fetch_themuse(ctx):
    out = []
    categories = ["Data and Analytics", "Data Science", "Software Engineering"]
    if ctx.general:
        categories += ["Product", "Design and UX", "Marketing", "Sales", "Customer Service",
                       "Project Management", "Accounting and Finance", "Human Resources and Recruiting"]
    for category in categories:
        for page in (1, 2, 3):
            try:
                data = ctx.http.json(
                    "https://www.themuse.com/api/public/jobs",
                    params={"category": category, "location": "Flexible / Remote", "page": page},
                )
            except Exception as e:
                ctx.warn(f"{category} p{page}: {e}")
                break
            for j in data.get("results", []):
                locs = ", ".join(l.get("name", "") for l in j.get("locations", []))
                out.append({
                    "title": j.get("name"), "company": (j.get("company") or {}).get("name"),
                    "url": (j.get("refs") or {}).get("landing_page"),
                    "description": html_to_text(j.get("contents")),
                    "posted": j.get("publication_date"), "location": locs or "Flexible / Remote",
                })
            if page >= data.get("page_count", 1):
                break
    return out


def fetch_hackernews(ctx):
    """Komentar top-level thread 'Ask HN: Who is hiring?' terbaru (1 thread, 2 request)."""
    hits = ctx.http.json(
        "https://hn.algolia.com/api/v1/search_by_date?tags=story,author_whoishiring&hitsPerPage=5"
    ).get("hits", [])
    thread = next((h for h in hits if "who is hiring" in (h.get("title") or "").lower()), None)
    if not thread:
        return []
    tree = ctx.http.json(f"https://hn.algolia.com/api/v1/items/{thread['objectID']}")
    out = []
    for c in tree.get("children", []):
        text = c.get("text")
        if not text or not re.search(r"\bremote\b", text, re.I):
            continue
        plain = html_to_text(text, escaped=True)
        header = plain.split("\n", 1)[0].strip()
        company = re.split(r"\s*[|—–]\s*", header, maxsplit=1)[0].strip() or "HN Company"
        out.append({
            "title": header[:140], "company": company[:80],
            "url": f"https://news.ycombinator.com/item?id={c['id']}",
            "description": plain, "posted": c.get("created_at"), "location": "Remote",
        })
    return out


# ---------------------------------------------------------------------------
# Company boards (ATS publik)
# ---------------------------------------------------------------------------
def _gh_board(ctx, slug):
    data = ctx.http.json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true")
    out = []
    for j in data.get("jobs", []):
        loc = (j.get("location") or {}).get("name")
        if not is_remote_location(loc):
            continue
        out.append({
            "title": j.get("title"), "company": j.get("company_name") or slug.title(),
            "url": j.get("absolute_url"),
            "description": with_location(html_to_text(j.get("content"), escaped=True), loc),
            "posted": j.get("first_published") or j.get("updated_at"), "location": loc,
        })
    return out


def _lever_board(ctx, slug):
    out = []
    for j in ctx.http.json(f"https://api.lever.co/v0/postings/{slug}?mode=json"):
        cats = j.get("categories") or {}
        loc = cats.get("location") or j.get("country")
        if j.get("workplaceType") != "remote" and not is_remote_location(loc):
            continue
        sr = j.get("salaryRange") or {}
        desc = j.get("descriptionPlain") or html_to_text(j.get("description"))
        for lst in j.get("lists") or []:
            desc += f"\n{lst.get('text', '')}\n{html_to_text(lst.get('content'))}"
        desc += "\n" + (j.get("additionalPlain") or "")
        out.append({
            "title": j.get("text"), "company": slug.title(), "url": j.get("hostedUrl"),
            "description": with_location(desc, loc),
            "salary": fmt_salary(sr.get("min"), sr.get("max"), sr.get("currency"),
                                 (sr.get("interval") or "").replace("per-", "").replace("-salary", "") or None),
            "posted": j.get("createdAt"), "location": loc,
        })
    return out


def _ashby_board(ctx, slug):
    data = ctx.http.json(f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true")
    out = []
    for j in data.get("jobs", []):
        if j.get("isListed") is False:
            continue
        loc = j.get("location")
        if not (j.get("isRemote") or str(j.get("workplaceType")).lower() == "remote"
                or is_remote_location(loc, *[s.get("location") for s in j.get("secondaryLocations") or []])):
            continue
        comp = (j.get("compensation") or {}).get("compensationTierSummary")
        if comp and not re.search(r"[1-9]", comp):  # '$0' dsb. = tidak ada data gaji
            comp = None
        out.append({
            "title": j.get("title"), "company": slug.replace("-", " ").title(), "url": j.get("jobUrl"),
            "description": with_location(j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml")), loc),
            "salary": comp, "posted": j.get("publishedAt"), "location": loc,
        })
    return out


def _fetch_boards(ctx, kind, board_fn):
    slugs = ctx.boards.get(kind, [])
    out = []
    for i, slug in enumerate(slugs):
        try:
            out.extend(board_fn(ctx, slug))
        except Exception as e:
            ctx.warn(f"board '{slug}' dilewati: {e}")
    return out


def fetch_greenhouse(ctx):
    return _fetch_boards(ctx, "greenhouse", _gh_board)


def fetch_lever(ctx):
    return _fetch_boards(ctx, "lever", _lever_board)


def fetch_ashby(ctx):
    return _fetch_boards(ctx, "ashby", _ashby_board)


# ---------------------------------------------------------------------------
# LinkedIn (guest API) & Upwork
# ---------------------------------------------------------------------------
def fetch_linkedin(ctx):
    out, start = [], 0
    while start < max(100, ctx.limit * 2) and len(out) < ctx.limit:
        url = ("https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
               f"?keywords={ctx.q}&f_WT=2&start={start}")
        soup = BeautifulSoup(ctx.http.text(url), "html.parser")
        cards = soup.find_all("div", class_="base-card")
        if not cards:
            break
        for card in cards:
            t = card.find("h3", class_="base-search-card__title")
            c = card.find("h4", class_="base-search-card__subtitle")
            link = card.find("a", class_="base-card__full-link")
            if not (t and c and link):
                continue
            job_url = link["href"].split("?")[0].strip()
            title = t.get_text(strip=True)
            # Hemat request: lewati detail page untuk yang sudah ada / tidak relevan
            if len(out) >= ctx.limit:
                break
            if ctx.is_seen(job_url) or not ctx.relevant(title):
                continue
            desc, salary = "", None
            try:
                d_soup = BeautifulSoup(ctx.http.text(job_url, timeout=10), "html.parser")
                el = d_soup.find("div", class_="show-more-less-html__markup")
                desc = el.get_text(separator="\n") if el else ""
                # JSON-LD/microdata dulu; selain itu HANYA rentang lengkap dari deskripsi. Heuristik halaman umum
                # (extract_salary) mengambil potongan acak -> salary salah pada seluruh 39 lowongan lama.
                salary = ctx.extract_salary_schema(d_soup) or strict_salary_range(desc)
            except Exception as e:
                ctx.warn(f"detail {job_url[-30:]}: {e}")
            loc = card.find("span", class_="job-search-card__location")
            out.append({
                "title": title, "company": c.get_text(strip=True), "url": job_url,
                "description": desc or f"Remote job posting for {title} at {c.get_text(strip=True)}. Apply via LinkedIn.",
                "salary": salary, "location": loc.get_text(strip=True) if loc else None,
            })
            time.sleep(random.uniform(2.0, 4.0))  # jeda acak anti rate-limit
        start += 25
        time.sleep(random.uniform(1.0, 2.0))
    return out


def fetch_upwork(ctx):
    feed_url = ctx.upwork_rss or os.environ.get("UPWORK_RSS_URL")
    path = ctx.upwork_file
    if path and os.path.exists(path):
        if path.endswith(".json"):
            with open(path, "r", encoding="utf-8") as f:
                items = json.load(f)
            return [{
                "title": i.get("title"), "company": "Upwork Client", "url": i.get("url"),
                "description": i.get("description", ""), "skills": i.get("skills") or None,
                "salary": i.get("budget") or i.get("salary"),
            } for i in items]
        with open(path, "rb") as f:
            xml_content = f.read()
    elif feed_url:
        xml_content = ctx.http.get(feed_url).content
    else:
        ctx.warn("UPWORK_RSS_URL / --upwork-file belum disetel (lewati)")
        return []

    out = []
    for item in ET.fromstring(xml_content).findall(".//item"):
        desc = html_to_text(item.findtext("description") or "")
        sal = re.search(r"(Hourly Range|Budget):\s*(\$[0-9.,]+(?:\s*-\s*\$[0-9.,]+)?)", desc, re.I)
        sk = re.search(r"Skills:\s*([^\n]+)", desc, re.I)
        out.append({
            "title": item.findtext("title") or "Upwork Project", "company": "Upwork Client",
            "url": (item.findtext("link") or "").strip(), "description": desc,
            "salary": sal.group(0).strip() if sal else None,
            "skills": [s.strip() for s in sk.group(1).split(",") if s.strip()] if sk else None,
            "posted": item.findtext("pubDate"),
        })
    return out


# Registry sumber. Urutan = urutan tampil; linkedin (HTML guest endpoint) paling rapuh & lambat.
SOURCES = {
    "remotive": fetch_remotive,
    "weworkremotely": fetch_weworkremotely,
    "remoteok": fetch_remoteok,
    "himalayas": fetch_himalayas,
    "jobicy": fetch_jobicy,
    "arbeitnow": fetch_arbeitnow,
    "workingnomads": fetch_workingnomads,
    "themuse": fetch_themuse,
    "hackernews": fetch_hackernews,
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
    "linkedin": fetch_linkedin,
    "upwork": fetch_upwork,
}

# Nama platform yang disimpan di database
PLATFORM_NAMES = {
    "remotive": "Remotive", "weworkremotely": "WeWorkRemotely", "remoteok": "RemoteOK",
    "himalayas": "Himalayas", "jobicy": "Jobicy", "arbeitnow": "Arbeitnow",
    "workingnomads": "WorkingNomads", "themuse": "TheMuse", "hackernews": "HackerNews",
    "greenhouse": "Greenhouse", "lever": "Lever", "ashby": "Ashby",
    "linkedin": "LinkedIn Remote", "upwork": "Upwork",
}
