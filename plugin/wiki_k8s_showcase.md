# K8s Showcase — KubeCon Demo

This page explains the K8s Showcase demo: a real app (not a static page) you can
deploy, upgrade, and switch between two live versions (blue/green), all from the
Morpheus catalog, or by just pushing a commit to GitHub.

## What is deployed

A small Flask app (`nixndme/hks-kubecon-demo` on Docker Hub, source in this
repo's `app/` folder), not a placeholder. Its own page shows, live: the running
version and color, which pod served the page, the real blue/green pod mix read
straight from the Kubernetes API, who committed the change that's running
(commit SHA/author/message), and recent visitors.

Everything lives in its own namespace on the Kubernetes cluster, called
`k8s-showcase`. Nothing is shared with other workloads on the cluster.

## The three things you can order, all under Provisioning > Catalog

1. **Deploy K8s Showcase App** — pick a version (image tag), a color (blue or
   green), and how many copies (replicas) to run. The first time you order this,
   whichever color you pick goes live right away. Order it again with the other
   color, and it deploys alongside the first one without touching what is
   currently live — that is what makes the blue-green switch possible next.

2. **Switch K8s Showcase Traffic** — flips live traffic from whichever color is
   live now to the other one. Needs both colors already deployed first.

3. **Check K8s Showcase Status** — prints a plain status report: which color is
   live, both versions, and the link to open the app. Use this any time you want
   to confirm what is actually running, without needing to open a terminal.

4. **Open Observability (Grafana + Prometheus)** — the HKS cluster layout
   already ships Grafana and Prometheus. This order exposes them (they are not
   reachable from outside the cluster by default), sets up Grafana with no login
   needed, installs OpenCost for real cost data, and ships real pod logs to
   Loki, all idempotently.

## GitOps: push a commit, it deploys itself

Installing the plugin also sets up a CronJob inside the `k8s-showcase`
namespace that checks this repo's `helm/k8s-showcase/values.yaml` every minute.
Edit `image.tag` or `color` in that file on GitHub and commit it — within a
minute the CronJob notices, deploys the new version through the same mechanism
as "Deploy K8s Showcase App", and switches live traffic to it automatically if
the color changed. No one needs to be at a keyboard in Morpheus for this to
happen. The app's own "Build" panel and the dashboard's Recent Activity both
show the real commit SHA, author, and message behind whatever is currently
running, whether it got there by catalog order or by a GitHub push.

This CronJob only gets created once the `k8s-showcase` namespace exists (i.e.
after "Deploy K8s Showcase App" has been ordered at least once) — on a brand
new install, order Deploy first, then re-run `install.py` once to turn GitOps
on.

## The live dashboard (the best way to see all of this)

Go to the Morpheus **Operations Dashboard** and look for the **K8s Showcase**
tile. This is the real, recommended way to see the app's status, no catalog
order or terminal needed. It shows, all live, all on one screen:

- Which color is live, each version, and a pulsing LIVE badge
- A one-click link to open the app, Grafana, and Prometheus
- A pod-by-pod table: name, ready state, restarts, which node it runs on
- A full resource tree for the namespace: Deployments, Pods, Services,
  Ingress, ConfigMaps, and Secrets, all real objects read live from the cluster
- Live Metrics: app CPU and memory use, restart count, and the whole
  cluster's node count, CPU%, and memory used/total
- Cost: real billed cost from Morpheus's own invoices for the cluster's nodes
  (honestly $0 on a lab with no price plan attached), plus a clearly labeled
  reference estimate
- Recent Activity: who ordered which catalog item and when, pulled from
  Morpheus's own job history, not guessed

Order "Open Observability" at least once first, so Grafana/Prometheus exist
for the Live Metrics panel and the Grafana/Prometheus links to work.

## Seeing it running

Each order's result includes a working link to open the app in a browser (for
example `http://<a cluster node's IP>:<port>/`). The exact link can change if
Kubernetes reassigns the port, so the safest way to get the current one is to
run "Check K8s Showcase Status" and read its output.

## "Complete" does not always mean it worked

Morpheus marks an order "Complete" once the workflow finishes running, even if
the task inside it failed on purpose (for example: switching traffic when the
other color was never deployed). Always run "Check K8s Showcase Status"
afterward if you are not sure, or check the order's own task result, not just
the word "Complete" on the order history card.

## If you have more than one Kubernetes cluster

Each catalog item has a "Cluster Name" field. Leave it blank if you only have
one Kubernetes cluster — it picks that one automatically. If you have more than
one, the order will tell you to fill in the cluster name and try again, rather
than guessing which one you meant. The GitOps CronJob takes the same field as
an `install.py --cluster-name` argument, for the same reason.

## Honestly stated: what is real data and what is an estimate

Everything on the dashboard is a live read from Morpheus or the cluster itself,
with one exception: the "Estimated / month" figure in the Cost panel is a
simple reference number (pod count times a flat rate), clearly labeled as an
estimate, not real billing. The "Billed this month" figure next to it is real
Morpheus invoice data, genuinely $0 on a lab appliance with no price plan
attached — shown as-is, not hidden or inflated.
