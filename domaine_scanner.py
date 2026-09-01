import asyncio

from domaine_collector import collect_domaine_lot


DOMAIN_URL = "https://encheres-domaine.gouv.fr/lot/audiq7-1-doo-1.html"


async def main():
    print("=== DOMAINE SCANNER START ===")

    result = await collect_domaine_lot(DOMAIN_URL)

    parsed = result.get("parsed", {})

    print("Source:", result.get("source"))
    print("Title:", result.get("page_title"))
    print("Brand:", parsed.get("brand"))
    print("Model:", parsed.get("model"))
    print("Year:", parsed.get("year"))
    print("Mileage:", parsed.get("mileage_km"))
    print("Current bid:", parsed.get("current_bid"))
    print("Location:", parsed.get("location"))
    print("Risk flags:", parsed.get("risk_flags"))

    print("=== DOMAINE SCANNER END ===")


if __name__ == "__main__":
    asyncio.run(main())
