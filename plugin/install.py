#!/usr/bin/env python3
"""Install the K8s Showcase demo pack onto a Morpheus appliance.

Creates, in order: the Cypher secrets (if not already set by hand), the
Option Types the order form needs, the Task that does the real work, a
Workflow wrapping that task, and a Catalog Item a customer can order.

Nothing here is specific to one appliance. The cluster to deploy onto is
found by the Task itself at run time, not picked here.

Run:
    python3 plugin/install.py --url https://<appliance> --token <token> --insecure
"""
import argparse
import json
import os
import pty
import select
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))


class ApiError(Exception):
    pass


class Client:
    def __init__(self, base, token, insecure=False):
        self.base, self.token = base.rstrip("/"), token
        self.ctx = ssl._create_unverified_context() if insecure else None

    def call(self, method, path, body=None, **params):
        url = f"{self.base}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, context=self.ctx, timeout=60) as r:
                raw = r.read().decode()
                return json.loads(raw) if raw.strip() else {}
        except urllib.error.HTTPError as e:
            raise ApiError(f"HTTP {e.code} {method} {path}: {e.read().decode(errors='replace')[:600]}")
        except urllib.error.URLError as e:
            raise ApiError(f"cannot reach {url}: {e.reason}")


INPUTS = [
    dict(name="K8s Showcase imageTag", fieldName="imageTag", fieldLabel="Image Tag (version)",
         type="text", required=True),
    dict(name="K8s Showcase appColor", fieldName="appColor", fieldLabel="Color (blue or green)",
         type="text", required=True, verifyPattern=r"^(blue|green)$"),
    dict(name="K8s Showcase replicaCount", fieldName="replicaCount", fieldLabel="Replica Count",
         type="text", required=True, defaultValue="2", verifyPattern=r"^[0-9]+$"),
    dict(name="K8s Showcase clusterName", fieldName="clusterName",
         fieldLabel="Cluster Name (leave blank if you only have one Kubernetes cluster)",
         type="text", required=False),
    dict(name="K8s Showcase commitSha", fieldName="commitSha",
         fieldLabel="Git commit SHA (filled in by GitOps, leave blank for a manual order)",
         type="text", required=False),
    dict(name="K8s Showcase commitAuthor", fieldName="commitAuthor",
         fieldLabel="Git commit author (filled in by GitOps)",
         type="text", required=False),
    dict(name="K8s Showcase commitMessage", fieldName="commitMessage",
         fieldLabel="Git commit message (filled in by GitOps)",
         type="text", required=False),
    dict(name="K8s Showcase deploySource", fieldName="deploySource",
         fieldLabel="Deploy source (catalog or gitops, leave blank for 'catalog')",
         type="text", required=False),
    dict(name="K8s Showcase canaryColor", fieldName="canaryColor",
         fieldLabel="Canary color (blue or green)",
         type="text", required=True, verifyPattern=r"^(blue|green)$"),
    dict(name="K8s Showcase canaryWeight", fieldName="canaryWeight",
         fieldLabel="Canary weight percent (0-100, 0 turns it off)",
         type="text", required=True, defaultValue="10", verifyPattern=r"^[0-9]{1,3}$"),
    dict(name="K8s Showcase trafficGenEnabled", fieldName="enabled",
         fieldLabel="Enabled (true or false)",
         type="text", required=True, defaultValue="true", verifyPattern=r"^(true|false)$"),
    dict(name="K8s Showcase trafficGenInterval", fieldName="intervalSeconds",
         fieldLabel="Seconds between requests",
         type="text", required=False, defaultValue="5", verifyPattern=r"^[0-9]+$"),
]

LABELS = ["kubecon-demo"]

DEPLOY_NAME = "Deploy K8s Showcase App"
DEPLOY_INPUTS = ["imageTag", "appColor", "replicaCount", "clusterName",
                  "commitSha", "commitAuthor", "commitMessage", "deploySource"]

SWITCH_NAME = "Switch K8s Showcase Traffic"
SWITCH_INPUTS = ["clusterName"]

STATUS_NAME = "Check K8s Showcase Status"
STATUS_INPUTS = ["clusterName"]

ROLLBACK_NAME = "Rollback K8s Showcase App"
ROLLBACK_INPUTS = ["clusterName"]

CANARY_NAME = "Canary Traffic Split"
CANARY_INPUTS = ["canaryColor", "canaryWeight", "clusterName"]

SYNTHETIC_NAME = "Synthetic Traffic Generator"
SYNTHETIC_INPUTS = ["enabled", "intervalSeconds", "clusterName"]

OBSERVABILITY_NAME = "Open Observability (Grafana + Prometheus)"
OBSERVABILITY_INPUTS = ["clusterName"]


