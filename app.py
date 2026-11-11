"""
app.py - small web front-end for cloud-prm, used for the Kubernetes deployment.

The research code (src/) is a batch pipeline: it trains forecasters and
replays allocation policies, then writes CSVs. A batch job is awkward to
demonstrate on a cluster, so this file wraps it in a tiny web service:

    /             HTML dashboard with the latest results
    /api/results  the same results as JSON
    /api/info     which pod and node answered this request
    /healthz      liveness probe  - "the process is alive"
    /readyz       readiness probe - "results are computed, send me traffic"

When the container starts, a background thread runs the synthetic demo once
(the same thing `python run.py --demo --policy` does) and keeps the results
in memory. Until that finishes, /readyz returns 503, so Kubernetes does not
send users to a pod that has nothing to show yet.

Settings come from environment variables, which the Kubernetes ConfigMap
supplies:
    APP_N_MACHINES   number of synthetic VMs to simulate (default 3)
    APP_MODELS       comma-separated forecaster list
    APP_VERSION      label shown on the page, e.g. v1
    POD_NAME, NODE_NAME   injected by Kubernetes (Downward API)
"""
from __future__ import annotations

import os
import socket
import threading
import time
import traceback

from flask import Flask, jsonify, render_template_string

from src.config import Config
from src import experiment

app = Flask(__name__)

N_MACHINES = int(os.getenv("APP_N_MACHINES", "3"))
MODELS = [m.strip() for m in os.getenv(
    "APP_MODELS", "naive,seasonal_naive,ridge,hgb").split(",") if m.strip()]
VERSION = os.getenv("APP_VERSION", "v1")
POD_NAME = os.getenv("POD_NAME", socket.gethostname())
NODE_NAME = os.getenv("NODE_NAME", "local")

STATE = {
    "status": "starting",      # starting -> running -> ready | failed
    "started_at": None,
    "finished_at": None,
    "seconds": None,
    "forecasters": [],
    "policies": [],
    "error": None,
}


