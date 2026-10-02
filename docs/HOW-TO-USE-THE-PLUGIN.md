# K8s Showcase Plugin - How to Use It

A real, step by step walkthrough. Every screenshot is a real screenshot of a real action taken against a live Morpheus appliance and a live Kubernetes cluster. Nothing here is staged or empty - forms are shown filled in with real values, and every change shown actually happened on the cluster.

## What this plugin is

A Morpheus plugin that deploys a small real app onto a Kubernetes cluster, lets you switch its live version between two colors (blue and green) with zero downtime, and keeps it in sync with a GitHub repo automatically. It also installs a dashboard, Grafana, Prometheus, and OpenCost so you can see everything that is happening, live.

Nothing is hardcoded to one appliance or one cluster. The installer finds the Kubernetes cluster, its master node, and everything else it needs at run time.

## Part 1: Install the plugin

Two things install it:

1. **The catalog and automation** (Tasks, Catalog Items, the GitOps CronJob, Cypher secrets): run `plugin/install.py` once, pointed at your Morpheus appliance and your cluster's SSH login. This step is a script, not a UI click.
2. **The dashboard widget** (a separate Groovy plugin, built with Gradle): upload the built jar once, by hand, through the Morpheus UI.

### Step 1.1: Upload the dashboard plugin jar

Go to **Administration > Integrations > Plugins**, click **+ Add Plugin**, and upload the jar built from `plugin/dashboard/` (`gradle clean shadowJar`, the file lands in `build/libs/`).

![Administration > Integrations > Plugins, showing K8s Showcase Dashboard installed and healthy](images/administration-plugins-upload.jpg)

### Step 1.2: Fill in the plugin's Settings

Click the pencil icon next to the plugin to open its settings. Enter:

- **Cluster SSH username / password**: login for your cluster's nodes (used to read live pod status)
- **Morpheus API token**: create one under your user menu > API Access, or Administration > API tokens

