"""Live end-to-end check on the REAL screen (not part of unittest discover).

    uv run python tests/live_e2e.py

Serves the fixture site, runs the agent against it, then asserts that the site
saw the Save click and the run finished `done` with a receipt that verifies.
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests", "fixtures", "site"))
import server  # noqa: E402

site = server.start(8780)
proc = subprocess.run([sys.executable, "-m", "agent", "run", "Open Settings and save the changes",
                       "--start-url", site.url], cwd=ROOT)
print("fixture server saw:", site.visited)
run_dirs = sorted(os.listdir(os.path.join(ROOT, "receipts")))
verified = subprocess.run([sys.executable, "-m", "agent", "verify", os.path.join(ROOT, "receipts", run_dirs[-1])], cwd=ROOT)
ok = proc.returncode == 0 and site.saved and verified.returncode == 0
print("LIVE E2E", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
