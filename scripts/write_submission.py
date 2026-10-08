"""Write the assignment manifest only after checking a real deployed service."""
import argparse
import json
from pathlib import Path
from urllib.parse import urlparse

import requests


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url",help="Your real HTTPS Cloud Run service URL")
    args=parser.parse_args()
    url=args.url.rstrip("/")
    parsed=urlparse(url)
    if parsed.scheme!="https" or not parsed.hostname or not parsed.hostname.endswith(".run.app") or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
        parser.error("Use the root HTTPS .run.app service URL without credentials/query/fragment.")
    try:
        health=requests.get(url+"/health",timeout=20)
        health.raise_for_status()
        if health.json().get("status")!="ok" or not health.json().get("maps_configured"):
            parser.error("Deployed server isn't healthy with a configured Maps key yet.")
        index=requests.get(url,timeout=20)
        index.raise_for_status()
        if "THE MIDPOINT NYC" not in index.text:
            parser.error("This URL does not serve THE MIDPOINT NYC.")
    except (requests.RequestException,ValueError):
        parser.error("Cannot verify deployed HTTPS service. Check public access and the URL, then retry.")
    path=Path(__file__).resolve().parents[1]/"submission.json"
    path.write_text(json.dumps({"deploy_url":url,"authors":["yt2979"]},indent=2)+"\n")
    print(f"Wrote {path}. Now run all three README queries on the deployed site before submitting.")


if __name__=="__main__": main()
