"""
Web scraper endpoint for relocant.help project.
Completely isolated from trading bot logic.
"""
from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel
import httpx
import os
import warnings
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
from bs4 import BeautifulSoup

router = APIRouter(prefix="/scraper", tags=["scraper"])

SCRAPER_SECRET = os.getenv("SCRAPER_SECRET", "")

class ScrapeRequest(BaseModel):
    url: str

class ScrapeResponse(BaseModel):
    success: bool
    title: str
    content: str
    url: str
    error: str = ""

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,es;q=0.8,it;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "no-cache",
    "Referer": "https://www.google.com/",
}

@router.post("/scrape", response_model=ScrapeResponse)
async def scrape_url(
    request: ScrapeRequest,
    x_scraper_secret: str = Header(default="")
):
    # Auth check
    if SCRAPER_SECRET and x_scraper_secret != SCRAPER_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        async with httpx.AsyncClient(
            headers=HEADERS,
            follow_redirects=True,
            timeout=15.0,
            verify=False
        ) as client:
            response = await client.get(request.url)

            if response.status_code != 200:
                return ScrapeResponse(
                    success=False,
                    title="",
                    content="",
                    url=request.url,
                    error=f"HTTP {response.status_code}"
                )

            soup = BeautifulSoup(response.text, "html.parser")

            # Remove navigation garbage
            for tag in soup.find_all([
                "nav", "header", "footer", "aside",
                "script", "style", "noscript",
                "menu", "breadcrumb"
            ]):
                tag.decompose()

            # Also remove by common nav class/id names
            for tag in soup.find_all(
                class_=lambda c: c and any(
                    x in str(c).lower() for x in
                    ["nav", "menu", "sidebar", "footer",
                     "header", "breadcrumb", "cookie"]
                )
            ):
                tag.decompose()

            # Extract title
            title = ""
            if soup.find("h1"):
                title = soup.find("h1").get_text(strip=True)
            elif soup.find("title"):
                title = soup.find("title").get_text(strip=True)

            # Extract main content
            content = ""
            main = (
                soup.find("main") or
                soup.find("article") or
                soup.find(id="content") or
                soup.find(id="main-content") or
                soup.find(class_="content") or
                soup.find(class_="article-body") or
                soup.body
            )

            if main:
                paragraphs = main.find_all("p")
                content = "\n\n".join(
                    p.get_text(strip=True)
                    for p in paragraphs
                    if len(p.get_text(strip=True)) > 60
                )

            if len(content) < 200:
                return ScrapeResponse(
                    success=False,
                    title=title,
                    content="",
                    url=request.url,
                    error="Content too short after cleaning"
                )

            return ScrapeResponse(
                success=True,
                title=title,
                content=content[:5000],  # limit
                url=str(response.url),
                error=""
            )

    except Exception as e:
        return ScrapeResponse(
            success=False,
            title="",
            content="",
            url=request.url,
            error=str(e)
        )
