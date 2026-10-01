# K8s Showcase App

A small live-telemetry demo app used to show Morpheus Kubernetes Service (MKS)
in action: deploys, upgrades, and a blue-green traffic switch, all visible on
the app's own page while it happens.

## What is inside

- `app/` the real app: a small Flask service (`app.py`, `templates/index.html`,
  `Dockerfile`). Shows its own version/color, real visitor requests, and the
  live blue/green pod mix read straight from the Kubernetes API. Built and
  pushed to Docker Hub as `nixndme/hks-kubecon-demo`.
- `k8s/` plain Kubernetes YAML reference (Deployment, Service, Ingress). The
  Morpheus plugin builds its own copy of this at deploy time; this folder is
  for reference/testing outside Morpheus.
- `helm/k8s-showcase/` the same app as a Helm chart. `values.yaml` is the file
  the GitOps poller watches — bump `image.tag` here and commit, and the
  cluster redeploys automatically within a minute.

## Fields Morpheus fills in

- `imageTag`, the image tag to run (the version).
- `appColor`, `blue` or `green`.
- `replicaCount`, how many pods to run.

## Deploying by hand (for testing, outside Morpheus)

```
kubectl apply -f k8s/
```

or

```
helm install k8s-showcase ./helm/k8s-showcase
```

## Building and pushing a new version of the app itself

```
cd app
docker build -t nixndme/hks-kubecon-demo:<new-tag> .
docker push nixndme/hks-kubecon-demo:<new-tag>
```

Then bump `image.tag` in `helm/k8s-showcase/values.yaml` to `<new-tag>` and
commit — the GitOps poller picks it up and redeploys automatically.