def load_task_content(filename):
    """Morpheus's own 'Python Script' task type runs on Jython (JVM), which has
    no real os.fork()/pty support. Running this as a Shell Script task that
    calls the appliance's real system python3 instead avoids that entirely."""
    path = os.path.join(HERE, "tasks", filename)
    with open(path, encoding="utf-8") as f:
        py_script = f.read()
    return "#!/bin/bash\npython3 << 'K8S_SHOWCASE_PY_EOF'\n" + py_script + "\nK8S_SHOWCASE_PY_EOF\n"


def ensure_cypher_secrets(c, ssh_user, ssh_password):
    """A query-param write (?value=X) silently stores nothing usable: cypher.read()
    later returns the literal string '{}'. A JSON body with a 'data' key is what
    actually works, confirmed live against this appliance."""
    if not ssh_user or not ssh_password:
        return
    c.call("POST", "/api/cypher/secret/hks-showcase-ssh-user", {"data": ssh_user})
    c.call("POST", "/api/cypher/secret/hks-showcase-ssh-password", {"data": ssh_password})
    print("  cypher secrets set: secret/hks-showcase-ssh-user, secret/hks-showcase-ssh-password")


def ensure_inputs(c):
    cur = {o["fieldName"]: o for o in c.call("GET", "/api/library/option-types", max=500).get("optionTypes", [])}
    ids = {}
    for i, spec in enumerate(INPUTS):
        body = {"name": spec["name"], "code": spec["fieldName"], "fieldName": spec["fieldName"],
                "fieldLabel": spec["fieldLabel"], "fieldContext": "config.customOptions",
                "type": spec["type"], "required": spec["required"], "labels": LABELS,
                "displayOrder": i * 10}
        if spec.get("verifyPattern"):
            body["verifyPattern"] = spec["verifyPattern"]
        if spec.get("defaultValue"):
            body["defaultValue"] = spec["defaultValue"]
        existing = cur.get(spec["fieldName"])
        if existing:
            c.call("PUT", f"/api/library/option-types/{existing['id']}", {"optionType": body})
            ids[spec["fieldName"]] = existing["id"]
            print(f"  input {spec['fieldName']!r}: updated")
        else:
            r = c.call("POST", "/api/library/option-types", {"optionType": body})
            new_id = (r.get("optionType") or {}).get("id")
            ids[spec["fieldName"]] = new_id
            print(f"  input {spec['fieldName']!r}: created")
    return ids


def ensure_task(c, name, content):
    cur = {t["name"]: t for t in c.call("GET", "/api/tasks", max=500).get("tasks", [])}
    body = {"name": name, "taskType": {"code": "script"}, "executeTarget": "local",
            "labels": LABELS, "file": {"sourceType": "local", "content": content}}
    existing = cur.get(name)
    if existing:
        c.call("PUT", f"/api/tasks/{existing['id']}", {"task": body})
        print(f"  task {name!r}: updated (id {existing['id']})")
        return existing["id"]
    r = c.call("POST", "/api/tasks", {"task": body})
    task_id = (r.get("task") or {}).get("id")
    print(f"  task {name!r}: created (id {task_id})")
    return task_id


def ensure_workflow(c, name, task_id, opt_ids):
    cur = {s["name"]: s for s in c.call("GET", "/api/task-sets", max=500).get("taskSets", [])}
    body = {"taskSet": {"name": name, "type": "operation", "labels": LABELS,
                        "tasks": [{"taskId": task_id, "taskPhase": "operation"}],
                        "optionTypes": opt_ids}}
    existing = cur.get(name)
    if existing:
        c.call("PUT", f"/api/task-sets/{existing['id']}", body)
        print(f"  workflow {name!r}: updated (id {existing['id']})")
        return existing["id"]
    r = c.call("POST", "/api/task-sets", body)
    wf_id = (r.get("taskSet") or {}).get("id")
    print(f"  workflow {name!r}: created (id {wf_id})")
    return wf_id


def ensure_catalog(c, name, description, workflow_id, opt_ids):
    cur = {i["name"]: i for i in c.call("GET", "/api/catalog-item-types", max=500).get("catalogItemTypes", [])}
    identity = {"name": name, "description": description,
                "category": "KubeCon Demo", "type": "workflow", "visibility": "public",
                "enabled": True, "labels": LABELS, "workflow": {"id": workflow_id}}
    existing = cur.get(name)
    if existing:
        cid = existing["id"]
        c.call("PUT", f"/api/catalog-item-types/{cid}", {"catalogItemType": identity})
    else:
        r = c.call("POST", "/api/catalog-item-types", {"catalogItemType": identity})
        cid = (r.get("catalogItemType") or {}).get("id")
    c.call("PUT", f"/api/catalog-item-types/{cid}", {"catalogItemType": {"optionTypes": opt_ids}})
    print(f"  catalog item {name!r}: ready (id {cid})")
    return cid


WIKI_PAGE_NAME = "K8s Showcase — KubeCon Demo"
WIKI_CATEGORY = "KubeCon Demo"


