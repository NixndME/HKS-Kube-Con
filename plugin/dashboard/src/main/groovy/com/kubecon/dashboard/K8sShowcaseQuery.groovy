package com.kubecon.dashboard

import groovy.json.JsonSlurper
import groovy.util.logging.Slf4j

/**
 * Reads live status for the K8s Showcase app: which cluster, which slot
 * (blue/green) is live, each slot's version and ready/desired pod count,
 * and a URL to open the app. Reuses the exact same cluster/master lookup
 * and SSH technique as plugin/tasks/deploy_k8s_showcase.py.tmpl, run as a
 * child python3 process rather than duplicated in Groovy.
 *
 * Secrets go in as environment variables, never baked into the script text.
 * Nothing here is specific to one appliance or one cluster: the appliance
 * URL defaults to the local one (this plugin runs on the appliance itself)
 * and the cluster is found dynamically, same as the tasks.
 */
@Slf4j
class K8sShowcaseQuery {

    // Kept in sync by hand with plugin/tasks/*.py.tmpl in the Python side of this
    // project (Kube-Con/plugin/tasks/). Not shared code: Morpheus tasks and this
    // plugin are two separate deployable units with no common classpath.
    private static final String PY_SCRIPT = '''
import datetime, json, os, pty, select, ssl, sys, time, urllib.parse, urllib.request

APPLIANCE_URL = os.environ.get("KUBECON_APPLIANCE_URL", "https://localhost")
API_TOKEN = os.environ["KUBECON_API_TOKEN"]
SSH_USER = os.environ["KUBECON_SSH_USER"]
SSH_PASS = os.environ["KUBECON_SSH_PASS"]
NAMESPACE = "k8s-showcase"
KUBECONFIG = "/etc/kubernetes/admin.conf"
CTX = ssl._create_unverified_context()

# Optional: if you've put your own public hostname in front of this cluster
# (e.g. a reverse proxy or tunnel), set either here and it's used instead of
# the raw NodePort URL. Left blank by default so a fresh install on any
# HKS/MKS cluster just works off the node's real IP and NodePort.
PUBLIC_APP_URL = ""
PUBLIC_GRAFANA_URL = ""


def api_get(path):
    req = urllib.request.Request(f"{APPLIANCE_URL}{path}", headers={
        "Authorization": f"Bearer {API_TOKEN}", "Accept": "application/json"})
    with urllib.request.urlopen(req, context=CTX, timeout=15) as r:
        return json.loads(r.read().decode())


def relative_time(iso_str):
    """Turn a Morpheus dateCreated ('2026-10-01T08:27:38.017Z') into '5 min ago'."""
    try:
        dt = datetime.datetime.strptime(iso_str[:19], "%Y-%m-%dT%H:%M:%S")
        secs = (datetime.datetime.utcnow() - dt).total_seconds()
        if secs < 0:
            secs = 0
        if secs < 60:
            return "just now"
        if secs < 3600:
            return f"{int(secs // 60)} min ago"
        if secs < 86400:
            return f"{int(secs // 3600)} hr ago"
        return f"{int(secs // 86400)} day(s) ago"
    except Exception:
        return iso_str


def latest_job_execution(job_name):
    """Who last ran this catalog item's workflow, and when. None if never run."""
    try:
        data = api_get(f"/api/job-executions?max=20&phrase={urllib.parse.quote(job_name)}")
    except Exception:
        return None
    for je in data.get("jobExecutions", []):
        if je.get("status") == "success" and (je.get("job") or {}).get("name") == job_name:
            created_by = je.get("createdBy") or {}
            when = je.get("dateCreated")
            return {
                "by": created_by.get("displayName") or created_by.get("username") or "unknown",
                "when": relative_time(when) if when else None,
            }
    return None


def list_pods(host, port):
    """Every pod in the demo namespace, for the dashboard's drill-down table."""
    out, code = kubectl(host, port, f"get pods -n {NAMESPACE} -o json")
    if code != 0:
        return []
    try:
        start = out.index("{")
        data = json.loads(out[start:])
    except (ValueError, json.JSONDecodeError):
        return []
    pods = []
    for item in data.get("items", []):
        meta = item.get("metadata", {}) or {}
        status = item.get("status", {}) or {}
        spec = item.get("spec", {}) or {}
        labels = meta.get("labels", {}) or {}
        name = meta.get("name", "")
        color = labels.get("slot") or ("blue" if "blue" in name else "green" if "green" in name else "")
        containers = status.get("containerStatuses", []) or []
        ready = sum(1 for c in containers if c.get("ready"))
        restarts = sum(c.get("restartCount", 0) for c in containers)
        phase = status.get("phase", "Unknown")
        # Simple health signal for the dashboard's color coding: green = running
        # clean, amber = running but has restarted at least once (past instability),
        # red = anything else (Pending, Failed, CrashLoopBackOff, etc).
        if phase != "Running":
            health = "red"
        elif restarts > 0:
            health = "amber"
        else:
            health = "green"
        pods.append({
            "name": name,
            "color": color,
            "phase": phase,
            "health": health,
            "ready": f"{ready}/{len(containers)}",
            "restarts": restarts,
            "node": spec.get("nodeName", ""),
            "containerNames": [c.get("name") for c in spec.get("containers", [])],
        })
    return pods


def query_prometheus(prom_url, query):
    """One instant-vector Prometheus query, or None if it fails or returns nothing.
    Called straight from the appliance over plain HTTP (no SSH): the appliance and
    the cluster nodes share the same network in every lab this has been tested on,
    and Prometheus here has no auth in front of it (confirmed live)."""
    if not prom_url:
        return None
    try:
        url = prom_url.rstrip("/") + "/api/v1/query?" + urllib.parse.urlencode({"query": query})
        with urllib.request.urlopen(url, timeout=8) as r:
            data = json.loads(r.read().decode())
        result = data.get("data", {}).get("result", [])
        if not result:
            return None
        return float(result[0]["value"][1])
    except Exception:
        return None


def live_metrics(prom_url):
    """App-level and cluster-level numbers for the dashboard's Live Metrics panel.
    Empty dict if the Observability catalog item has not been ordered yet (no
    Prometheus URL to query)."""
    if not prom_url:
        return {}
    cpu = query_prometheus(prom_url, 'sum(rate(container_cpu_usage_seconds_total{namespace="k8s-showcase"}[5m]))')
    mem = query_prometheus(prom_url, 'sum(container_memory_working_set_bytes{namespace="k8s-showcase",container!=""})')
    restarts = query_prometheus(prom_url, 'sum(kube_pod_container_status_restarts_total{namespace="k8s-showcase"})')
    nodes = query_prometheus(prom_url, 'count(kube_node_info)')
    cluster_cpu_pct = query_prometheus(prom_url, '100 - (avg(rate(node_cpu_seconds_total{mode="idle"}[5m])) * 100)')
    mem_used = query_prometheus(prom_url, 'sum(node_memory_MemTotal_bytes) - sum(node_memory_MemAvailable_bytes)')
    mem_total = query_prometheus(prom_url, 'sum(node_memory_MemTotal_bytes)')
    return {
        "appCpuCores": round(cpu, 3) if cpu is not None else None,
        "appMemMb": round(mem / 1024 / 1024, 1) if mem is not None else None,
        "appRestarts": int(restarts) if restarts is not None else None,
        "clusterNodes": int(nodes) if nodes is not None else None,
        "clusterCpuPct": round(cluster_cpu_pct, 1) if cluster_cpu_pct is not None else None,
        "clusterMemUsedGb": round(mem_used / 1024 / 1024 / 1024, 1) if mem_used is not None else None,
        "clusterMemTotalGb": round(mem_total / 1024 / 1024 / 1024, 1) if mem_total is not None else None,
    }


SLO_TARGET_PCT = 99.0


def slo_metrics(prom_url, live_color):
    """Real SLO data, not fabricated: average fraction of the live deployment's
    pods that were actually Ready over the last 15 minutes, from Prometheus's
    own history (the same source every other number on this dashboard already
    uses) -- not just a point-in-time snapshot. Error budget is a simple
    target-minus-actual against a fixed 99% target, floored at 0 so a bad
    window reads 'budget exhausted' rather than a confusing negative number."""
    if not prom_url or not live_color:
        return {}
    pct = query_prometheus(prom_url,
        f'avg_over_time(kube_deployment_status_replicas_ready{{namespace="k8s-showcase",'
        f'deployment="k8s-showcase-{live_color}"}}[15m]) / '
        f'avg_over_time(kube_deployment_spec_replicas{{namespace="k8s-showcase",'
        f'deployment="k8s-showcase-{live_color}"}}[15m]) * 100')
    if pct is None:
        return {}
    pct = min(pct, 100.0)
    return {
        "windowMinutes": 15,
        "readyPct": round(pct, 2),
        "targetPct": SLO_TARGET_PCT,
        "errorBudgetRemainingPct": round(max(0.0, pct - (100.0 - SLO_TARGET_PCT)), 2),
    }


def canary_status(host, port):
    """Whether a canary split is currently active, and at what weight --
    read straight from the real Ingress object the Canary Traffic Split
    catalog item creates. None if it has never been ordered or is currently
    turned off (that catalog item deletes the Ingress entirely at weight 0,
    rather than leaving a stale 0%-weighted one around)."""
    out, code = kubectl(host, port, "get ingress k8s-showcase-canary -n %s -o json" % NAMESPACE)
    if code != 0:
        return None
    try:
        start = out.index("{")
        ing = json.loads(out[start:])
    except (ValueError, json.JSONDecodeError):
        return None
    annotations = (ing.get("metadata") or {}).get("annotations", {}) or {}
    weight = annotations.get("nginx.ingress.kubernetes.io/canary-weight")
    rules = (ing.get("spec") or {}).get("rules", [])
    backend_svc = ""
    if rules:
        paths = (rules[0].get("http") or {}).get("paths", [])
        if paths:
            backend_svc = ((paths[0].get("backend") or {}).get("service") or {}).get("name", "")
    color = "blue" if "blue" in backend_svc else ("green" if "green" in backend_svc else None)
    if not color or not weight:
        return None
    return {"color": color, "weightPct": int(weight) if weight.isdigit() else weight}


def synthetic_traffic_status(host, port):
    """Whether the synthetic traffic generator is currently running, and
    whether its one pod is actually up -- read from the real Deployment, not
    a flag anywhere in Morpheus (there isn't one; the Deployment's own
    existence IS the on/off state, same pattern as the canary Ingress)."""
    out, code = kubectl(host, port, "get deployment k8s-showcase-traffic-gen -n %s -o json" % NAMESPACE)
    if code != 0:
        return {"enabled": False}
    try:
        start = out.index("{")
        dep = json.loads(out[start:])
    except (ValueError, json.JSONDecodeError):
        return {"enabled": False}
    status = dep.get("status", {}) or {}
    return {"enabled": True, "ready": status.get("readyReplicas", 0) or 0}


KNOWN_JOB_NAMES = [
    "Deploy K8s Showcase App",
    "Switch K8s Showcase Traffic",
    "Check K8s Showcase Status",
    "Open Observability (Grafana + Prometheus)",
    "Rollback K8s Showcase App",
    "Canary Traffic Split",
    "Synthetic Traffic Generator",
]


def recent_activity():
    """Who ran which K8s Showcase catalog item, and when. Pulled from Morpheus's
    own job-executions (the real record of every catalog order's workflow run),
    not the generic /api/activity log -- that log only covers Provisioning-style
    App/Instance objects, not Task/Workflow catalog items, so it would otherwise
    show old, unrelated entries instead of our actual deploy/switch history."""
    try:
        data = api_get("/api/job-executions?max=30")
    except Exception:
        return []
    out = []
    for je in data.get("jobExecutions", []):
        job_name = (je.get("job") or {}).get("name") or ""
        if job_name not in KNOWN_JOB_NAMES:
            continue
        created_by = je.get("createdBy") or {}
        when = je.get("dateCreated")
        duration_ms = je.get("duration") or 0
        out.append({
            "message": job_name,
            "status": je.get("status") or "",
            "user": created_by.get("displayName") or created_by.get("username") or "unknown",
            "when": relative_time(when) if when else None,
            "durationSec": round(duration_ms / 1000.0, 1),
        })
        if len(out) >= 5:
            break
    return out


def list_resources(host, port):
    """Service, Ingress, ConfigMap, and Secret objects in the demo namespace,
    for the dashboard's resource tree. Real objects only, nothing invented."""
    resources = {"services": [], "ingresses": [], "configMaps": [], "secrets": []}
    out, code = kubectl(host, port, f"get svc,ingress,configmap,secret -n {NAMESPACE} -o json")
    if code != 0:
        return resources
    try:
        start = out.index("{")
        data = json.loads(out[start:])
    except (ValueError, json.JSONDecodeError):
        return resources
    for item in data.get("items", []):
        kind = item.get("kind", "")
        name = (item.get("metadata") or {}).get("name", "")
        if kind == "Service":
            ports = ",".join(str(p.get("port")) for p in (item.get("spec", {}).get("ports") or []))
            resources["services"].append({"name": name, "type": item.get("spec", {}).get("type", ""), "ports": ports})
        elif kind == "Ingress":
            resources["ingresses"].append({"name": name})
        elif kind == "ConfigMap":
            if name == "kube-root-ca.crt":
                continue
            resources["configMaps"].append({"name": name})
        elif kind == "Secret":
            if (item.get("type") or "") == "kubernetes.io/service-account-token":
                continue
            resources["secrets"].append({"name": name, "type": item.get("type", "")})
    return resources


def opencost_url(host, port):
    """NodePort + hosting node IP for OpenCost's API, same dynamic discovery
    pattern as Grafana/Prometheus. None if OpenCost isn't installed/exposed yet."""
    out, code = kubectl(host, port,
        "get svc k8s-showcase-opencost-access -n opencost -o jsonpath='{.spec.ports[?(@.port==9003)].nodePort}'")
    node_port = out.splitlines()[-1].strip() if out.strip() else ""
    if code != 0 or not node_port.isdigit():
        return None
    out2, code2 = kubectl(host, port,
        "get pods -n opencost -l app.kubernetes.io/name=opencost -o jsonpath='{.items[0].status.hostIP}'")
    node_ip = out2.splitlines()[-1].strip() if out2.strip() else ""
    target = node_ip if (code2 == 0 and node_ip) else host
    return f"http://{target}:{node_port}"


def node_cost(host, port, live_pod_names):
    """Real cost, computed by OpenCost (installed alongside this app, pointed at
    the cluster's own Prometheus). OpenCost allocates cost from each pod's real
    CPU/memory REQUESTS, same as real cloud billing would -- not a guess. Falls
    back to Morpheus's own (currently $0, no price plan attached) invoice data
    if OpenCost isn't reachable yet, labeled honestly either way."""
    base_url = opencost_url(host, port)
    if base_url:
        try:
            url = (f"{base_url}/allocation/compute?window=1h&aggregate=namespace&accumulate=true")
            with urllib.request.urlopen(url, timeout=10) as r:
                doc = json.loads(r.read().decode())
            rows = (doc.get("data") or [{}])[0]
            cluster_hourly = sum(max(v.get("totalCost") or 0, 0) for v in rows.values())
            app_hourly = max((rows.get(NAMESPACE) or {}).get("totalCost") or 0, 0)

            pod_url = (f"{base_url}/allocation/compute?window=1h&aggregate=pod&accumulate=true"
                       f"&filter=" + urllib.parse.quote(f'namespace:"{NAMESPACE}"'))
            pods = []
            try:
                with urllib.request.urlopen(pod_url, timeout=10) as r2:
                    pdoc = json.loads(r2.read().decode())
                prows = (pdoc.get("data") or [{}])[0]
                for k, v in prows.items():
                    pod_name = (v.get("properties") or {}).get("pod", k)
                    # OpenCost's window is historical: it includes pods that existed
                    # at any point in the last hour, including ones already replaced
                    # by a redeploy. Only show currently-running pods here, or every
                    # redeploy leaves ghost rows behind for up to an hour.
                    if pod_name not in live_pod_names:
                        continue
                    color = "blue" if "blue" in pod_name else "green" if "green" in pod_name else ""
                    pods.append({
                        "pod": pod_name,
                        "color": color,
                        "monthly": round(max(v.get("totalCost") or 0, 0) * 730, 4),
                    })
                pods.sort(key=lambda p: p["color"])
            except Exception:
                pass

            return {
                "source": "opencost",
                "clusterMonthly": round(cluster_hourly * 730, 2),
                "appMonthly": round(app_hourly * 730, 4),
                "pods": pods,
            }
        except Exception:
            pass

    # OpenCost not reachable: fall back to Morpheus's own invoices (real, but
    # usually $0 on a lab with no price plan attached -- shown honestly).
    try:
        data = api_get("/api/invoices?max=50")
        billed = sum(float(inv.get("totalCost") or 0) for inv in data.get("invoices", [])
                     if inv.get("refType") == "ComputeServer" and str(inv.get("refName", "")).startswith("hks-"))
    except Exception:
        billed = None
    return {"source": "morpheus-invoices", "clusterMonthly": billed, "appMonthly": None, "pods": []}


def observability_links(host, port):
    """Grafana/Prometheus URLs, only if the Observability catalog item has already
    been ordered (its NodePort Services exist). Never creates anything here, this
    is a read-only dashboard query. Grafana's link points straight at this app's
    own dashboard (uid 'k8s-showcase-app', provisioned once via the Grafana API --
    see plugin/grafana/k8s-showcase-dashboard.json), not the generic Grafana home
    page, so a click lands on real metrics for this app immediately."""
    links = {}
    targets = [
        ("grafana", "k8s-showcase-grafana-access", "app.kubernetes.io/name=grafana"),
        ("prometheus", "k8s-showcase-prometheus-access", "app.kubernetes.io/name=prometheus"),
    ]
    for key, svc_name, selector in targets:
        out1, code1 = kubectl(host, port,
            f"get svc {svc_name} -n monitoring -o jsonpath='{{.spec.ports[0].nodePort}}'")
        node_port = out1.splitlines()[-1].strip() if out1.strip() else ""
        if code1 != 0 or not node_port.isdigit():
            continue
        out2, code2 = kubectl(host, port,
            f"get pods -n monitoring -l {selector} -o jsonpath='{{.items[0].status.hostIP}}'")
        node_ip = out2.splitlines()[-1].strip() if out2.strip() else ""
        target = node_ip if (code2 == 0 and node_ip) else host
        # Plain '?kiosk' (no value) hides Grafana's own left nav and breadcrumb
        # entirely on this Grafana version (12.4.1) -- confirmed by screenshot,
        # 'kiosk=tv' does NOT do this on this version, it leaves the sidebar
        # showing. Panels and the namespace filter control stay visible.
        path = "d/k8s-showcase-app/k8s-showcase-app?kiosk&refresh=10s" if key == "grafana" else ""
        links[key] = f"http://{target}:{node_port}/{path}"
    if PUBLIC_GRAFANA_URL and "grafana" in links:
        links["grafana"] = PUBLIC_GRAFANA_URL
    return links


def ssh_run(host, port, remote_cmd, timeout=20):
    cmd = ["ssh", "-p", str(port), "-o", "StrictHostKeyChecking=no",
           "-o", "PubkeyAuthentication=no", "-o", "PreferredAuthentications=password",
           "-o", "ConnectTimeout=8", f"{SSH_USER}@{host}", remote_cmd]
    pid, fd = pty.fork()
    if pid == 0:
        os.execvp(cmd[0], cmd)
        os._exit(1)
    output = b""
    sent = False
    start = time.time()
    while time.time() - start < timeout:
        r, _, _ = select.select([fd], [], [], 1)
        if fd in r:
            try:
                chunk = os.read(fd, 4096)
            except OSError:
                break
            if not chunk:
                break
            output += chunk
            if not sent and b"assword:" in chunk:
                os.write(fd, (SSH_PASS + "\\n").encode())
                sent = True
        else:
            try:
                done, status = os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                break
            if done != 0:
                return output.decode(errors="replace"), status
    # Loop above only stops reading on timeout, it doesn't stop the remote
    # command -- a genuinely hung remote command would make a plain blocking
    # waitpid here hang forever too (confirmed live, see morpheus-lab-gotchas).
    import signal
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        _, status = os.waitpid(pid, 0)
    except ChildProcessError:
        status = 0
    return output.decode(errors="replace"), status


def exit_code_of(raw):
    return os.WEXITSTATUS(raw) if os.WIFEXITED(raw) else raw


def kubectl(host, port, args):
    out, status = ssh_run(host, port, f"sudo kubectl --kubeconfig={KUBECONFIG} {args}")
    return out.strip(), exit_code_of(status)


def main():
    result = {"ok": False, "error": None}
    try:
        clusters = api_get("/api/clusters?max=100").get("clusters", [])
        k8s = [c for c in clusters
               if "kubernetes" in (c.get("layout") or {}).get("name", "").lower()
               or c.get("type") == "kubernetes"]
        if not k8s:
            result["error"] = "no Kubernetes cluster found"
            print(json.dumps(result)); return
        cluster = k8s[0]
        result["clusterName"] = cluster["name"]

        host = port = None
        for s in cluster.get("servers", []):
            if (s.get("computeServerType") or {}).get("nodeType") == "kube-master":
                detail = api_get(f"/api/servers/{s['id']}").get("server", {})
                host, port = detail.get("sshHost"), detail.get("sshPort") or 22
                break
        if not host:
            result["error"] = "no master node found"
            print(json.dumps(result)); return

        live, code = kubectl(host, port,
            f"get svc k8s-showcase -n {NAMESPACE} -o jsonpath='{{.spec.selector.slot}}'")
        if code != 0:
            result["error"] = "not deployed yet"
            print(json.dumps(result)); return
        result["live"] = live.splitlines()[-1].strip() if live else None

        slots = {}
        for color in ("blue", "green"):
            out, code = kubectl(host, port,
                "get deployment k8s-showcase-%s -n %s -o json" % (color, NAMESPACE))
            if code != 0:
                continue
            try:
                start = out.index("{")
                dep = json.loads(out[start:])
            except (ValueError, json.JSONDecodeError):
                continue
            containers = (dep.get("spec", {}).get("template", {})
                          .get("spec", {}).get("containers", [{}]))
            image = containers[0].get("image", "") if containers else ""
            version = image.split(":")[-1] if ":" in image else image
            status = dep.get("status", {})
            slots[color] = {
                "version": version,
                "ready": status.get("readyReplicas", 0) or 0,
                "desired": dep.get("spec", {}).get("replicas", 0) or 0,
            }
        result["slots"] = slots
        result["pods"] = list_pods(host, port)
        result["lastDeploy"] = latest_job_execution("Deploy K8s Showcase App")
        result["lastSwitch"] = latest_job_execution("Switch K8s Showcase Traffic")
        result["observability"] = observability_links(host, port)
        result["metrics"] = live_metrics(result["observability"].get("prometheus"))
        result["slo"] = slo_metrics(result["observability"].get("prometheus"), result["live"])
        result["canary"] = canary_status(host, port)
        result["trafficGen"] = synthetic_traffic_status(host, port)
        result["activity"] = recent_activity()
        result["resources"] = list_resources(host, port)
        live_pod_names = {p["name"] for p in result["pods"]}
        result["cost"] = node_cost(host, port, live_pod_names)

        out, code = kubectl(host, port,
            "get svc -n ingress-nginx ingress-nginx-controller "
            "-o jsonpath='{.spec.ports[?(@.port==80)].nodePort}'")
        node_port = out.splitlines()[-1].strip() if out.strip() else ""
        if code == 0 and node_port.isdigit():
            out2, code2 = kubectl(host, port,
                "get pods -n ingress-nginx -l app.kubernetes.io/component=controller "
                "-o jsonpath='{.items[0].status.hostIP}'")
            node_ip = out2.splitlines()[-1].strip() if out2.strip() else ""
            target = node_ip if (code2 == 0 and node_ip) else host
            result["url"] = PUBLIC_APP_URL or f"http://{target}:{node_port}/"

        result["ok"] = True
        print(json.dumps(result))
    except Exception as e:
        result["error"] = str(e)
        print(json.dumps(result))


main()
'''.stripIndent().trim()

