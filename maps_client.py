"""Bounded Google Maps HTTP requests. Never return request headers or credentials."""
import math
import os
import re
from datetime import datetime, timedelta, timezone

import requests

from fairness import parse_time

ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
PLACES_URL = "https://places.googleapis.com/v1/places:"
ROUTE_FIELDS = ",".join([
    "routes.duration", "routes.distanceMeters", "routes.legs.endLocation",
    "routes.legs.steps.travelMode", "routes.legs.steps.distanceMeters",
    "routes.legs.steps.staticDuration", "routes.legs.steps.transitDetails",
])
PLACE_FIELDS = ",".join("places." + f for f in [
    "id", "displayName", "formattedAddress", "location", "primaryType",
    "googleMapsUri", "rating", "userRatingCount", "priceLevel",
    "currentOpeningHours", "businessStatus", "attributions",
])


def seconds(value):
    if not isinstance(value, str) or not value.endswith("s"):
        raise ValueError("Missing duration")
    number = float(value[:-1])
    if not math.isfinite(number) or number < 0:
        raise ValueError("Invalid duration")
    return number


def _distance(lat, lng, location):
    a, b = math.radians(lat), math.radians(location["latitude"])
    d = math.radians(location["longitude"] - lng)
    h = math.sin((b-a)/2)**2 + math.cos(a)*math.cos(b)*math.sin(d/2)**2
    return 6371000 * 2 * math.asin(math.sqrt(min(1, h)))


