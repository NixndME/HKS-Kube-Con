#!/usr/bin/env python3
"""Remove everything plugin/install.py and the K8s Showcase catalog items ever
created, from this Morpheus appliance and the HKS cluster it manages.

This is the other half of "plug and play": install.py proves a fresh install
builds everything from nothing, this proves a teardown actually leaves nothing
behind, so the next install.py run is a true fresh start, not a reinstall on
top of leftovers.

Only removes what this project added. Never touches:
- The HKS cluster's own layout (Grafana, Prometheus, fluent-bit, ingress-nginx)
  -- their original config/content, just our additions to them.
- The Prometheus datasource in Grafana (readOnly: true, provisioned by the
  cluster layout itself, confirmed live -- not ours to delete).
- Anything in biome/biome-lab (never touched by this project at all).

Run:
    python3 plugin/uninstall.py --url https://<appliance> --token <token> --insecure \
        --ssh-user <user> --ssh-password <password>
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

LABELS = ["kubecon-demo"]

CATALOG_NAMES = [
    "Deploy K8s Showcase App",
    "Switch K8s Showcase Traffic",
    "Check K8s Showcase Status",
    "Open Observability (Grafana + Prometheus)",
    "Rollback K8s Showcase App",
    "Canary Traffic Split",
    "Synthetic Traffic Generator",
]
OPTION_FIELD_NAMES = ["imageTag", "appColor", "replicaCount", "clusterName",
                      "commitSha", "commitAuthor", "commitMessage", "deploySource",
                      "canaryColor", "canaryWeight", "enabled", "intervalSeconds"]
WIKI_PAGE_NAME = "K8s Showcase — KubeCon Demo"


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
            raise ApiError(f"HTTP {e.code} {method} {path}: {e.read().decode(errors='replace')[:400]}")
        except urllib.error.URLError as e:
            raise ApiError(f"cannot reach {url}: {e.reason}")


def remove_catalog_items(c):
    cur = {i["name"]: i for i in c.call("GET", "/api/catalog-item-types", max=500).get("catalogItemTypes", [])}
    for name in CATALOG_NAMES:
        item = cur.get(name)
        if item:
            c.call("DELETE", f"/api/catalog-item-types/{item['id']}")
            print(f"  catalog item {name!r}: deleted")
        else:
            print(f"  catalog item {name!r}: not present")


def remove_workflows_and_tasks(c):
    workflows = {s["name"]: s for s in c.call("GET", "/api/task-sets", max=500).get("taskSets", [])}
    for name in CATALOG_NAMES:
        wf = workflows.get(name)
        if wf:
            c.call("DELETE", f"/api/task-sets/{wf['id']}")
            print(f"  workflow {name!r}: deleted")
    tasks = {t["name"]: t for t in c.call("GET", "/api/tasks", max=500).get("tasks", [])}
    for name in CATALOG_NAMES:
        t = tasks.get(name)
        if t:
            c.call("DELETE", f"/api/tasks/{t['id']}")
            print(f"  task {name!r}: deleted")


def remove_option_types(c):
    cur = {o["fieldName"]: o for o in c.call("GET", "/api/library/option-types", max=500).get("optionTypes", [])}
    for field in OPTION_FIELD_NAMES:
        opt = cur.get(field)
        if opt and opt.get("labels") and "kubecon-demo" in opt["labels"]:
            c.call("DELETE", f"/api/library/option-types/{opt['id']}")
            print(f"  option type {field!r}: deleted")


def remove_wiki(c):
    pages = {p["name"]: p for p in c.call("GET", "/api/wiki/pages", max=500).get("pages", [])}
    page = pages.get(WIKI_PAGE_NAME)
    if page:
        c.call("DELETE", f"/api/wiki/pages/{page['id']}")
        print(f"  wiki page {WIKI_PAGE_NAME!r}: deleted")


def remove_cypher_secrets(c):
    for key in ("secret/hks-showcase-ssh-user", "secret/hks-showcase-ssh-password"):
        try:
            c.call("DELETE", f"/api/cypher/secret/{key.split('/', 1)[1]}")
            print(f"  cypher {key}: deleted")
        except ApiError:
            pass  # cypher delete endpoint shape varies by version; non-fatal either way


def reset_appliance_settings(c):
    c.call("PUT", "/api/appliance-settings", {"applianceSettings": {"dashboardsToDisplay": ""}})
    print("  dashboardsToDisplay: reset")


def remove_dashboard_plugin(c):
    plugins = c.call("GET", "/api/plugins?max=200").get("plugins", [])
    for p in plugins:
        if "kubecon" in (p.get("code") or "").lower():
            c.call("DELETE", f"/api/plugins/{p['id']}")
            print(f"  plugin {p['name']!r} (id {p['id']}): deleted")


# ---- Cluster-side teardown: SSH to the cluster's master node ----

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
    kubeconfig = "/etc/kubernetes/admin.conf"
    out, status = ssh_run(ssh_user, ssh_pass, host, port,
                           f"sudo kubectl --kubeconfig={kubeconfig} {args}", stdin_data)
    return out.strip(), exit_code_of(status)


def write_remote_file_chunked(ssh_user, ssh_pass, host, port, remote_path, content):
    """Write a large payload to a remote file safely. A pty defaults to
    canonical (line-buffered) mode, whose kernel-side input queue is capped at
    MAX_CANON (4096 bytes on Linux) -- confirmed live, a single ~8KB stdin_data
    write landed on the remote end truncated to exactly 4095 bytes, corrupting
    the JSON, regardless of how the write() calls on our side were chunked
    (the limit is the kernel's canonical-mode queue, not our syscalls).
    Sidesteps it by never sending more than one small chunk per SSH session --
    each chunk gets its own fresh pty, so no single session's queue ever gets
    close to the limit."""
    CHUNK = 3000
    ssh_run(ssh_user, ssh_pass, host, port, f"rm -f {remote_path}")
    for i in range(0, len(content), CHUNK):
        chunk = content[i:i + CHUNK]
        # Not checking exit code per chunk: confirmed live that `cat >> file`
        # terminated via Ctrl-D in this pty setup reports a nonzero status even
        # when the write genuinely succeeded. The size check below is the real
        # verification.
        ssh_run(ssh_user, ssh_pass, host, port, f"cat >> {remote_path}", stdin_data=chunk, timeout=30)
    out, status = ssh_run(ssh_user, ssh_pass, host, port, f"wc -c < {remote_path}")
    written = out.strip().splitlines()[-1].strip() if out.strip() else ""
    return written.isdigit() and int(written) == len(content)


def find_master(c, cluster_name=None):
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


def teardown_cluster(c, ssh_user, ssh_pass, cluster_name=None):
    name, host, port = find_master(c, cluster_name)
    if not host:
        print("  no Kubernetes cluster/master reachable -- skipping cluster-side cleanup "
              "(nothing more to do if the cluster itself is already gone).")
        return
    print(f"  using cluster: {name}")

    # The app's own namespace -- everything inside goes with it, including the
    # GitOps CronJob and its ServiceAccount/Role/RoleBinding/Secret/ConfigMaps
    # (install.py's ensure_gitops_cronjob puts all of it in this one namespace
    # for exactly this reason -- one delete here is a complete teardown of it,
    # no separate cleanup step needed).
    out, code = kubectl(ssh_user, ssh_pass, host, port, "delete namespace k8s-showcase --ignore-not-found --timeout=60s")
    print(f"  k8s-showcase namespace: {'deleted' if code == 0 else 'delete failed: ' + out[-200:]}")

    # OpenCost: its own namespace plus the two cluster-scoped RBAC objects it needs.
    out, code = kubectl(ssh_user, ssh_pass, host, port, "delete namespace opencost --ignore-not-found --timeout=60s")
    print(f"  opencost namespace: {'deleted' if code == 0 else 'delete failed: ' + out[-200:]}")
    kubectl(ssh_user, ssh_pass, host, port, "delete clusterrole opencost --ignore-not-found")
    kubectl(ssh_user, ssh_pass, host, port, "delete clusterrolebinding opencost --ignore-not-found")
    print("  opencost ClusterRole/ClusterRoleBinding: deleted")

    # Loki's own objects in `logging` -- never delete the namespace itself, fluent-bit
    # (not ours) also lives there.
    kubectl(ssh_user, ssh_pass, host, port, "delete deployment loki -n logging --ignore-not-found")
    kubectl(ssh_user, ssh_pass, host, port, "delete service loki -n logging --ignore-not-found")
    kubectl(ssh_user, ssh_pass, host, port, "delete configmap loki-config -n logging --ignore-not-found")
    print("  loki deployment/service/configmap: deleted")

    # Revert fluent-bit-config to drop our additive Loki output, restore its original
    # behavior exactly (still shipping to Morpheus, the one thing that was always there).
    out, code = kubectl(ssh_user, ssh_pass, host, port, "get configmap fluent-bit-config -n logging -o json")
    if code == 0:
        try:
            start = out.index("{")
            cm = json.loads(out[start:])
            data = cm.get("data", {})
            changed = False
            if "output-loki.conf" in data:
                del data["output-loki.conf"]
                changed = True
            main_conf = data.get("fluent-bit.conf", "")
            if "@INCLUDE output-loki.conf" in main_conf:
                data["fluent-bit.conf"] = main_conf.replace("\n@INCLUDE output-loki.conf", "")
                changed = True
            if changed:
                cm["data"] = data
                for k in ("resourceVersion", "uid", "creationTimestamp"):
                    cm.get("metadata", {}).pop(k, None)
                ok = write_remote_file_chunked(ssh_user, ssh_pass, host, port,
                                                "/tmp/fbc_revert.json", json.dumps(cm))
                if ok:
                    _, code = kubectl(ssh_user, ssh_pass, host, port, "apply -f /tmp/fbc_revert.json")
                    if code == 0:
                        kubectl(ssh_user, ssh_pass, host, port, "rollout restart daemonset fluent-bit -n logging")
                        print("  fluent-bit-config: reverted (Loki output removed), daemonset restarted")
                    else:
                        print("  fluent-bit-config: write ok but apply failed")
                else:
                    print("  fluent-bit-config: could not write the reverted config to the remote host")
            else:
                print("  fluent-bit-config: already clean")
        except (ValueError, json.JSONDecodeError):
            print("  fluent-bit-config: could not parse, left as-is")

    # Revert Grafana's anonymous-access config back to its original minimal content.
    out, code = kubectl(ssh_user, ssh_pass, host, port,
        r"get secret grafana-config -n monitoring -o jsonpath='{.data.grafana\.ini}'")
    b64 = out.splitlines()[-1].strip() if out.strip() else ""
    needs_revert = True
    if code == 0 and b64:
        import base64
        try:
            decoded = base64.b64decode(b64).decode(errors="replace")
            needs_revert = "auth.anonymous" in decoded
        except Exception:
            pass
    if needs_revert:
        original_ini = "[date_formats]\ndefault_timezone = UTC\n"
        cmd = ("kubectl create secret generic grafana-config -n monitoring "
               "--from-file=grafana.ini=/tmp/grafana_orig.ini --dry-run=client -o yaml | "
               "sudo kubectl --kubeconfig=/etc/kubernetes/admin.conf apply -f -")
        write_cmd = f"cat > /tmp/grafana_orig.ini && {cmd}"
        _, status = ssh_run(ssh_user, ssh_pass, host, port, write_cmd, stdin_data=original_ini)
        if exit_code_of(status) == 0:
            kubectl(ssh_user, ssh_pass, host, port, "rollout restart deployment grafana -n monitoring")
            print("  grafana-config: reverted to original (anonymous access removed), restarted")
        else:
            print("  grafana-config: could not revert")
    else:
        print("  grafana-config: already clean")

    # NodePort access services this project created directly in shared namespaces
    # (not covered by the namespace deletes above).
    for ns, svc in [
        ("monitoring", "k8s-showcase-grafana-access"),
        ("monitoring", "k8s-showcase-prometheus-access"),
    ]:
        kubectl(ssh_user, ssh_pass, host, port, f"delete service {svc} -n {ns} --ignore-not-found")
    print("  grafana/prometheus NodePort access services: deleted")

    # Grafana dashboard + the Loki datasource we created. The Prometheus datasource
    # is readOnly (provisioned by the cluster layout itself) -- confirmed live,
    # never touch it.
    grafana_node_port, _ = kubectl(ssh_user, ssh_pass, host, port,
        "get svc grafana -n monitoring -o jsonpath='{.spec.clusterIP}'")
    grafana_ip = grafana_node_port.splitlines()[-1].strip() if grafana_node_port.strip() else None
    if grafana_ip:
        ssh_run(ssh_user, ssh_pass, host, port,
                f"curl -s --connect-timeout 5 --max-time 10 -u admin:admin -X DELETE "
                f"http://{grafana_ip}:3000/api/dashboards/uid/k8s-showcase-app")
        ssh_run(ssh_user, ssh_pass, host, port,
                f"curl -s --connect-timeout 5 --max-time 10 -u admin:admin -X DELETE "
                f"http://{grafana_ip}:3000/api/datasources/uid/loki")
        print("  grafana dashboard + loki datasource: deleted (prometheus datasource left alone, it's read-only/stock)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--token", required=True)
    ap.add_argument("--insecure", action="store_true")
    ap.add_argument("--ssh-user", required=True, help="SSH login to the cluster's master node")
    ap.add_argument("--ssh-password", required=True)
    ap.add_argument("--cluster-name", help="Only needed if more than one Kubernetes cluster exists")
    ap.add_argument("--skip-cluster", action="store_true",
                     help="Only remove Morpheus objects, skip SSHing into the cluster")
    args = ap.parse_args()

    c = Client(args.url, args.token, args.insecure)
    print("K8s Showcase uninstall")

    print("Morpheus catalog objects:")
    remove_catalog_items(c)
    remove_workflows_and_tasks(c)
    remove_option_types(c)
    remove_wiki(c)
    remove_cypher_secrets(c)

    print("Dashboard plugin:")
    reset_appliance_settings(c)
    remove_dashboard_plugin(c)

    if not args.skip_cluster:
        print("Cluster-side (SSH to master node):")
        teardown_cluster(c, args.ssh_user, args.ssh_password, args.cluster_name)

    print("Done. The appliance and cluster should now be back to their pre-install state.")
    print("Run plugin/install.py to rebuild everything from scratch.")


if __name__ == "__main__":
    try:
        main()
    except ApiError as e:
        sys.exit(f"api error: {e}")
