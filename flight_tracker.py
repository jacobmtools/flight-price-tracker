"""
Flight Price Tracker
Uses SerpApi to fetch Google Flights data, logs prices to CSV,
and sends a push notification via ntfy.sh when a deal is found.
"""

import os
import csv
import json
from datetime import datetime, timedelta
from urllib import request, parse, error

# ─────────────────────────────────────────────
# CONFIGURATION — Edit these to match your route
# ─────────────────────────────────────────────

ORIGIN = "YYZ"          # Departure airport IATA code (Toronto Pearson)
DESTINATION = "GYE"     # Arrival airport IATA code (Guayaquil)
SEARCH_DAYS_AHEAD = 30  # How many days ahead to search for flights
TRIP_TYPE = 1           # 1 = Round trip, 2 = One way
RETURN_DAYS_AFTER = 14  # For round trips: return this many days after departure
CURRENCY = "CAD"        # Currency code (CAD, USD, EUR, etc.)
HISTORY_FILE = "price_history.csv"

# These are read from GitHub Secrets (set in Step 3)
SERPAPI_KEY = os.environ.get("SERPAPI_KEY", "")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")


def search_flights():
    """
    Ask SerpApi for Google Flights data on our route.
    Returns a dictionary with flight offers and price insights.
    """
    departure_date = (datetime.now() + timedelta(days=SEARCH_DAYS_AHEAD)).strftime("%Y-%m-%d")
    return_date = (datetime.now() + timedelta(days=SEARCH_DAYS_AHEAD + RETURN_DAYS_AFTER)).strftime("%Y-%m-%d")

    params = {
        "engine": "google_flights",
        "departure_id": ORIGIN,
        "arrival_id": DESTINATION,
        "outbound_date": departure_date,
        "type": str(TRIP_TYPE),
        "currency": CURRENCY,
        "hl": "en",
        "api_key": SERPAPI_KEY,
    }

    if TRIP_TYPE == 1:
        params["return_date"] = return_date
    query_string = parse.urlencode(params)
    url = f"https://serpapi.com/search.json?{query_string}"

    try:
        req = request.Request(url)
        with request.urlopen(req) as response:
            return json.loads(response.read().decode("utf-8"))
    except error.HTTPError as e:
        error_body = e.read().decode("utf-8")
        print(f" API Error {e.code}: {error_body}")
        return None
    except Exception as e:
        print(f" Request failed: {e}")
        return None


def extract_price_info(data):
    """
    Extract the cheapest price and Google's price insights from the API response.
    """
    if not data:
        return None
    result = {
        "cheapest_price": None,
        "price_level": None,
        "typical_low": None,
        "typical_high": None,
        "airline": None,
        "stops": None,
        "duration": None,
    }

    insights = data.get("price_insights", {})
    if insights:
        result["cheapest_price"] = insights.get("lowest_price")
        result["price_level"] = insights.get("price_level")
        typical = insights.get("typical_price_range", [])
        if len(typical) == 2:
            result["typical_low"] = typical[0]
            result["typical_high"] = typical[1]

    best_flights = data.get("best_flights", [])
    if best_flights:
        top_flight = best_flights[0]
        result["cheapest_price"] = result["cheapest_price"] or top_flight.get("price")
        legs = top_flight.get("flights", [])
        if legs:
            result["airline"] = legs[0].get("airline")
            result["stops"] = len(legs) - 1
            result["duration"] = top_flight.get("total_duration")

    if result["cheapest_price"] is None:
        other_flights = data.get("other_flights", [])
        if other_flights:
            result["cheapest_price"] = other_flights[0].get("price")

    return result