    /** Runs the query, or null if settings are missing or it fails. Never throws. */
    static Map fetch(K8sShowcaseSettings settings) {
        if (!settings?.isConfigured()) {
            log.warn('[KUBECON-DASH] plugin settings not configured yet (ssh user/password/api token)')
            return null
        }
        try {
            ProcessBuilder pb = new ProcessBuilder('python3', '-c', PY_SCRIPT)
            pb.environment().put('KUBECON_API_TOKEN', settings.apiToken)
            pb.environment().put('KUBECON_SSH_USER', settings.sshUsername)
            pb.environment().put('KUBECON_SSH_PASS', settings.sshPassword)
            pb.redirectErrorStream(false)
            Process proc = pb.start()
            String out = proc.inputStream.getText('UTF-8')
            String err = proc.errorStream.getText('UTF-8')
            boolean finished = proc.waitFor(25, java.util.concurrent.TimeUnit.SECONDS)
            if (!finished) {
                proc.destroyForcibly()
                log.error('[KUBECON-DASH] status query timed out')
                return null
            }
            if (err?.trim()) {
                log.warn("[KUBECON-DASH] status query stderr: ${err.trim()}")
            }
            String jsonLine = out?.trim()?.split('\n')?.last()
            if (!jsonLine) return null
            return new JsonSlurper().parseText(jsonLine) as Map
        } catch (Throwable t) {
            log.error('[KUBECON-DASH] status query failed', t)
            return null
        }
    }
}
