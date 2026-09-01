import requests
from bs4 import BeautifulSoup


LOT_URL = "https://encheres-domaine.gouv.fr/lot/audiq7-1-doo-1.html"


def collect_domaine_lot(url):
    headers = {
        "User-Agent": "Mozilla/5.0"
    }

    response = requests.get(url, headers=headers, timeout=20)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    text = soup.get_text(" ", strip=True)

    result = {
        "source": "Encheres du Domaine",
        "url": url,
        "http_status": response.status_code,
        "page_title": soup.title.get_text(strip=True) if soup.title else None,
        "contains_audi": "AUDI" in text.upper(),
        "contains_q7": "Q7" in text.upper(),
        "contains_km": "245200" in text.replace(" ", ""),
    }

    return result


if __name__ == "__main__":
    data = collect_domaine_lot(LOT_URL)
    print(data)
