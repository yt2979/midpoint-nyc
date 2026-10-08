"""Verify Google Routes access with two requests; never save the API key."""

import argparse
import getpass
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone


ENDPOINT = "https://routes.googleapis.com/directions/v2:computeRoutes"
FIELD_MASK = ",".join([
    "routes.duration",
    "routes.distanceMeters",
    "routes.legs.steps.travelMode",
    "routes.legs.steps.distanceMeters",
    "routes.legs.steps.transitDetails",
])


def make_body(mode, origin, destination, departure_time):
    body = {
        "origin": {"address": origin},
        "destination": {"address": destination},
        "travelMode": mode,
        "departureTime": departure_time,
        "languageCode": "en-US",
        "units": "METRIC",
    }
    if mode == "DRIVE":
        body["routingPreference"] = "TRAFFIC_AWARE"
    else:
        body["transitPreferences"] = {"allowedTravelModes": ["SUBWAY"]}
    return body


def check_route(api_key, body):
    request = urllib.request.Request(
        ENDPOINT,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": FIELD_MASK,
        },
        method="POST",
    )
    label = "公共交通（优先地铁）" if body["travelMode"] == "TRANSIT" else "驾车（考虑交通状况）"
    print(f"\n{label}", flush=True)
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            status = response.status
            data = json.load(response)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            message = json.loads(raw).get("error", {}).get("message", raw)
        except json.JSONDecodeError:
            message = raw
        print(f"失败：HTTP {exc.code} — {str(message).replace(api_key, '[REDACTED]')[:1500]}")
        return False
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        print(f"失败：{str(exc).replace(api_key, '[REDACTED]')[:500]}")
        return False

    routes = data.get("routes", [])
    if not routes:
        print(f"HTTP {status}，但没有找到路线；这不能算路线验证通过。")
        return False
    route = routes[0]
    duration = route.get("duration", "")
    distance = route.get("distanceMeters")
    try:
        if not duration.endswith("s") or distance is None:
            raise ValueError("响应缺少 duration 或 distanceMeters")
        minutes = float(duration[:-1]) / 60
        kilometers = float(distance) / 1000
    except (ValueError, TypeError) as exc:
        print(f"HTTP {status}，但路线字段不能解析：{exc}")
        return False
    print(f"HTTP {status} | 预计 {minutes:.1f} 分钟 | {kilometers:.2f} 公里")
    steps = [step for leg in route.get("legs", []) for step in leg.get("steps", [])]
    if body["travelMode"] == "TRANSIT":
        walk_meters = sum(step.get("distanceMeters", 0) for step in steps if step.get("travelMode") == "WALK")
        print(f"步行路段合计：{walk_meters} 米")
        for step in steps:
            details = step.get("transitDetails")
            if not details:
                continue
            line = details.get("transitLine", {})
            stops = details.get("stopDetails", {})
            start = stops.get("departureStop", {}).get("name", "未知站点")
            end = stops.get("arrivalStop", {}).get("name", "未知站点")
            vehicle = line.get("vehicle", {}).get("type", "TRANSIT")
            print(f"  {vehicle} {line.get('nameShort') or line.get('name', '')}: {start} → {end}")
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", default="Broadway and West 116th Street, Manhattan, New York, NY")
    parser.add_argument("--destination", default="Times Square, Manhattan, New York, NY")
    parser.add_argument("--dry-run", action="store_true", help="检查请求内容，不调用接口")
    args = parser.parse_args()
    # Depart in one minute so the request time is still in the future when sent.
    from datetime import timedelta
    departure = (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat(timespec="seconds").replace("+00:00", "Z")
    bodies = [make_body(mode, args.origin, args.destination, departure) for mode in ("TRANSIT", "DRIVE")]
    if args.dry_run:
        print(json.dumps({"endpoint": ENDPOINT, "field_mask": FIELD_MASK, "requests": bodies}, indent=2, ensure_ascii=False))
        return 0
    if not sys.stdin.isatty():
        print("请在本地交互式终端运行此脚本，以便隐藏输入 API key。")
        return 1
    api_key = getpass.getpass("粘贴 Routes API key，然后按回车（输入不会显示）：").strip()
    if not api_key:
        print("没有输入密钥，未发送请求。")
        return 1
    print(f"起点：{args.origin}\n终点：{args.destination}\n将发送两次 Routes API 请求。", flush=True)
    results = [check_route(api_key, body) for body in bodies]
    if all(results):
        print("\n✓ 两种路线均验证通过。驾车时间不包含 Uber 接车等待，也不提供 Uber 报价。")
        return 0
    print("\n至少一种路线未通过，请复制上面的错误信息用于排查。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