def ensure_wiki(c):
    path = os.path.join(HERE, "wiki_k8s_showcase.md")
    with open(path, encoding="utf-8") as f:
        content = f.read()
    existing = {p["name"]: p for p in c.call("GET", "/api/wiki/pages", max=500).get("pages", [])}
    cur = existing.get(WIKI_PAGE_NAME)
    page = {"name": WIKI_PAGE_NAME, "category": WIKI_CATEGORY, "content": content}
    if cur:
        c.call("PUT", f"/api/wiki/pages/{cur['id']}", {"page": page})
        print(f"  wiki page {WIKI_PAGE_NAME!r}: updated")
    else:
        c.call("POST", "/api/wiki/pages", {"page": page})
        print(f"  wiki page {WIKI_PAGE_NAME!r}: created")


DASHBOARD_CODE = "kubecon-k8s-showcase"


def ensure_dashboard_display(c):
    """Make the dashboard widget actually show up on the Operations page. This
    appliance setting is purely additive -- never remove what's already there,
    just make sure our dashboard id is in the list. Needs the dashboard plugin
    jar uploaded separately first (Administration > Plugins > Add Plugin) --
    this installer only manages Tasks/Catalog/Wiki objects via the REST API,
    the Groovy UI plugin is a different kind of artifact with its own upload
    mechanism, not something this script can push itself."""
    settings = c.call("GET", "/api/appliance-settings").get("applianceSettings", {})
    current = (settings.get("dashboardsToDisplay") or "").strip()
    ids = [x for x in current.split(",") if x]
    if DASHBOARD_CODE in ids:
        print(f"  dashboardsToDisplay: already includes {DASHBOARD_CODE!r}")
        return
    ids.append(DASHBOARD_CODE)
    c.call("PUT", "/api/appliance-settings", {"applianceSettings": {"dashboardsToDisplay": ",".join(ids)}})
    print(f"  dashboardsToDisplay: added {DASHBOARD_CODE!r}")


# ---- GitOps CronJob: SSH to the cluster's master node, same mechanism uninstall.py
# uses for its own cluster-side cleanup. This is what makes the GitHub-poll-and-
# auto-redeploy loop part of "installing the plugin" rather than a manual extra step
# -- no catalog order needed, no appliance-specific value baked in (cluster, master
# node, and both task-set ids are all resolved fresh on every install.py run).

def ssh_run(ssh_user, ssh_pass, host, port, remote_cmd, stdin_data=None, timeout=30):
    cmd = ["ssh", "-p", str(port), "-o", "StrictHostKeyChecking=no",
           "-o", "UserKnownHostsFile=/dev/null",
           "-o", "PubkeyAuthentication=no", "-o", "PreferredAuthentications=password",
           "-o", "ConnectTimeout=10", f"{ssh_user}@{host}", remote_cmd]
    pid, fd = pty.fork()
    if pid == 0:
        os.execvp(cmd[0], cmd)
        os._exit(1)
    output = b""
    sent_password = False
    sent_stdin = stdin_data is None
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
            if not sent_password and b"assword:" in chunk:
                os.write(fd, (ssh_pass + "\n").encode())
                sent_password = True
            elif sent_password and not sent_stdin:
                os.write(fd, stdin_data.encode())
                os.write(fd, b"\x04")
                sent_stdin = True
        else:
            try:
                done, status = os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                break
            if done != 0:
                return output.decode(errors="replace"), status
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


def kubectl(ssh_user, ssh_pass, host, port, args, stdin_data=None):
    out, status = ssh_run(ssh_user, ssh_pass, host, port,
                           f"sudo kubectl --kubeconfig=/etc/kubernetes/admin.conf {args}", stdin_data)
    return out.strip(), exit_code_of(status)


def write_remote_file_chunked(ssh_user, ssh_pass, host, port, remote_path, content):
    """Same fix as uninstall.py's write_remote_file_chunked -- a pty's canonical-mode
    input queue is capped at MAX_CANON (4096 bytes on Linux), confirmed live to
    silently truncate any larger single stdin write. Never send more than one small
    chunk per SSH session."""
    CHUNK = 3000
    ssh_run(ssh_user, ssh_pass, host, port, f"rm -f {remote_path}")
    for i in range(0, len(content), CHUNK):
        chunk = content[i:i + CHUNK]
        ssh_run(ssh_user, ssh_pass, host, port, f"cat >> {remote_path}", stdin_data=chunk, timeout=30)
    out, status = ssh_run(ssh_user, ssh_pass, host, port, f"wc -c < {remote_path}")
    written = out.strip().splitlines()[-1].strip() if out.strip() else ""
    return written.isdigit() and int(written) == len(content)


