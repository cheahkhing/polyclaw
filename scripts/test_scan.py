"""Quick test of the scan API endpoint."""
import requests

r = requests.post("http://127.0.0.1:8420/api/scan?strategy=sports_volatility", timeout=60)
print(f"Status: {r.status_code}")
data = r.json()
candidates = data.get("candidates", [])
print(f"Candidates: {len(candidates)}")
for c in candidates[:10]:
    print(f"  {c['score']:.1f} | {c['event_title'][:50]} | mid={c['midpoint']:.3f}")
if not candidates:
    print("No candidates matched filters.")
    print("Raw response:", data)