![The plugin's Settings form: SSH username, SSH password, Morpheus API token](images/plugin-settings.jpg)

This is a one-time step, same as installing any plugin.

## Part 2: The catalog

All seven catalog items live under **Provisioning > Catalog**, category "KubeCon Demo".

![Provisioning > Catalog, all seven K8s Showcase catalog items under the KubeCon Demo category](images/catalog-list.jpg)

## Part 3: Deploy the app, step by step

Click **Deploy K8s Showcase App > ORDER**. Fill in:

- **Image Tag (version)**: `1.2`
- **Color (blue or green)**: `blue`
- **Replica Count**: `2` (already defaulted)
- Everything else can stay blank for a manual order

![The Deploy K8s Showcase App form filled in with real values: 1.2, blue](images/order-deploy-filled.jpg)

Click **Order Now**. Morpheus redirects to Order History, which shows the order as **Complete** within a few seconds.

![Order History showing the Deploy order as Complete](images/order-history-complete.jpg)

### Verify the deploy with kubectl

```
$ kubectl get deployment k8s-showcase-blue -n k8s-showcase
NAME                READY   UP-TO-DATE   AVAILABLE   AGE
k8s-showcase-blue   2/2     2            2           7h14m
```

`2/2` means both replicas are up and passing their readiness probe.

## Part 4: Switch live traffic, with before/during/after

Before the switch, the app (open the link any order prints, or click **Open the app** on the dashboard) shows **GREEN** live:

![The app before switching: GREEN, 1.2, 2/2 ready](images/app-before-switch.jpg)

Click **Switch K8s Showcase Traffic > ORDER**. There is only one field (Cluster Name), which you leave blank if you have one cluster:

![The Switch K8s Showcase Traffic order form](images/order-switch.jpg)

Click **Order Now**. Refresh the app's own page a few seconds later and you catch it mid-transition - blue is already live, green is still shutting down:

![The app during the switch: blue is LIVE, green pods are TERMINATING](images/app-during-switch.jpg)

A few seconds after that, it settles:

![The app after the switch: BLUE, 100% traffic, green has no pods left](images/app-after-switch.jpg)

### Verify the switch with kubectl

```
$ kubectl get pods -n k8s-showcase
NAME                                  READY   STATUS        RESTARTS   AGE
k8s-showcase-blue-6c765fc978-2slm7   1/1     Running       0          80s
k8s-showcase-blue-6c765fc978-rw9cc   1/1     Running       0          80s
k8s-showcase-green-5c7785674d-gv6nl  1/1     Terminating   0          7h13m
k8s-showcase-green-5c7785674d-ml7sf  1/1     Terminating   0          7h13m

$ kubectl get svc k8s-showcase -n k8s-showcase -o jsonpath='{.spec.selector.slot}'
blue
```

The Service's own selector is the source of truth for which color is actually live - this is what the switch really changed.

### Verify the switch in Morpheus

The Operations dashboard tile updates immediately, showing exactly who switched it and when:

![The Morpheus dashboard after the switch: Blue LIVE, 2/2, "Last switch: ... 1 min ago"](images/dashboard-after-switch.jpg)

### Verify the switch in Grafana

Click **Grafana** on the dashboard tile. The real pod counts confirm it:

![Grafana showing k8s-showcase-blue at 2/2 ready, k8s-showcase-green at 0/0](images/grafana-after-switch.jpg)

## Part 5: GitOps - edit a file on GitHub, watch it deploy itself

This is the part that needs no catalog order at all. Installing the plugin sets up a Kubernetes CronJob that checks this repo's `helm/k8s-showcase/values.yaml` every minute, and auto-deploys whatever is committed there.

### Where to edit it

Go to the file on GitHub and click the pencil (edit) icon: `https://github.com/<you>/<repo>/edit/main/helm/k8s-showcase/values.yaml`

![The real GitHub edit screen for values.yaml, before any change](images/github-edit-screen.jpg)

The three fields that matter:

- `image.tag` - which version to run
- `replicaCount` - how many copies
- `color` - blue or green, which one goes live

### Make a real edit

Change line 5 from `replicaCount: 2` to `replicaCount: 3`:

![The edited line: replicaCount: 3, with the Commit changes button now active](images/github-edit-filled.jpg)

Click **Commit changes...**. GitHub shows a commit dialog (it even suggests a commit message):

![The commit dialog: message "Increase replica count from 2 to 3", committing directly to main](images/github-commit-dialog.jpg)

Click **Commit changes**. The real commit lands immediately:

![The real commit confirmed on GitHub: b591523, "Increase replica count from 2 to 3", by NixndME](images/github-commit-confirmed.jpg)

### What happens next, with no one touching Morpheus

Within a minute, the CronJob notices the new commit and acts on it. Here is the actual log from the pod that picked it up:

```
new commit detected: b591523c0c by NixndME: Increase replica count from 2 to 3
deploying imageTag='1.2' color='green' replicaCount='3'
deploy complete
committed color 'green' differs from live 'blue' -- switching traffic
switch complete -- traffic now on the committed color
health check passed: green is 3/3 ready
done
```

It detected the commit, deployed with the new replica count, noticed the committed color (`green`) differed from what was live (`blue` from Part 4), switched automatically, and confirmed the new version was actually healthy before calling it done.

### Verify it on the app's own page

Refresh the app. It now shows **GREEN**, 3 pods, all live:

![The app after GitOps auto-deployed: GREEN, 3/3 pods, all LIVE](images/app-after-gitops.jpg)

Scroll down to the Build panel - it shows the exact commit that caused this, automatically:

![The Build panel: Source GITOPS, commit b591523c0c, author NixndME, message "Increase replica count from 2 to 3"](images/app-build-panel-gitops.jpg)

### Verify it in Morpheus

Recent Activity on the dashboard shows the Deploy and Switch that just happened, timed exactly to match the commit - no one placed these orders by hand:

![Recent Activity showing the auto-triggered Deploy and Switch, both "3 min ago"](images/dashboard-recent-activity-gitops.jpg)

### Verify it in Grafana

```
k8s-showcase-blue:  0 ready / 0 desired
k8s-showcase-green: 3 ready / 3 desired
```

![Grafana confirming 3 ready green pods, 0 blue, matching the committed replicaCount](images/grafana-after-gitops.jpg)

### Verify it with kubectl, one more time

```
$ kubectl get pods -n k8s-showcase
NAME                                  READY   STATUS    RESTARTS   AGE
k8s-showcase-green-6cdcd578d4-245sq   1/1     Running   0          2m19s
k8s-showcase-green-6cdcd578d4-jr8xb   1/1     Running   0          2m19s
k8s-showcase-green-6cdcd578d4-wznfp   1/1     Running   0          2m19s
```

Three real pods, created at the exact same moment, matching the commit.

## Part 6: The other catalog items, each with real proof

### Rollback K8s Showcase App

Rolls back to whichever color isn't live right now. Different from Switch: if that color is scaled to zero, Rollback scales it back up by itself, waits for it to actually become healthy, and only then switches - one order instead of two. Refuses to switch if the color never becomes healthy, so it can't accidentally send traffic to something broken.

Before: green live, 3/3 ready, blue has no pods at all.

![The app before Rollback: GREEN, 3/3 ready, blue has no pods](images/app-before-rollback.jpg)

Order it (only field is Cluster Name, left blank):

![The Rollback K8s Showcase App order form](images/order-rollback.jpg)

**A real bug was caught and fixed while testing this**: the first live attempt scaled blue to the wrong replica count and then failed, reporting `0/2 ready` even though the pods were fine - a stripped-output bug (the same kind of SSH-preamble issue documented earlier in this project) meant the readiness check could never read a real number, so it always read `0` and always ran out the clock. Fixed, and the real before/after in Morpheus's own Recent Activity shows both the failure and the fix, honestly:

![Recent Activity showing the real Rollback error (155.0s) immediately followed by the real fix (14.2s, success)](images/dashboard-rollback-activity.jpg)

After the fix, the real job output:

```
currently live: 'green'. Rolling back to: 'blue'.
slot 'blue' is scaled to 0 -- scaling it back up to 3 replica(s) before switching (image: nixndme/hks-kubecon-demo:1.2)
slot 'blue': 3/3 ready after scale-up.
service/k8s-showcase patched
Rolled back: traffic is now live on slot 'blue' (image: nixndme/hks-kubecon-demo:1.2).
Scaled down the previous slot ('green') to 0 replicas.
```

After: blue live, 3/3 ready, all three pods created at the same moment:

![The app after Rollback: BLUE, 100% traffic, 3/3 ready, all LIVE](images/app-after-rollback.jpg)

![The Morpheus dashboard confirming Blue LIVE, 3/3 pods ready](images/dashboard-after-rollback.jpg)

### Canary Traffic Split

Sends a chosen percentage of real traffic to a second color at the same time as the live one - a true weighted split, not blue-green. Fill in the canary color and a weight from 0 to 100:

![The Canary Traffic Split order form filled in: green, 40%](images/order-canary-filled.jpg)

Weight 0 turns it off completely. This is additive: the normal blue-green Switch still works exactly the same underneath it.

**Proof it's real traffic splitting, not just a setting**: sampled the live app 20 times in a row right after ordering a 40% split to green:

```
$ for i in $(seq 1 20); do curl -sk https://.../api/status | jq -r .color; done | sort | uniq -c
     15 blue
      5 green
```

5 out of 20 real responses came from green - genuine weighted routing through ingress-nginx's own canary mechanism, confirmed by the Morpheus dashboard too:

![The dashboard's Automation panel showing "40% CANARY -> GREEN", and the resource tree now listing the real canary Ingress and per-color Services](images/dashboard-canary-active.jpg)

Ordering it again with weight `0` deletes the canary Ingress entirely - confirmed by the same panel reading "Off" immediately after, with the canary Ingress gone from the resource tree (see the Synthetic Traffic screenshot below, taken right after turning Canary off).

### Synthetic Traffic Generator

Turns a steady trickle of real HTTP requests at the app on or off, so the dashboards show real moving data even when nobody's actually clicking around. Defaults are already sensible:

![The Synthetic Traffic Generator order form: enabled=true, 5 seconds between requests](images/order-synthetic-traffic.jpg)

**Proof it's real traffic**: the app's own Recent Requests table fills up with genuine requests from the generator pod's own IP, 5 seconds apart, `curl/8.10.1` as the user agent (the exact tool the generator uses):

![Recent Requests showing 5 real synthetic requests, 5 seconds apart, from curl/8.10.1](images/app-synthetic-traffic-requests.jpg)

The dashboard confirms it's genuinely running (not just an order that succeeded) by reading the real Deployment's pod count, and shows Canary correctly back to "Off" after the test above:

![The dashboard's Automation panel: Canary Split Off, Synthetic Traffic On (1 pod)](images/dashboard-synthetic-traffic-on.jpg)

Order it again with `enabled=false` and the Deployment is deleted outright - the requests stop immediately.

### Check K8s Showcase Status

Prints a plain report: which color is live, both versions, both pod counts, and the current link to open the app.

![The Check K8s Showcase Status order form](images/order-status.jpg)

Morpheus's own UI does not surface a catalog task's successful console output anywhere (confirmed by direct testing - Order History, Jobs, and Job Executions pages all stay silent on success). The real output is reachable through the API, and it's genuinely live data:

```
==================================================
K8s Showcase App - cluster: HKS
==================================================
BLUE   version 1.2        3/3 pods ready <-- LIVE
GREEN  version 1.2        2/2 pods ready
--------------------------------------------------
Open it here: https://kcs-app.nixndme.com/
==================================================
```

### Open Observability (Grafana + Prometheus)

Exposes the cluster's existing Grafana and Prometheus (ClusterIP-only by default), turns on Grafana's anonymous read-only access, installs OpenCost for real cost numbers, and ships real pod logs to Loki. Order this once after Deploy. Safe to order again any time - it only fixes what's missing.

![The Open Observability order form](images/order-observability.jpg)

Real output from ordering it again on an already-set-up cluster (idempotent - it reports what's already there instead of reinstalling):

```
using cluster: HKS
GRAFANA: https://kcs-grafana.nixndme.com/
PROMETHEUS: http://192.168.122.231:30093/
OPENCOST (real cost data API, used by the dashboard, not meant to be opened directly): http://192.168.122.232:32561/
GRAFANA: app dashboard ready (Fleet Overview + resource gauges + live logs)
```

## The app's own page, in full

- **Version and color**, with a pulsing live indicator
- **Live Traffic Mix** - the real blue/green split, read straight from the cluster
- **Live Rollout** - every individual pod, color coded: green means it is actually serving live traffic right now, amber means it's still starting, red/faded means it's terminating
- **Transition Log** - a running, timestamped list of every color switch and version change seen since the page was opened
- **Build** - the commit SHA, author, and message behind whatever is currently running, and whether it got there from the catalog or from GitOps
- **Recent Requests** - who's actually hitting this exact pod

## The Morpheus dashboard, in full

Go to **Operations**, look for the **K8s Showcase** tile. Everything on it is a live read from the cluster or Morpheus:

- **Resource tree** - the real ownership hierarchy: namespace, then Service/Ingress/ConfigMap/Secret/Deployment as siblings, pods nested under their actual owning Deployment
- **Live Metrics** - app CPU/memory, restarts, cluster node count and CPU/memory, from Prometheus
- **SLO (last 15 min)** - the real fraction of the live deployment's pods that were actually ready over the last 15 minutes, against a 99% target, with the remaining error budget
- **Automation** - whether a canary split is active right now and at what weight, and whether the synthetic traffic generator is running
- **Cost** - real billed cost from Morpheus's own invoices, plus a clearly labeled reference estimate
- **Recent Activity** - who ordered what and when, including automatic GitOps deploys, switches, and auto-rollbacks

## If you uninstall

`plugin/uninstall.py` removes everything this plugin created: every catalog item, task, workflow, and Option Type, the dashboard plugin, the Cypher secrets, and on the cluster side - the app's whole namespace (which takes the GitOps CronJob, the canary objects, and the synthetic traffic generator with it), OpenCost, Loki, and it reverts the shared Grafana and fluent-bit config back to exactly what they were before.