def find_cluster_master(c, cluster_name=None):
    clusters = c.call("GET", "/api/clusters?max=100").get("clusters", [])
    k8s = [cl for cl in clusters
           if "kubernetes" in (cl.get("layout") or {}).get("name", "").lower()
           or cl.get("type") == "kubernetes"]
    if not k8s:
        return None, None, None
    cluster = k8s[0]
    if cluster_name:
        for cl in k8s:
            if cl["name"] == cluster_name:
                cluster = cl
                break
    for s in cluster.get("servers", []):
        if (s.get("computeServerType") or {}).get("nodeType") == "kube-master":
            detail = c.call("GET", f"/api/servers/{s['id']}").get("server", {})
            return cluster["name"], detail.get("sshHost"), detail.get("sshPort") or 22
    return cluster["name"], None, None


GITOPS_POLLER_PY = r'''import json, os, re, ssl, sys, time, urllib.error, urllib.request

APPLIANCE_URL = os.environ["APPLIANCE_URL"].rstrip("/")
API_TOKEN = os.environ["API_TOKEN"]
DEPLOY_TASKSET_ID = os.environ["DEPLOY_TASKSET_ID"]
SWITCH_TASKSET_ID = os.environ["SWITCH_TASKSET_ID"]
ROLLBACK_TASKSET_ID = os.environ["ROLLBACK_TASKSET_ID"]
GITHUB_REPO = os.environ["GITHUB_REPO"]
VALUES_PATH = os.environ["VALUES_PATH"]
CLUSTER_NAME = os.environ.get("CLUSTER_NAME", "")
REPLICA_COUNT = os.environ.get("REPLICA_COUNT", "2")
NAMESPACE = os.environ.get("POD_NAMESPACE", "k8s-showcase")
STATE_NAME = "k8s-showcase-gitops-state"

CTX = ssl._create_unverified_context()
SA_DIR = "/var/run/secrets/kubernetes.io/serviceaccount"
K8S_API = "https://kubernetes.default.svc"


def k8s_call(method, path, body=None):
    with open(f"{SA_DIR}/token") as f:
        token = f.read().strip()
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/merge-patch+json" if method == "PATCH" else "application/json"
    req = urllib.request.Request(f"{K8S_API}{path}", data=data, method=method, headers=headers)
    ctx = ssl.create_default_context(cafile=f"{SA_DIR}/ca.crt")
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=10) as r:
            raw = r.read().decode()
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def morpheus_call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Authorization": f"Bearer {API_TOKEN}", "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(f"{APPLIANCE_URL}{path}", data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, context=CTX, timeout=20) as r:
        raw = r.read().decode()
        return json.loads(raw) if raw.strip() else {}


def latest_commit(etag):
    """GitHub's unauthenticated API allows 60 requests/hour -- polling every
    minute uses the entire quota with zero headroom, so any other traffic
    from the same outbound IP (confirmed live: this is exactly what happened
    on the real lab) pushes it over and the poller starts failing every tick
    until the hourly window resets. A conditional request (If-None-Match)
    fixes this for good: GitHub does NOT count a 304 response against the
    rate limit, so once nothing has changed, polling becomes free. Returns
    (commit_info_or_None, new_etag). commit_info is None both when nothing
    changed (304) and when the repo genuinely has no commit history yet for
    this path -- the caller doesn't need to tell those apart."""
    req = urllib.request.Request(
        f"https://api.github.com/repos/{GITHUB_REPO}/commits?path={VALUES_PATH}&per_page=1",
        headers={"Accept": "application/vnd.github+json", "User-Agent": "k8s-showcase-gitops"})
    if etag:
        req.add_header("If-None-Match", etag)
    try:
        with urllib.request.urlopen(req, context=CTX, timeout=15) as r:
            new_etag = r.headers.get("ETag") or etag
            commits = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 304:
            return None, etag
        raise
    if not commits:
        return None, new_etag
    c = commits[0]
    commit = c.get("commit", {}) or {}
    return {
        "sha": c["sha"],
        "author": (commit.get("author", {}) or {}).get("name", "unknown"),
        "message": (commit.get("message", "") or "").splitlines()[0][:200] if commit.get("message") else "",
    }, new_etag


def raw_values(sha):
    url = f"https://raw.githubusercontent.com/{GITHUB_REPO}/{sha}/{VALUES_PATH}"
    req = urllib.request.Request(url, headers={"User-Agent": "k8s-showcase-gitops"})
    with urllib.request.urlopen(req, context=CTX, timeout=15) as r:
        return r.read().decode()


def parse_values(text):
    tag_m = re.search(r'^\s*tag:\s*"?([^"\n]+?)"?\s*$', text, re.MULTILINE)
    color_m = re.search(r'^\s*color:\s*([a-zA-Z]+)\s*$', text, re.MULTILINE)
    if not tag_m or not color_m:
        return None
    replica_m = re.search(r'^\s*replicaCount:\s*"?([0-9]+)"?\s*$', text, re.MULTILINE)
    return tag_m.group(1).strip(), color_m.group(1).strip(), \
        replica_m.group(1).strip() if replica_m else REPLICA_COUNT


def get_state():
    cm = k8s_call("GET", f"/api/v1/namespaces/{NAMESPACE}/configmaps/{STATE_NAME}")
    if cm is None:
        return None, None
    data = cm.get("data") or {}
    return data.get("lastSha"), data.get("etag")


def set_state(sha, etag):
    data = {}
    if sha is not None:
        data["lastSha"] = sha
    if etag is not None:
        data["etag"] = etag
    body = {"metadata": {"name": STATE_NAME}, "data": data}
    existing = k8s_call("GET", f"/api/v1/namespaces/{NAMESPACE}/configmaps/{STATE_NAME}")
    if existing is None:
        k8s_call("POST", f"/api/v1/namespaces/{NAMESPACE}/configmaps", body)
    else:
        k8s_call("PATCH", f"/api/v1/namespaces/{NAMESPACE}/configmaps/{STATE_NAME}", body)


def live_slot():
    svc = k8s_call("GET", f"/api/v1/namespaces/{NAMESPACE}/services/k8s-showcase")
    if svc is None:
        return None
    return (svc.get("spec", {}).get("selector") or {}).get("slot")


def deployment_health(color):
    """(readyReplicas, desiredReplicas) for k8s-showcase-{color}, straight from
    the Deployment -- the same signal "Check K8s Showcase Status" already
    shows a human, just read here so GitOps can decide for itself whether a
    switch it just made is actually healthy."""
    dep = k8s_call("GET", f"/apis/apps/v1/namespaces/{NAMESPACE}/deployments/k8s-showcase-{color}")
    if dep is None:
        return 0, 0
    spec = dep.get("spec", {}) or {}
    status = dep.get("status", {}) or {}
    return status.get("readyReplicas", 0) or 0, spec.get("replicas", 0) or 0


def execute_taskset(taskset_id, custom_options):
    r = morpheus_call("POST", f"/api/task-sets/{taskset_id}/execute",
                       {"config": {"customOptions": custom_options}})
    job = r.get("job") or {}
    return job.get("id")


def wait_for_job(job_id, timeout=90):
    if not job_id:
        return False
    deadline = time.time() + timeout
    while time.time() < deadline:
        execs = morpheus_call("GET", f"/api/job-executions?jobId={job_id}&max=1").get("jobExecutions", [])
        if execs:
            process = execs[0].get("process") or {}
            status = execs[0].get("status") or process.get("status")
            if status in ("complete", "success"):
                return True
            if status in ("failed", "error", "cancelled"):
                return False
        time.sleep(5)
    return False


def main():
    last_sha, etag = get_state()
    try:
        commit, new_etag = latest_commit(etag)
    except urllib.error.HTTPError as e:
        # A transient GitHub error (rate limit, an outage) should not crash
        # this job -- exit 0, not 1. concurrencyPolicy: Forbid means a Job
        # stuck retrying after a nonzero exit blocks every later scheduled
        # tick until it finally gives up, so a single bad poll would otherwise
        # stall GitOps far longer than the problem itself lasts. Just try
        # again next minute, same as "no change" does.
        print(f"GitHub request failed ({e.code} {e.reason}) -- will retry next tick")
        return
    if commit is None:
        print("no new commit for the watched path")
        if new_etag != etag:
            set_state(None, new_etag)
        return
    if last_sha == commit["sha"]:
        print(f"no change (sha {commit['sha'][:10]})")
        if new_etag != etag:
            set_state(None, new_etag)
        return
    print(f"new commit detected: {commit['sha'][:10]} by {commit['author']}: {commit['message']}")
    try:
        parsed = parse_values(raw_values(commit["sha"]))
    except urllib.error.HTTPError as e:
        print(f"could not fetch the committed values.yaml ({e.code} {e.reason}) -- will retry next tick")
        return
    if not parsed:
        print("could not parse image tag / color out of the committed values.yaml -- skipping")
        set_state(commit["sha"], new_etag)
        return
    image_tag, color, replica_count = parsed
    if color not in ("blue", "green"):
        print(f"committed color {color!r} is not 'blue' or 'green' -- skipping")
        set_state(commit["sha"], new_etag)
        return
    print(f"deploying imageTag={image_tag!r} color={color!r} replicaCount={replica_count!r}")
    custom = {
        "imageTag": image_tag, "appColor": color, "replicaCount": replica_count,
        "clusterName": CLUSTER_NAME, "commitSha": commit["sha"],
        "commitAuthor": commit["author"], "commitMessage": commit["message"],
        "deploySource": "gitops",
    }
    if not wait_for_job(execute_taskset(DEPLOY_TASKSET_ID, custom)):
        print("FATAL: deploy did not confirm complete -- leaving last-seen commit "
              "unchanged so this is retried next run")
        sys.exit(1)
    print("deploy complete")
    current_live = live_slot()
    if current_live and current_live != color:
        print(f"committed color {color!r} differs from live {current_live!r} -- switching traffic")
        if wait_for_job(execute_taskset(SWITCH_TASKSET_ID, {"clusterName": CLUSTER_NAME})):
            print("switch complete -- traffic now on the committed color")
            # Give the new pods a real chance to come up before judging them --
            # confirmed live on this cluster that a scale-up can take well
            # over a minute (scheduling, image-pull check, not just the
            # readinessProbe's own timing), so a short window here would
            # trigger a false-positive auto-rollback on a deploy that was
            # never actually broken, just still starting.
            ready, desired = 0, 0
            health_check_failed = False
            deadline = time.time() + 150
            while time.time() < deadline:
                try:
                    ready, desired = deployment_health(color)
                except urllib.error.HTTPError as e:
                    print(f"could not read {color} deployment health ({e.code} {e.reason}) -- "
                          f"skipping the auto-rollback check this time, leaving traffic as switched")
                    health_check_failed = True
                    break
                if desired > 0 and ready == desired:
                    break
                time.sleep(5)
            if health_check_failed:
                pass
            elif desired > 0 and ready == desired:
                print(f"health check passed: {color} is {ready}/{desired} ready")
            else:
                print(f"AUTO-ROLLBACK: {color} only reached {ready}/{desired} ready "
                      f"within 45s of switching -- reverting to {current_live!r}")
                if wait_for_job(execute_taskset(ROLLBACK_TASKSET_ID, {"clusterName": CLUSTER_NAME})):
                    print(f"AUTO-ROLLBACK complete -- traffic is back on {current_live!r}")
                else:
                    print("AUTO-ROLLBACK FAILED to confirm complete -- check the cluster manually")
        else:
            print("WARNING: switch did not confirm complete -- check manually")
    else:
        print(f"committed color {color!r} already live -- no switch needed")
    set_state(commit["sha"], new_etag)
    print("done")


if __name__ == "__main__":
    main()
'''


