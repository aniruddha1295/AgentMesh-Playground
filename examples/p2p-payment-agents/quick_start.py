#!/usr/bin/env python3
"""Cross-platform launcher: 1 bootstrap + 2 merchants + 1 buyer."""
import subprocess, sys, time, os, signal

PY = sys.executable
DIR = os.path.dirname(os.path.abspath(__file__))

# ANSI colors (enable VT on Windows 10+; fall back to no color)
try:
    os.system("")
    GREEN, CYAN, YELLOW, RED, RESET = "\033[92m", "\033[96m", "\033[93m", "\033[91m", "\033[0m"
except Exception:
    GREEN = CYAN = YELLOW = RED = RESET = ""

def status(color, tag, msg):
    print(f"{color}[{tag}]{RESET} {msg}", flush=True)

def parse_multiaddr(proc, timeout=3):
    """Read bootstrap stdout until we find the /ip4/... multiaddr line."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        line = proc.stdout.readline()
        if not line:
            time.sleep(0.1)
            continue
        text = line.strip()
        if text:
            print(f"  bootstrap | {text}", flush=True)
        if "/ip4/" in text and "/p2p/" in text:
            # Extract the multiaddr (may have leading spaces or log prefix)
            for part in text.split():
                if part.startswith("/ip4/"):
                    return part
    return None

def main():
    procs = []

    def cleanup():
        status(YELLOW, "CLEANUP", "Shutting down all subprocesses...")
        for p in procs:
            try: p.terminate()
            except OSError: pass
        for p in procs:
            try: p.wait(timeout=3)
            except subprocess.TimeoutExpired: p.kill()

    def on_signal(sig, _frame):
        cleanup(); sys.exit(1)

    signal.signal(signal.SIGINT, on_signal)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, on_signal)

    status(GREEN, "START", "Launching AP2 Payment Agents demo\n")

    # 1) Bootstrap node
    status(CYAN, "BOOT", "Starting bootstrap node on port 8000...")
    boot = subprocess.Popen(
        [PY, os.path.join(DIR, "bootstrap_node.py"), "--port", "8000"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    procs.append(boot)

    multiaddr = parse_multiaddr(boot, timeout=10)
    if not multiaddr:
        status(RED, "ERROR", "Failed to get multiaddr from bootstrap node")
        cleanup(); sys.exit(1)
    status(GREEN, "BOOT", f"Multiaddr: {multiaddr}\n")

    # 2) Merchants
    merchants = [("QuickShoot Studios", "8001", "350"), ("Premium Films", "8002", "450")]
    for name, port, price in merchants:
        status(CYAN, "MERCHANT", f"Starting {name} on port {port} (${price})...")
        p = subprocess.Popen(
            [PY, os.path.join(DIR, "merchant_agent.py"),
             "--port", port, "--name", name, "--price", price, "--bootstrap", multiaddr],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        procs.append(p)

    status(YELLOW, "WAIT", "Letting merchants connect (5s)...")
    time.sleep(5)

    # 3) Buyer
    status(CYAN, "BUYER", "Starting buyer on port 8003 (budget $400)...")
    buyer = subprocess.Popen(
        [PY, os.path.join(DIR, "buyer_agent.py"),
         "--port", "8003", "--budget", "400", "--bootstrap", multiaddr],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    procs.append(buyer)

    try:
        buyer.wait(timeout=30)
        for line in (buyer.stdout.readlines() if buyer.stdout else []):
            print(f"  buyer | {line.rstrip()}", flush=True)
    except subprocess.TimeoutExpired:
        status(RED, "TIMEOUT", "Buyer did not finish within 30 seconds")

    # Summary
    print()
    status(GREEN, "DONE", "Demo complete!")
    status(GREEN, "SUMMARY",
           f"Bootstrap={multiaddr}  |  Merchants: {len(merchants)}  |  "
           f"Buyer exit: {buyer.returncode}")
    cleanup()

if __name__ == "__main__":
    main()