def _compute() -> None:
    """Run the demo experiment once and store the results in STATE."""
    STATE["status"] = "running"
    STATE["started_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    t0 = time.time()
    try:
        cfg = Config.load(None)
        cfg.use_synthetic = True
        cfg.n_machines = N_MACHINES
        cfg.models = MODELS
        cfg.output_dir = "/tmp/outputs"        # writable inside the container

        res = experiment.run(cfg)
        cols = ["model", "MAE_mean", "RMSE_mean",
                "sla_violation_pct_mean", "mean_utilisation_pct_mean"]
        summary = res["summary"][[c for c in cols if c in res["summary"].columns]]
        STATE["forecasters"] = summary.round(3).to_dict(orient="records")

        pc = experiment.run_policy_comparison(cfg)
        if not pc.empty:
            keep = ["provisioning_delay", "policy", "sla_violation_pct",
                    "mean_utilisation_pct", "mean_idle_capacity", "cost_J"]
            STATE["policies"] = pc[keep].round(3).to_dict(orient="records")

        STATE["status"] = "ready"
    except Exception:                          # keep the pod alive, show the error
        STATE["status"] = "failed"
        STATE["error"] = traceback.format_exc()
    finally:
        STATE["seconds"] = round(time.time() - t0, 1)
        STATE["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")


threading.Thread(target=_compute, daemon=True).start()


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
{% if s.status in ('starting', 'running') %}<meta http-equiv="refresh" content="5">{% endif %}
<title>cloud-prm on Kubernetes</title>
<style>
 body{font-family:system-ui,Segoe UI,Arial,sans-serif;margin:0;background:#f5f7fa;color:#1f2933}
 header{background:#0f4761;color:#fff;padding:22px 32px}
 header h1{margin:0;font-size:22px} header p{margin:6px 0 0;opacity:.85;font-size:14px}
 main{max-width:960px;margin:24px auto;padding:0 20px}
 .badges>span{display:inline-block;background:#fff;border:1px solid #d9e2ec;border-radius:6px;
   padding:6px 10px;margin:0 8px 8px 0;font-size:13px}
 .card{background:#fff;border:1px solid #d9e2ec;border-radius:8px;padding:18px 22px;margin-bottom:20px}
 h2{font-size:17px;margin:0 0 10px} table{border-collapse:collapse;width:100%;font-size:14px}
 th,td{text-align:left;padding:7px 10px;border-bottom:1px solid #e4e7eb}
 th{background:#f0f4f8} .note{font-size:13px;color:#52606d}
 .ok{color:#18794e;font-weight:600} .wait{color:#b7791f;font-weight:600} .bad{color:#c53030;font-weight:600}
 pre{white-space:pre-wrap;font-size:12px}
</style></head><body>
<header><h1>AI-Driven Predictive Resource Management for Cloud Data Centres</h1>
<p>cloud-prm &middot; containerised with Docker &middot; running on Azure Kubernetes Service</p></header>
<main>
<div class="badges">
 <span>Served by pod: <b>{{ pod }}</b></span>
 <span>Node: <b>{{ node }}</b></span>
 <span>Version: <b>{{ version }}</b></span>
 <span>Status:
  {% if s.status == 'ready' %}<span class="ok">ready</span>
  {% elif s.status == 'failed' %}<span class="bad">failed</span>
  {% else %}<span class="wait">{{ s.status }} (page refreshes every 5 s)</span>{% endif %}</span>
</div>

{% if s.status == 'ready' %}
<div class="card"><h2>Forecaster accuracy (synthetic demo, {{ n }} VMs)</h2>
<table><tr><th>Model</th><th>MAE</th><th>RMSE</th><th>SLA violation %</th><th>Utilisation %</th></tr>
{% for r in s.forecasters %}<tr><td>{{ r.model }}</td><td>{{ r.MAE_mean }}</td><td>{{ r.RMSE_mean }}</td>
<td>{{ r.sla_violation_pct_mean }}</td><td>{{ r.mean_utilisation_pct_mean }}</td></tr>{% endfor %}
</table><p class="note">Lower MAE means a more accurate forecast. The project's point is that
the most accurate model is not always the one with the fewest SLA violations.</p></div>

<div class="card"><h2>Predictive vs reactive allocation</h2>
<table><tr><th>Delay</th><th>Policy</th><th>SLA violation %</th><th>Utilisation %</th><th>Idle capacity</th><th>Cost J</th></tr>
{% for r in s.policies %}<tr><td>{{ r.provisioning_delay }}</td><td>{{ r.policy }}</td><td>{{ r.sla_violation_pct }}</td>
<td>{{ r.mean_utilisation_pct }}</td><td>{{ r.mean_idle_capacity }}</td><td>{{ r.cost_J }}</td></tr>{% endfor %}
</table><p class="note">Both policies are calibrated to the same SLA target, so compare
utilisation and idle capacity at similar violation rates.</p></div>
<p class="note">Computed in {{ s.seconds }} s, finished {{ s.finished_at }} (container time).</p>
{% elif s.status == 'failed' %}
<div class="card"><h2>The experiment failed</h2><pre>{{ s.error }}</pre></div>
{% else %}
<div class="card"><h2>Computing results&hellip;</h2>
<p>This pod is training the forecasters and replaying the allocation policies.
It takes under a minute. Kubernetes keeps this pod out of the Service until it is ready.</p></div>
{% endif %}
</main></body></html>"""


@app.get("/")
def index():
    return render_template_string(PAGE, s=STATE, pod=POD_NAME, node=NODE_NAME,
                                  version=VERSION, n=N_MACHINES)


@app.get("/api/results")
def results():
    return jsonify(pod=POD_NAME, version=VERSION, **STATE)


@app.get("/api/info")
def info():
    return jsonify(pod=POD_NAME, node=NODE_NAME, version=VERSION, status=STATE["status"])


@app.get("/api/simulate")
def simulate():
    """Generate a fresh 7-day synthetic VM trace and replay the reactive
    threshold autoscaler on it at four provisioning delays.

    Each call costs roughly a tenth of a CPU-second, which makes this the
    endpoint used to put load on the pods during the autoscaling (HPA) demo.
    """
    import numpy as np
    from src.synthetic import make_machine
    from src.simulator import replay
    from src.policy import ReactiveThresholdPolicy

    rng = np.random.default_rng()
    trace = make_machine(2016, rng)["cpu_usage_pct"]
    demand = trace.interpolate().bfill().ffill().to_numpy()
    rows = []
    for delay in (0, 1, 3, 6):
        pol = ReactiveThresholdPolicy(initial=float(np.mean(demand[:20])))
        m = replay(demand, pol, provisioning_delay=delay).metrics
        rows.append({"provisioning_delay": delay,
                     "sla_violation_pct": round(m["sla_violation_pct"], 2),
                     "mean_utilisation_pct": round(m["mean_utilisation_pct"], 2)})
    return jsonify(pod=POD_NAME, policy="reactive", intervals=len(demand), results=rows)


@app.get("/healthz")
def healthz():
    return jsonify(status="alive"), 200


@app.get("/readyz")
def readyz():
    if STATE["status"] == "ready":
        return jsonify(status="ready"), 200
    return jsonify(status=STATE["status"]), 503


if __name__ == "__main__":                     # local run: python app.py
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
