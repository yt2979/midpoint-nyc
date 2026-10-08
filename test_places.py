"""Check Places API (New) nearby search without saving credentials or results."""

import argparse
import getpass
import json
import sys
import urllib.error
import urllib.request


ENDPOINT = "https://places.googleapis.com/v1/places:searchNearby"
FIELD_MASK = "places.displayName,places.formattedAddress,places.primaryType,places.googleMapsUri"
SEARCHES = [
    ("餐厅", ["restaurant"]),
    ("游玩地点（公园、博物馆、景点）", ["park", "museum", "tourist_attraction"]),
]


def make_body(types):
    return {
        "includedTypes": types,
        "maxResultCount": 3,
        "rankPreference": "DISTANCE",
        "languageCode": "en",
        "locationRestriction": {
            "circle": {
                "center": {"latitude": 40.7359, "longitude": -73.9911},
                "radius": 1000.0,
            }
        },
    }


def check_places(api_key, label, body):
    print(f"\n{label}", flush=True)
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

    places = data.get("places", [])
    if not places:
        print(f"HTTP {status}，但此搜索没有返回地点。")
        return False
    print(f"HTTP {status} | 返回 {len(places)} 个地点（Google Maps 数据）")
    complete = True
    for index, place in enumerate(places, 1):
        name = place.get("displayName", {}).get("text")
        address = place.get("formattedAddress")
        if not name or not address:
            complete = False
        print(f"{index}. {name or '名称缺失'}")
        print(f"   地址：{address or '地址缺失'}")
        print(f"   类型：{place.get('primaryType', '未提供')}")
        print(f"   地图：{place.get('googleMapsUri', '未提供')}")
    if not complete:
        print("有地点缺少名称或地址，不能算完整验证通过。")
    return complete


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="查看请求内容，不调用 API")
    args = parser.parse_args()
    if args.dry_run:
        print(json.dumps({
            "endpoint": ENDPOINT,
            "field_mask": FIELD_MASK,
            "requests": [make_body(types) for _, types in SEARCHES],
        }, indent=2, ensure_ascii=False))
        return 0
    if not sys.stdin.isatty():
        print("请在本地交互式终端运行，以便隐藏输入 API key。")
        return 1
    api_key = getpass.getpass("粘贴原来的 Google Maps API key，然后按回车（输入不会显示）：").strip()
    if not api_key:
        print("没有输入密钥，未发送请求。")
        return 1
    print("查询范围：Union Square 附近，直线半径 1 公里。")
    print("将发送两次 Nearby Search 请求；每次最多返回 3 个地点。", flush=True)
    results = [check_places(api_key, label, make_body(types)) for label, types in SEARCHES]
    if all(results):
        print("\n✓ 两类地点查询均验证通过。")
        print("本次只验证地点信息，不包含价格、营业时间、预约或门票数据。")
        return 0
    print("\n至少一类查询未通过；请复制上面的结果用于排查。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
