# K8s Showcase App

A small nginx web app used to demo Morpheus Kubernetes Service (MKS). It shows a version number and a color (blue or green) on the page, so you can see a deploy, an upgrade, or a blue-green switch happen live.

## What is inside

- `k8s/` plain Kubernetes YAML. Deployment, Service, ConfigMap, Ingress.
- `helm/k8s-showcase/` the same app as a Helm chart.

## How the version and color show up

The container uses the normal nginx image. The page text (`index.html.template`) has `${APP_VERSION}` and `${APP_COLOR}` in it. nginx's own startup script fills these in from environment variables before the page is served (this is a built in nginx image feature, no custom image needed).

## Fields Morpheus fills in

In the plain YAML (`k8s/deployment.yaml`):

- `imageTag`, the nginx image tag to run (the version).
- `appColor`, `blue` or `green`.
- `replicaCount`, how many pods to run.

In the Helm chart (`helm/k8s-showcase/values.yaml`), the same three values: `image.tag`, `color`, `replicaCount`.

## Deploying by hand (for testing, outside Morpheus)

```
kubectl apply -f k8s/
```

or

```
helm install k8s-showcase ./helm/k8s-showcase --set image.tag=1.27 --set color=blue
```