class MapsClient:
    def __init__(self, api_key=None, post=None):
        self._key = api_key if api_key is not None else os.getenv("GOOGLE_MAPS_API_KEY", "")
        self._post = post or requests.post

    @property
    def configured(self):
        return bool(self._key.strip())

    def _request(self, url, body, fieldmask, api_name):
        if not self.configured:
            return {"error": "Server setup needed: set GOOGLE_MAPS_API_KEY with Routes API and Places API (New) enabled. Never paste keys into chat."}
        try:
            response = self._post(url, json=body,
                headers={"X-Goog-Api-Key": self._key, "X-Goog-FieldMask": fieldmask,
                         "Content-Type": "application/json"}, timeout=(5,20))
            if response.status_code != 200:
                advice = {400: "Check precise NYC addresses, future meeting date and supported travel mode.",
                          401: "Check the server's Maps key.",
                          403: f"Enable {api_name} and billing; check this key's API restrictions.",
                          429: "Maps quota exceeded. Wait briefly or check project quotas."}.get(response.status_code, "Maps is temporarily unavailable; retry later.")
                result = {"error": f"{api_name} HTTP {response.status_code}. {advice}", "http_status":response.status_code}
                try:
                    problem = response.json().get("error", {})
                    message = problem.get("message")
                    if isinstance(message, str):
                        # Keep Google's diagnostic, but never echo the runtime credential.
                        message = message.replace(self._key, "[REDACTED]")
                        message = re.sub(r"AIza[\w-]{20,}", "[REDACTED]", message)
                        result["error"] += " Google: " + " ".join(message.split())[:500]
                    if problem.get("status") in {"INVALID_ARGUMENT", "PERMISSION_DENIED", "UNAUTHENTICATED", "RESOURCE_EXHAUSTED", "UNAVAILABLE", "INTERNAL"}:
                        result["google_status"] = problem["status"]
                except (ValueError, TypeError, AttributeError):
                    pass
                return result
            data = response.json()
            if not isinstance(data, dict):
                return {"error": f"{api_name} returned invalid data. Retry later."}
            return data
        except requests.Timeout:
            return {"error": f"{api_name} timed out. Retry or compare fewer candidates."}
        except requests.RequestException:
            return {"error": f"Cannot connect to {api_name}. Check the server network and retry."}
        except ValueError:
            return {"error": f"{api_name} returned unreadable data. Retry later."}

    def route(self, member, candidate, meeting_time=None):
        now = datetime.now(timezone.utc)
        mode = member.get("travel_mode", "TRANSIT")
        body = {"origin":{"address":member["address"]}, "destination":{"address":candidate["address"]},
                "travelMode":mode, "languageCode":"en-US", "units":"IMPERIAL"}
        if mode == "TRANSIT":
            body["transitPreferences"] = {"allowedTravelModes":["SUBWAY","TRAIN","BUS","LIGHT_RAIL","RAIL"]}
            if meeting_time:
                body["arrivalTime"] = parse_time(meeting_time).astimezone(timezone.utc).isoformat()
                assumption = "Transit route requested to arrive by the meeting time; actual service may arrive earlier."
            else:
                body["departureTime"] = (now+timedelta(minutes=1)).isoformat()
                assumption = "Transit departing approximately now, not a future scheduled meetup."
        else:
            departure = max(now+timedelta(minutes=1),parse_time(meeting_time).astimezone(timezone.utc)-timedelta(hours=1)) if meeting_time else now+timedelta(minutes=1)
            body.update(routingPreference="TRAFFIC_AWARE",departureTime=departure.isoformat())
            assumption = f"Driving traffic estimated for departure {departure.isoformat()} (about 1 hour before the meeting when possible). Excludes parking and pickup waits; recheck near departure."
        data = self._request(ROUTES_URL,body,ROUTE_FIELDS,"Routes API")
        if "error" in data:
            return data
        if not data.get("routes"):
            return {"error":"No route found. Try a precise station/street address or another candidate/time/mode."}
        try:
            route = data["routes"][0]
            duration = seconds(route.get("duration"))
            legs = route.get("legs",[])
            steps = [s for leg in legs for s in leg.get("steps",[])]
            lines, boardings = [], []
            last_arrival, walk_after = None, 0
            for step in steps:
                if step.get("travelMode") == "TRANSIT":
                    details = step.get("transitDetails",{})
                    line, stops = details.get("transitLine",{}), details.get("stopDetails",{})
                    lines.append({"line":line.get("nameShort") or line.get("name"),
                                  "vehicle":line.get("vehicle",{}).get("type"),
                                  "from":stops.get("departureStop",{}).get("name"),
                                  "to":stops.get("arrivalStop",{}).get("name"),
                                  "departure_time":stops.get("departureTime"), "arrival_time":stops.get("arrivalTime")})
                    if stops.get("departureTime"):
                        boardings.append(stops["departureTime"])
                    last_arrival, walk_after = stops.get("arrivalTime"), 0
                elif last_arrival:
                    walk_after += seconds(step.get("staticDuration","0s"))
            actual_arrival = parse_time(last_arrival).astimezone(timezone.utc)+timedelta(seconds=walk_after) if last_arrival else None
            start = actual_arrival-timedelta(seconds=duration) if actual_arrival else None
            return {"duration_seconds":duration, "duration_minutes":round(duration/60,1),
                    "distance_meters":route.get("distanceMeters"),
                    "walking_meters":sum(s.get("distanceMeters",0) for s in steps if s.get("travelMode") == "WALK"),
                    "transit_lines":lines, "destination_location":legs[-1].get("endLocation",{}).get("latLng") if legs else None,
                    "scheduled_departure_time":start.isoformat() if start else None,
                    "scheduled_arrival_time":actual_arrival.isoformat() if actual_arrival else None,
                    "first_boarding_time":boardings[0] if boardings else None,
                    "time_assumption":assumption, "source":"Google Maps / Routes API", "observed_at":now.isoformat()}
        except (ValueError,TypeError,KeyError,IndexError,AttributeError):
            return {"error":"Routes API response lacks usable duration/schedule data. Retry before recommending this destination."}

    def places(self, lat, lng, category, keyword=None):
        center={"latitude":lat,"longitude":lng}
        circle={"center":center,"radius":1000.0}
        types={"restaurant":["restaurant"],"cafe":["cafe"],"activity":["park","museum","tourist_attraction"]}
        if category not in types:
            return {"error":"category must be restaurant, cafe or activity."}
        if keyword:
            body={"textQuery":f"{keyword} {category if category != 'activity' else 'things to do'}",
                  "locationBias":{"circle":circle},"pageSize":6,"languageCode":"en"}
            if category != "activity": body.update(includedType=category,strictTypeFiltering=True)
            endpoint="searchText"
        else:
            body={"includedTypes":types[category],"locationRestriction":{"circle":circle},
                  "maxResultCount":6,"rankPreference":"POPULARITY","languageCode":"en"}
            endpoint="searchNearby"
        data=self._request(PLACES_URL+endpoint,body,PLACE_FIELDS,"Places API (New)")
        if "error" in data: return data
        places=[]
        try:
            for place in data.get("places",[]):
                location=place.get("location")
                if not location or _distance(lat,lng,location)>1000 or place.get("businessStatus") in ("CLOSED_PERMANENTLY","CLOSED_TEMPORARILY"):
                    continue
                if category=="activity" and place.get("primaryType")=="dog_park": continue
                places.append({"place_id":place.get("id"),"name":place.get("displayName",{}).get("text"),
                               "address":place.get("formattedAddress"),"location":location,
                               "type":place.get("primaryType"),"maps_url":place.get("googleMapsUri"),
                               "rating":place.get("rating"),"rating_count":place.get("userRatingCount"),
                               "price_level":place.get("priceLevel"),"open_now":place.get("currentOpeningHours",{}).get("openNow"),
                               "attributions":place.get("attributions",[])})
                if len(places)==3: break
        except (ValueError,TypeError,KeyError,AttributeError):
            return {"error":"Places returned incomplete location data. Retry the search."}
        return {"places":places,"source":"Google Maps / Places API (New)",
                "observed_at":datetime.now(timezone.utc).isoformat(),"radius_meters":1000,
                "note":"Straight-line radius, not walking distance. Open-now is not future opening hours. Listings may be seasonal; verify availability. Missing price/rating/hours means unknown. Recheck all routes to the exact venue before claiming it is fair."}
