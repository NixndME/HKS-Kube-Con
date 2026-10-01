"""K8s Showcase demo app.

A small Flask service that shows, live: which version/color is running,
who is hitting it (IP + time), the real blue/green pod mix read straight
from the Kubernetes API, and which commit/build produced the running image.

Everything here is read at request time, nothing is pre-baked at build time
except the version/color, which come in as environment variables set by the
Deploy task.
"""
import collections
import datetime
import os
import ssl
import urllib.request

from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

APP_VERSION = os.environ.get("APP_VERSION", "unknown")
APP_COLOR = os.environ.get("APP_COLOR", "unknown")
POD_NAME = os.environ.get("HOSTNAME", "unknown")
NODE_NAME = os.environ.get("NODE_NAME", "")
NAMESPACE = os.environ.get("POD_NAMESPACE", "k8s-showcase")

DEPLOY_COMMIT_SHA = os.environ.get("DEPLOY_COMMIT_SHA", "")
DEPLOY_COMMIT_AUTHOR = os.environ.get("DEPLOY_COMMIT_AUTHOR", "")
DEPLOY_COMMIT_MESSAGE = os.environ.get("DEPLOY_COMMIT_MESSAGE", "")
DEPLOY_SOURCE = os.environ.get("DEPLOY_SOURCE", "catalog")  # "catalog" or "gitops"

START_TIME = datetime.datetime.utcnow()
VISITORS = collections.deque(maxlen=25)

K8S_API = "https://kubernetes.default.svc"
SA_DIR = "/var/run/secrets/kubernetes.io/serviceaccount"


def _k8s_get(path):
    """Read-only call to the in-cluster Kubernetes API using this pod's own
    ServiceAccount token. Scoped by RBAC to list/get pods in this namespace
    only -- see k8s/rbac.yaml."""
    try:
        with open(f"{SA_DIR}/token") as f:
            token = f.read().strip()
        ctx = ssl.create_default_context(cafile=f"{SA_DIR}/ca.crt")
        req = urllib.request.Request(
            f"{K8S_API}{path}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )
        import json
        with urllib.request.urlopen(req, context=ctx, timeout=4) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


def live_pod_mix():
    """Real pod counts by color, read fresh from the Kubernetes API on every
    call -- not cached, not guessed from this pod's own env vars."""
    data = _k8s_get(f"/api/v1/namespaces/{NAMESPACE}/pods")
    counts = {"blue": 0, "green": 0}
    ready = {"blue": 0, "green": 0}
    if not data:
        return None
    for item in data.get("items", []):
        labels = (item.get("metadata") or {}).get("labels", {}) or {}
        slot = labels.get("slot")
        if slot not in counts:
            continue
        counts[slot] += 1
        statuses = (item.get("status") or {}).get("containerStatuses", []) or []
        if statuses and all(c.get("ready") for c in statuses):
            ready[slot] += 1
    total = counts["blue"] + counts["green"]
    if total == 0:
        return None
    return {
        "blue": counts["blue"], "blueReady": ready["blue"],
        "green": counts["green"], "greenReady": ready["green"],
        "bluePct": round(counts["blue"] / total * 100),
        "greenPct": round(counts["green"] / total * 100),
    }


def live_rollout():
    """Per-pod rollout detail: which individual pod is starting, serving live
    traffic, or terminating, right now. live_pod_mix() answers "what's the
    split" -- this answers "which exact pod is which", which is what actually
    makes a blue-green switch visible as it happens rather than as a single
    before/after percentage jump. "Serving" means the pod's slot matches the
    Service's current selector AND the pod is ready AND not mid-termination --
    a pod can exist and be ready without ever receiving traffic if it's the
    non-live slot (the normal in-between state right after a Deploy, before
    a Switch)."""
    pods_data = _k8s_get(f"/api/v1/namespaces/{NAMESPACE}/pods")
    svc_data = _k8s_get(f"/api/v1/namespaces/{NAMESPACE}/services/k8s-showcase")
    live_slot = ((svc_data or {}).get("spec") or {}).get("selector", {}).get("slot")
    pods = []
    for item in (pods_data or {}).get("items", []):
        labels = (item.get("metadata") or {}).get("labels", {}) or {}
        slot = labels.get("slot")
        if slot not in ("blue", "green"):
            continue
        meta = item.get("metadata") or {}
        status = item.get("status") or {}
        statuses = status.get("containerStatuses", []) or []
        ready = bool(statuses) and all(c.get("ready") for c in statuses)
        terminating = meta.get("deletionTimestamp") is not None
        started = status.get("startTime") or meta.get("creationTimestamp")
        age_seconds = None
        if started:
            try:
                started_dt = datetime.datetime.strptime(started, "%Y-%m-%dT%H:%M:%SZ")
                age_seconds = int((datetime.datetime.utcnow() - started_dt).total_seconds())
            except ValueError:
                pass
        pods.append({
            "name": meta.get("name", "unknown"),
            "slot": slot,
            "phase": "Terminating" if terminating else status.get("phase", "Unknown"),
            "ready": ready,
            "restarts": sum(c.get("restartCount", 0) for c in statuses),
            "ageSeconds": age_seconds,
            "serving": slot == live_slot and ready and not terminating,
        })
    pods.sort(key=lambda p: (p["slot"], p["name"]))
    return {"liveSlot": live_slot, "pods": pods}


def client_ip():
    fwd = request.headers.get("X-Forwarded-For", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.remote_addr or "unknown"


@app.before_request
def log_visitor():
    if request.path == "/" :
        VISITORS.appendleft({
            "ip": client_ip(),
            "time": datetime.datetime.utcnow().strftime("%H:%M:%S"),
            "userAgent": request.headers.get("User-Agent", "")[:60],
        })


@app.route("/")
def index():
    return render_template("index.html", version=APP_VERSION, color=APP_COLOR)


@app.route("/api/status")
def status():
    uptime = (datetime.datetime.utcnow() - START_TIME).total_seconds()
    return jsonify({
        "version": APP_VERSION,
        "color": APP_COLOR,
        "pod": POD_NAME,
        "node": NODE_NAME,
        "namespace": NAMESPACE,
        "uptimeSeconds": int(uptime),
        "visitors": list(VISITORS),
        "podMix": live_pod_mix(),
        "rollout": live_rollout(),
        "deploy": {
            "commitSha": DEPLOY_COMMIT_SHA,
            "commitAuthor": DEPLOY_COMMIT_AUTHOR,
            "commitMessage": DEPLOY_COMMIT_MESSAGE,
            "source": DEPLOY_SOURCE,
        },
    })


@app.route("/healthz")
def healthz():
    return "ok"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
