"""Console client for the notification stream (demo of the WebSocket flow).

    python -m tools.ws_client ana [--base http://localhost:8000]

1. mints a maintainer token (same JWT_SECRET as the service),
2. POST /history/notifications  -> one-time WebSocket URI,
3. opens the WebSocket and prints every `task.finished` notification as it arrives.
"""
import argparse
import asyncio
import json
import os
import sys
import urllib.request

import websockets

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.mint_token import mint  # noqa: E402


async def main(user: str, base: str) -> None:
    req = urllib.request.Request(f"{base}/api/manager/history/notifications", method="POST",
                                 headers={"Authorization": f"Bearer {mint(user, 'maintainer')}"})
    session = json.load(urllib.request.urlopen(req))
    print("session:", session["uri"])
    async with websockets.connect(session["uri"]) as ws:
        print("connected, waiting for notifications (Ctrl+C to stop)...")
        async for message in ws:
            t = json.loads(message)["task"]
            print(f"[{t['date']}] job {t['id']}  {t['username']}@{t['computer']}  "
                  f"cluster={t['id_cluster']}  {t['state']}: {t['status']}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("user")
    ap.add_argument("--base", default="http://localhost:8000")
    a = ap.parse_args()
    try:
        asyncio.run(main(a.user, a.base))
    except KeyboardInterrupt:
        pass