def build_gitops_manifest(deploy_taskset_id, switch_taskset_id, rollback_taskset_id, appliance_url, api_token,
                           github_repo, values_path, cluster_name, replica_count):
    indented_script = "\n".join("    " + line for line in GITOPS_POLLER_PY.splitlines())
    cluster_name_yaml = cluster_name or ""
    return f"""
apiVersion: v1
kind: ServiceAccount
metadata:
  name: k8s-showcase-gitops
  namespace: k8s-showcase
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: k8s-showcase-gitops
  namespace: k8s-showcase
rules:
  - apiGroups: [""]
    resources: ["configmaps"]
    # "update" (PUT) and "patch" (PATCH) are separate RBAC verbs -- confirmed
    # live that granting only "update" makes the poller's own merge-patch
    # state save fail with a 403 from the Kubernetes API itself (not GitHub),
    # even though the actual deploy/switch had already succeeded. Needs both.
    verbs: ["get", "create", "update", "patch"]
  - apiGroups: [""]
    resources: ["services"]
    verbs: ["get"]
  - apiGroups: ["apps"]
    resources: ["deployments"]
    # So the auto-rollback health check can read readyReplicas/replicas
    # straight from the Deployment it just switched to, right after a
    # GitOps-triggered switch -- never writes to deployments itself, scaling
    # happens through the Rollback task-set, same as any other order.
    verbs: ["get"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: k8s-showcase-gitops
  namespace: k8s-showcase
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: k8s-showcase-gitops
subjects:
  - kind: ServiceAccount
    name: k8s-showcase-gitops
    namespace: k8s-showcase
---
apiVersion: v1
kind: Secret
metadata:
  name: k8s-showcase-gitops-secret
  namespace: k8s-showcase
type: Opaque
stringData:
  applianceUrl: "{appliance_url}"
  apiToken: "{api_token}"
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: k8s-showcase-gitops-script
  namespace: k8s-showcase
data:
  poll.py: |
{indented_script}
---
apiVersion: batch/v1
kind: CronJob
metadata:
  name: k8s-showcase-gitops
  namespace: k8s-showcase
spec:
  schedule: "*/1 * * * *"
  concurrencyPolicy: Forbid
  successfulJobsHistoryLimit: 3
  failedJobsHistoryLimit: 3
  jobTemplate:
    spec:
      # Worst case on a real new commit: deploy's wait_for_job (up to 90s) +
      # switch's wait_for_job (up to 90s) + the auto-rollback health check
      # (up to 150s) + a rollback's own wait_for_job if it's needed (up to
      # 90s) = 420s. 480 gives headroom without risking Kubernetes killing
      # the pod mid-rollback, which would leave state unsaved and the same
      # commit re-processed next tick. Every "no new commit" tick (by far the
      # common case) still finishes in a few seconds either way.
      activeDeadlineSeconds: 480
      template:
        spec:
          serviceAccountName: k8s-showcase-gitops
          restartPolicy: Never
          containers:
            - name: poller
              image: python:3.12-slim
              command: ["python3", "/scripts/poll.py"]
              env:
                - name: APPLIANCE_URL
                  valueFrom: {{secretKeyRef: {{name: k8s-showcase-gitops-secret, key: applianceUrl}}}}
                - name: API_TOKEN
                  valueFrom: {{secretKeyRef: {{name: k8s-showcase-gitops-secret, key: apiToken}}}}
                - name: DEPLOY_TASKSET_ID
                  value: "{deploy_taskset_id}"
                - name: SWITCH_TASKSET_ID
                  value: "{switch_taskset_id}"
                - name: ROLLBACK_TASKSET_ID
                  value: "{rollback_taskset_id}"
                - name: GITHUB_REPO
                  value: "{github_repo}"
                - name: VALUES_PATH
                  value: "{values_path}"
                - name: CLUSTER_NAME
                  value: "{cluster_name_yaml}"
                - name: REPLICA_COUNT
                  value: "{replica_count}"
                - name: POD_NAMESPACE
                  valueFrom: {{fieldRef: {{fieldPath: metadata.namespace}}}}
              resources:
                requests: {{cpu: 10m, memory: 32Mi}}
                limits: {{cpu: 100m, memory: 96Mi}}
              volumeMounts:
                - name: script
                  mountPath: /scripts
          volumes:
            - name: script
              configMap:
                name: k8s-showcase-gitops-script
"""


