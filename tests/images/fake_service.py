"""A fake supervised child for the supervisor tests (standard library only).

    python fake_service.py --name api --log events.log [--port N] [--ready-delay S]
        [--die-after S --exit-code C] [--on-term exit|ignore|code:N] [--term-delay S]
        [--grandchild PIDFILE] [--dump-env FILE]

Every lifecycle event is appended to ``--log`` as ``<event> <name> <pid>``.
"""

import argparse
import http.server
import json
import os
import signal
import subprocess
import sys
import threading
import time


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--name", required=True)
    p.add_argument("--log", required=True)
    p.add_argument("--port", type=int)
    p.add_argument("--ready-delay", type=float, default=0.0)
    p.add_argument("--die-after", type=float)
    p.add_argument("--exit-code", type=int, default=3)
    p.add_argument("--on-term", default="exit")
    p.add_argument("--term-delay", type=float, default=0.0)
    p.add_argument("--grandchild")
    p.add_argument("--dump-env")
    args = p.parse_args()

    def event(kind):
        with open(args.log, "a", encoding="utf-8") as fh:
            fh.write(f"{kind} {args.name} {os.getpid()}\n")

    event("start")
    if args.dump_env:
        with open(args.dump_env, "w", encoding="utf-8") as fh:
            json.dump(dict(os.environ), fh)
    if args.grandchild:
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
        with open(args.grandchild, "w", encoding="utf-8") as fh:
            fh.write(str(child.pid))

    def on_term(signum, _frame):
        event(f"signal{signum}")
        if args.on_term == "ignore":
            return
        time.sleep(args.term_delay)
        event("exit")
        os._exit(int(args.on_term.split(":", 1)[1]) if args.on_term.startswith("code:") else 0)

    signal.signal(signal.SIGTERM, on_term)
    signal.signal(signal.SIGQUIT, on_term)
    signal.signal(signal.SIGINT, on_term)
    if args.port:
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"status": "ok"}')

            def log_message(self, *a):
                pass

        def serve():
            time.sleep(args.ready_delay)
            http.server.HTTPServer(("127.0.0.1", args.port), Handler).serve_forever()

        threading.Thread(target=serve, daemon=True).start()
    start = time.monotonic()
    while True:
        if args.die_after is not None and time.monotonic() - start >= args.die_after:
            event("die")
            os._exit(args.exit_code)
        time.sleep(0.05)


if __name__ == "__main__":
    main()
