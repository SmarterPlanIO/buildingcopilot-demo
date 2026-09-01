# -*- coding: utf-8 -*-
"""T19 : que fait le LLM client autour d'un appel PALIM_copro_overview ? (Langfuse)"""
import base64, json, os, urllib.request
from datetime import datetime
pk, sk, host = os.environ["LF_PK"], os.environ["LF_SK"], os.environ["LF_HOST"]
auth = base64.b64encode(f"{pk}:{sk}".encode()).decode()


def get(path):
    req = urllib.request.Request(host + path, headers={"Authorization": "Basic " + auth})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


traces = []
for page in range(1, 40):
    data = get(f"/api/public/traces?limit=100&page={page}").get("data", [])
    if not data:
        break
    traces.extend(data)


def ts(t):
    return datetime.fromisoformat(t["timestamp"].replace("Z", "+00:00"))


traces.sort(key=ts)
ov = [t for t in traces if t.get("name") == "PALIM_copro_overview"]
print(f"{len(traces)} traces ; {len(ov)} appels copro_overview ; sessionId renseigne sur {sum(1 for t in traces if t.get('sessionId'))} traces")
for o in ov:
    t0 = ts(o)
    win = [t for t in traces if abs((ts(t) - t0).total_seconds()) <= 900]
    seq = " -> ".join((t.get("name") or "?").replace("PALIM_", "") + ("*" if t is o else "") for t in win)
    inp = o.get("input")
    try:
        inp = json.loads(inp) if isinstance(inp, str) else inp
    except Exception:
        pass
    out = o.get("output")
    size = len(json.dumps(out, ensure_ascii=False)) if out is not None else 0
    lat = o.get("latency")
    code = (inp or {}).get("code_ncg") if isinstance(inp, dict) else None
    print(f"\n  {o['timestamp'][:16]} overview({code}) sortie={size} o latence={lat}s")
    print(f"     fenetre +/-15 min : {seq}")
    # que contenait la sortie (narratif v1 a l'epoque) ?
    if isinstance(out, dict):
        print(f"     cles sortie : {sorted(out.keys())}")
