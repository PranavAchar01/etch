"""Serve deploy/etch-app on 127.0.0.1 with caching off, so every reload picks up the latest edits.

  python3 deploy/serve_page.py [port]      (run.sh starts it on :8766)

python -m http.server sends Last-Modified and no Cache-Control, so browsers reuse old JS/CSS without asking.
"""

import functools
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent / "etch-app"


class NoCache(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store, max-age=0")
        super().end_headers()

    def send_head(self):
        # no 304s from If-Modified-Since: always the file on disk
        if "If-Modified-Since" in self.headers:
            del self.headers["If-Modified-Since"]
        return super().send_head()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8766
    handler = functools.partial(NoCache, directory=str(ROOT))
    ThreadingHTTPServer(("127.0.0.1", port), handler).serve_forever()
