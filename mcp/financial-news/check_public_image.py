"""Verify the exact deployment digest is pullable without registry credentials."""

import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


def check(image):
    match = re.fullmatch(r"ghcr.io/([a-z0-9._/-]+)@(sha256:[a-f0-9]{64})", image)
    if not match:
        raise ValueError("An immutable ghcr.io image digest is required")
    repository, digest = match.groups()
    query = urllib.parse.urlencode(
        {"service": "ghcr.io", "scope": f"repository:{repository}:pull"}
    )
    with urllib.request.urlopen(f"https://ghcr.io/token?{query}", timeout=30) as response:
        token = json.load(response)["token"]
    request = urllib.request.Request(
        f"https://ghcr.io/v2/{repository}/manifests/{digest}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": ", ".join(
                [
                    "application/vnd.oci.image.index.v1+json",
                    "application/vnd.oci.image.manifest.v1+json",
                    "application/vnd.docker.distribution.manifest.list.v2+json",
                    "application/vnd.docker.distribution.manifest.v2+json",
                ]
            ),
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        actual = "sha256:" + hashlib.sha256(response.read()).hexdigest()
    if actual != digest:
        raise ValueError("Anonymous manifest digest does not match the deployment image")


if __name__ == "__main__":
    for attempt in range(6):
        try:
            check(sys.argv[1])
            print("Anonymous pull verified for the immutable deployment digest.")
            break
        except (urllib.error.URLError, ValueError, KeyError):
            if attempt == 5:
                sys.exit(
                    "Anonymous GHCR pull failed. In GitHub Packages, open "
                    "option-income-lab-financial-news-mcp > Package settings > "
                    "Change visibility > Public, then rerun this workflow. "
                    "No short-lived registry credential will be deployed."
                )
            time.sleep(10)
