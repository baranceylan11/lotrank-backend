import json
import requests


API_URL = "https://recherche.lacentrale.fr/v3/search"

TEST_PARAMS = {
    "makesModelsCommercialNames": "PEUGEOT:208",
    "page": "0",
    "pageSize": "5",
    "yearMin": "2018",
    "yearMax": "2020",
    "mileageMin": "65000",
    "mileageMax": "120000",
}

HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://www.lacentrale.fr",
    "Referer": "https://www.lacentrale.fr/",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "X-Client-Source": "lc:recherche:front",
}


def safe_preview(text: str, limit: int = 1000) -> str:
    if not text:
        return "<empty>"

    return " ".join(text.split())[:limit]


def run_test() -> None:
    print("=== LACENTRALE SEARCH API TEST START ===")
    print("Endpoint:", API_URL)
    print("API key configured: no")
    print("Writing to database: no")

    try:
        response = requests.get(
            API_URL,
            params=TEST_PARAMS,
            headers=HEADERS,
            timeout=30,
            allow_redirects=False,
        )

    except requests.RequestException as error:
        print("Connection result: FAILED")
        print("Error type:", type(error).__name__)
        print("Error:", str(error))
        print("=== LACENTRALE SEARCH API TEST END ===")
        return

    print("Connection result: REACHED")
    print("HTTP status:", response.status_code)
    print(
        "Content-Type:",
        response.headers.get("content-type"),
    )
    print(
        "Response bytes:",
        len(response.content),
    )

    if response.is_redirect:
        print(
            "Redirect location:",
            response.headers.get("location"),
        )

    try:
        payload = response.json()

    except (ValueError, json.JSONDecodeError):
        print("JSON response: no")
        print(
            "Response preview:",
            safe_preview(response.text),
        )
        print(
            "=== LACENTRALE SEARCH API TEST END ==="
        )
        return

    print("JSON response: yes")

    if isinstance(payload, dict):
        print(
            "Top-level keys:",
            sorted(payload.keys()),
        )

        hits = payload.get("hits")

        if isinstance(hits, list):
            print(
                "Hits:",
                len(hits),
            )

            if hits:
                first = hits[0]

                print(
                    "First hit type:",
                    type(first).__name__,
                )

                if isinstance(first, dict):
                    print(
                        "First hit keys:",
                        sorted(first.keys()),
                    )

                    item = first.get("item")

                    if isinstance(item, dict):
                        print(
                            "First item keys:",
                            sorted(item.keys()),
                        )

                        vehicle = item.get(
                            "vehicle"
                        )

                        if isinstance(
                            vehicle,
                            dict,
                        ):
                            print(
                                "First vehicle keys:",
                                sorted(
                                    vehicle.keys()
                                ),
                            )

        else:
            print(
                "Hits field: unavailable"
            )

        error_value = (
            payload.get("error")
            or payload.get("message")
            or payload.get("detail")
        )

        if error_value:
            print(
                "API message:",
                str(error_value)[:500],
            )

    else:
        print(
            "JSON root type:",
            type(payload).__name__,
        )

    print(
        "=== LACENTRALE SEARCH API TEST END ==="
    )


if __name__ == "__main__":
    run_test()
