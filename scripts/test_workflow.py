"""Full workflow test: scan → select → monitor."""
import requests
import time

BASE = "http://127.0.0.1:8420"

# 1. Scan
print("1. Scanning markets...")
r = requests.post(f"{BASE}/api/scan?strategy=sports_volatility", timeout=30)
assert r.status_code == 200, f"Scan failed: {r.text}"
data = r.json()
candidates = data["candidates"]
print(f"   Found {len(candidates)} candidates")

# Pick first 5 token_ids
token_ids = [c["token_id"] for c in candidates[:5]]
print(f"   Selected {len(token_ids)} tokens: {[t[:15]+'...' for t in token_ids]}")

# 2. Set watchlist
print("2. Setting watchlist...")
r = requests.post(f"{BASE}/api/sim/watchlist", json={"token_ids": token_ids}, timeout=10)
assert r.status_code == 200, f"Watchlist failed: {r.text}"
print(f"   Watchlist set: {r.json()['count']} tokens")

# 3. Start monitoring
print("3. Starting simulation...")
r = requests.post(f"{BASE}/api/sim/start?strategy=sports_volatility&tick_interval=15", timeout=10)
assert r.status_code == 200, f"Start failed: {r.text}"
run = r.json()
print(f"   Run started: {run['run_id']}")

# 4. Wait for a tick cycle and check state
print("4. Waiting 35s for ticks...")
time.sleep(35)

r = requests.get(f"{BASE}/api/sim/state", timeout=10)
state = r.json()
print(f"   Status: {state['status']}, Tick: {state.get('tick_count', '?')}")
print(f"   Balance: {state.get('balance', '?')}")
print(f"   Watchlist: {len(state.get('watchlist', []))} tokens")

# 5. Stop
print("5. Stopping simulation...")
r = requests.post(f"{BASE}/api/sim/stop", timeout=10)
print(f"   Stopped: {r.json()}")

print("\nAll workflow steps passed!")