def ensure_gitops_cronjob(c, ssh_user, ssh_password, deploy_taskset_id, switch_taskset_id,
                           rollback_taskset_id, appliance_url, api_token, github_repo, values_path,
                           cluster_name, replica_count):
    """Create the in-cluster CronJob that polls GitHub and auto-redeploys. Lives
    entirely inside the k8s-showcase namespace, so uninstall.py's single
    'delete namespace k8s-showcase' already removes all of it -- no separate
    teardown code needed there.

    Needs the app's own namespace to already exist (created by the Deploy task
    the first time it's ordered) -- if it isn't there yet, this just skips with
    a clear note instead of failing install.py outright, since ordering Deploy
    is a separate, deliberate demo step."""
    if not ssh_user or not ssh_password:
        print("  gitops cronjob: skipped (no --ssh-user/--ssh-password given)")
        return
    name, host, port = find_cluster_master(c, cluster_name or None)
    if not host:
        print("  gitops cronjob: skipped (no Kubernetes cluster/master reachable yet)")
        return
    out, code = kubectl(ssh_user, ssh_password, host, port, "get namespace k8s-showcase")
    if code != 0:
        print("  gitops cronjob: skipped (namespace k8s-showcase does not exist yet -- "
              "order 'Deploy K8s Showcase App' once first, then re-run this installer)")
        return
    manifest = build_gitops_manifest(deploy_taskset_id, switch_taskset_id, rollback_taskset_id,
                                      appliance_url, api_token, github_repo, values_path,
                                      cluster_name, replica_count)
    ok = write_remote_file_chunked(ssh_user, ssh_password, host, port,
                                    "/tmp/k8s-showcase-gitops.yaml", manifest)
    if not ok:
        print("  gitops cronjob: could not write the manifest to the remote host")
        return
    out, code = kubectl(ssh_user, ssh_password, host, port, "apply -f /tmp/k8s-showcase-gitops.yaml")
    if code != 0:
        print(f"  gitops cronjob: apply failed -- {out[-300:]}")
        return
    print(f"  gitops cronjob: ready on cluster {name!r} (polls GitHub every minute, "
          f"auto-deploys and auto-switches on a new commit to {values_path})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--token", required=True)
    ap.add_argument("--insecure", action="store_true")
    ap.add_argument("--ssh-user", help="set the Cypher secret for SSH login to cluster nodes, "
                                        "and (if given) also set up the GitOps CronJob")
    ap.add_argument("--ssh-password", help="set the Cypher secret for SSH login to cluster nodes")
    ap.add_argument("--github-repo", default="NixndME/HKS-Kube-Con",
                     help="owner/repo the GitOps CronJob polls for changes")
    ap.add_argument("--values-path", default="helm/k8s-showcase/values.yaml",
                     help="path inside that repo the GitOps CronJob watches")
    ap.add_argument("--gitops-replica-count", default="2",
                     help="replica count the GitOps CronJob requests on an auto-deploy")
    ap.add_argument("--cluster-name", help="Only needed if more than one Kubernetes cluster exists")
    args = ap.parse_args()

    c = Client(args.url, args.token, args.insecure)
    print("K8s Showcase install")
    ensure_cypher_secrets(c, args.ssh_user, args.ssh_password)
    input_ids = ensure_inputs(c)

    deploy_task_id = ensure_task(c, DEPLOY_NAME, load_task_content("deploy_k8s_showcase.py.tmpl"))
    deploy_opt_ids = [input_ids[n] for n in DEPLOY_INPUTS]
    deploy_wf_id = ensure_workflow(c, DEPLOY_NAME, deploy_task_id, deploy_opt_ids)
    ensure_catalog(c, DEPLOY_NAME, "Deploy the K8s Showcase sample app onto a Kubernetes cluster.",
                   deploy_wf_id, deploy_opt_ids)

    switch_task_id = ensure_task(c, SWITCH_NAME, load_task_content("switch_k8s_showcase_traffic.py.tmpl"))
    switch_opt_ids = [input_ids[n] for n in SWITCH_INPUTS]
    switch_wf_id = ensure_workflow(c, SWITCH_NAME, switch_task_id, switch_opt_ids)
    ensure_catalog(c, SWITCH_NAME,
                   "Flip live traffic for the K8s Showcase app between its blue and green deployments.",
                   switch_wf_id, switch_opt_ids)

    rollback_task_id = ensure_task(c, ROLLBACK_NAME, load_task_content("rollback_k8s_showcase.py.tmpl"))
    rollback_opt_ids = [input_ids[n] for n in ROLLBACK_INPUTS]
    rollback_wf_id = ensure_workflow(c, ROLLBACK_NAME, rollback_task_id, rollback_opt_ids)
    ensure_catalog(c, ROLLBACK_NAME,
                   "Roll back to whichever color isn't live, scaling it back up automatically if needed.",
                   rollback_wf_id, rollback_opt_ids)

    ensure_gitops_cronjob(c, args.ssh_user, args.ssh_password, deploy_wf_id, switch_wf_id, rollback_wf_id,
                          args.url, args.token, args.github_repo, args.values_path,
                          args.cluster_name, args.gitops_replica_count)

    status_task_id = ensure_task(c, STATUS_NAME, load_task_content("status_k8s_showcase.py.tmpl"))
    status_opt_ids = [input_ids[n] for n in STATUS_INPUTS]
    status_wf_id = ensure_workflow(c, STATUS_NAME, status_task_id, status_opt_ids)
    ensure_catalog(c, STATUS_NAME,
                   "Print a status report for the K8s Showcase app: live color, versions, demo URL.",
                   status_wf_id, status_opt_ids)

    obs_task_id = ensure_task(c, OBSERVABILITY_NAME, load_task_content("open_observability.py.tmpl"))
    obs_opt_ids = [input_ids[n] for n in OBSERVABILITY_INPUTS]
    obs_wf_id = ensure_workflow(c, OBSERVABILITY_NAME, obs_task_id, obs_opt_ids)
    ensure_catalog(c, OBSERVABILITY_NAME,
                   "Expose the cluster's own Grafana and Prometheus with a working link to each.",
                   obs_wf_id, obs_opt_ids)

    canary_task_id = ensure_task(c, CANARY_NAME, load_task_content("canary_split.py.tmpl"))
    canary_opt_ids = [input_ids[n] for n in CANARY_INPUTS]
    canary_wf_id = ensure_workflow(c, CANARY_NAME, canary_task_id, canary_opt_ids)
    ensure_catalog(c, CANARY_NAME,
                   "Send a chosen percentage of traffic to a second color at the same time as the live one.",
                   canary_wf_id, canary_opt_ids)

    synthetic_task_id = ensure_task(c, SYNTHETIC_NAME, load_task_content("synthetic_traffic.py.tmpl"))
    synthetic_opt_ids = [input_ids[n] for n in SYNTHETIC_INPUTS]
    synthetic_wf_id = ensure_workflow(c, SYNTHETIC_NAME, synthetic_task_id, synthetic_opt_ids)
    ensure_catalog(c, SYNTHETIC_NAME,
                   "Turn a steady trickle of real requests at the app on or off, for live demo dashboards.",
                   synthetic_wf_id, synthetic_opt_ids)

    ensure_wiki(c)
    ensure_dashboard_display(c)

    print("Done. Look under Provisioning > Catalog for 'Deploy K8s Showcase App', "
          "'Switch K8s Showcase Traffic', 'Check K8s Showcase Status', and "
          "'Open Observability (Grafana + Prometheus)'. "
          "The wiki page is under Wiki > KubeCon Demo. "
          "If the GitOps cronjob shows as skipped above because the k8s-showcase namespace "
          "did not exist yet, order 'Deploy K8s Showcase App' once, then re-run this installer "
          "to turn it on. "
          "If the K8s Showcase dashboard tile is missing from Operations, "
          "upload plugin/dashboard's built jar once via Administration > Plugins "
          "(a one-time step, separate from this script) then re-run this installer.")


if __name__ == "__main__":
    try:
        main()
    except ApiError as e:
        sys.exit(f"api error: {e}")