def load_price_history():
    """Read past prices from the CSV file."""
    history = []
    if not os.path.exists(HISTORY_FILE):
        return history
    with open(HISTORY_FILE, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                history.append({
                    "date": row["date"],
                    "price": float(row["price"]),
                    "price_level": row.get("price_level", ""),
                    "typical_low": float(row["typical_low"]) if row.get("typical_low") else None,
                    "typical_high": float(row["typical_high"]) if row.get("typical_high") else None,
                })
            except (KeyError, ValueError):
                continue
    return history


def save_price_to_history(price_info):
    """Add today's price data to the CSV history file."""
    file_exists = os.path.exists(HISTORY_FILE)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    route = f"{ORIGIN}-{DESTINATION}"

    with open(HISTORY_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["date", "price", "route", "currency", "price_level",
                             "typical_low", "typical_high", "airline"])
        writer.writerow([
            now,
            price_info.get("cheapest_price", ""),
            route,
            CURRENCY,
            price_info.get("price_level", ""),
            price_info.get("typical_low", ""),
            price_info.get("typical_high", ""),
            price_info.get("airline", ""),
        ])


def is_good_deal(price_info):
    """Determine if the current price is a good deal."""
    price = price_info.get("cheapest_price")
    if price is None:
        return False, "No price data"

    level = price_info.get("price_level", "")
    if level == "low":
        return True, f"Google rates this as LOW (a deal!)"

    typical_low = price_info.get("typical_low")
    if typical_low and price < typical_low:
        return True, f"Price {CURRENCY} {price} is below typical low of {CURRENCY} {typical_low}"

    if level:
        return False, f"Google rates this price as: {level.upper()}"
    return False, "Not enough data to determine"


def send_notification(message):
    """
    Send a push notification to your phone via ntfy.sh.
    Uses urllib with manually set bytes headers to avoid Unicode/latin-1 encoding errors.
    """
    if not NTFY_TOPIC:
        print(" No NTFY_TOPIC set — skipping notification.")
        return
    url = f"https://ntfy.sh/{NTFY_TOPIC}"
    data = message.encode("utf-8")
    req = request.Request(url, data=data, method="POST")
    # Use encode("utf-8") for headers to avoid latin-1 codec errors with emoji
    req.add_unredirected_header("Title", "Flight Deal Alert!".encode("utf-8").decode("latin-1", errors="replace"))
    req.add_unredirected_header("Priority", "high")
    req.add_unredirected_header("Tags", "airplane,moneybag")
    req.add_unredirected_header("Content-Type", "text/plain; charset=utf-8")

    try:
        with request.urlopen(req) as response:
            print(f" Notification sent! Status: {response.status}")
    except error.HTTPError as e:
        print(f" Notification error: {e.code}")


def main():
    """Main function that ties everything together."""
    departure_date = (datetime.now() + timedelta(days=SEARCH_DAYS_AHEAD)).strftime("%b %d, %Y")

    print(f"{'='*55}")
    print(f" Flight Price Tracker — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f" Route: {ORIGIN} -> {DESTINATION}")
    print(f" Searching for: {departure_date}")
    print(f"{'='*55}")

    print("\n1. Fetching flight data from Google Flights (via SerpApi)...")
    data = search_flights()
    if data is None:
        print(" Failed to fetch data. Check your API key.")
        return

    if "error" in data:
        print(f" API error: {data['error']}")
        return

    print("\n2. Analyzing prices...")
    price_info = extract_price_info(data)

    if price_info is None or price_info.get("cheapest_price") is None:
        print(" No flight data found for this route/date.")
        print(" Try a different date or a more common route.")
        return
    price = price_info["cheapest_price"]
    level = price_info.get("price_level", "unknown")
    typical_low = price_info.get("typical_low")
    typical_high = price_info.get("typical_high")
    airline = price_info.get("airline", "Unknown airline")

    print(f" Cheapest price: {CURRENCY} {price}")
    print(f" Google's rating: {level.upper()}")
    if typical_low and typical_high:
        print(f" Typical range: {CURRENCY} {typical_low} - {CURRENCY} {typical_high}")
    print(f" Airline: {airline}")

    print("\n3. Updating price history...")
    history = load_price_history()
    save_price_to_history(price_info)
    print(f" Saved. Total data points: {len(history) + 1}")

    print("\n4. Deal check...")
    is_deal, reason = is_good_deal(price_info)
    print(f" -> {reason}")

    if is_deal:
        message = (
            f"DEAL FOUND: {ORIGIN} -> {DESTINATION}\n"
            f"Price: {CURRENCY} {price}\n"
            f"Google says: {level.upper()}\n"
        )
        if typical_low and typical_high:
            message += f"Typical range: {CURRENCY} {typical_low} - {CURRENCY} {typical_high}\n"
        message += (
            f"Airline: {airline}\n"
            f"Depart: {departure_date}"
        )
        print(f"\n DEAL! Sending notification...")
        send_notification(message)
    else:
        print(f"\n No deal right now. Tracker will check again in 6 hours.")

    if len(history) == 0:
        send_notification(
            f"Flight tracker started!\n"
            f"Route: {ORIGIN} -> {DESTINATION}\n"
            f"First price: {CURRENCY} {price} ({level})\n"
            f"Checking every 6 hours for deals."
        )

    print(f"\n{'='*55}")
    print("Done!")


if __name__ == "__main__":
    main()
