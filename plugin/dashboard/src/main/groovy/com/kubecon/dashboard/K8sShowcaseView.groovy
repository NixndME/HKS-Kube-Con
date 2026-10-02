package com.kubecon.dashboard

/**
 * What the widget shows, decided here so it stays out of the template.
 * Three states: not configured (settings missing), unreachable (query
 * failed or app not deployed yet), and the normal live view.
 */
class K8sShowcaseView {

    boolean configured
    boolean reachable
    String errorMessage
    String clusterName
    String live
    boolean blueLive
    boolean greenLive
    Map blue
    Map green
    String url
    List pods
    List bluePods
    List greenPods
    int podCount
    Map lastDeploy
    Map lastSwitch
    String grafanaUrl
    String prometheusUrl
    Map metrics
    boolean hasMetrics
    List activity
    Map resources
    Map cost
    boolean costFromOpenCost
    Map slo
    boolean hasSlo
    Map canary
    boolean canaryActive
    Map trafficGen
    boolean trafficGenEnabled

    static K8sShowcaseView of(K8sShowcaseSettings settings, Map data) {
        K8sShowcaseView v = new K8sShowcaseView()
        v.configured = settings?.isConfigured() ?: false
        if (!v.configured) {
            v.reachable = false
            v.errorMessage = 'Set the SSH username/password and Morpheus API token in ' +
                'Administration > Plugins > K8s Showcase Dashboard > Settings.'
            return v
        }
        if (data == null || data.ok != true) {
            v.reachable = false
            v.errorMessage = (data?.error as String) ?: 'Could not read live status.'
            return v
        }
        v.reachable = true
        v.clusterName = data.clusterName as String
        v.live = data.live as String
        v.blueLive = v.live == 'blue'
        v.greenLive = v.live == 'green'
        Map slots = (data.slots ?: [:]) as Map
        v.blue = slots.blue as Map
        v.green = slots.green as Map
        v.url = data.url as String
        v.pods = (data.pods ?: []) as List
        v.podCount = v.pods.size()
        v.bluePods = v.pods.findAll { it.color == "blue" }
        v.greenPods = v.pods.findAll { it.color == "green" }
        v.lastDeploy = data.lastDeploy as Map
        v.lastSwitch = data.lastSwitch as Map
        Map obs = (data.observability ?: [:]) as Map
        v.grafanaUrl = obs.grafana as String
        v.prometheusUrl = obs.prometheus as String
        v.metrics = (data.metrics ?: [:]) as Map
        v.hasMetrics = v.metrics?.appCpuCores != null
        v.activity = (data.activity ?: []) as List
        v.resources = (data.resources ?: [:]) as Map
        v.cost = (data.cost ?: [:]) as Map
        v.costFromOpenCost = v.cost?.source == 'opencost'
        v.slo = (data.slo ?: [:]) as Map
        v.hasSlo = v.slo?.readyPct != null
        v.canary = data.canary as Map
        v.canaryActive = v.canary != null
        v.trafficGen = (data.trafficGen ?: [:]) as Map
        v.trafficGenEnabled = v.trafficGen?.enabled == true
        return v
    }
}
