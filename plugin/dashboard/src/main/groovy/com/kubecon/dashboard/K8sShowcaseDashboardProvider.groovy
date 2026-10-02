package com.kubecon.dashboard

import com.morpheusdata.core.MorpheusContext
import com.morpheusdata.core.Plugin
import com.morpheusdata.core.dashboard.AbstractDashboardProvider
import com.morpheusdata.model.Dashboard
import com.morpheusdata.model.DashboardItem
import com.morpheusdata.views.HTMLResponse
import com.morpheusdata.views.ViewModel
import groovy.util.logging.Slf4j

@Slf4j
class K8sShowcaseDashboardProvider extends AbstractDashboardProvider {

    Plugin plugin
    MorpheusContext morpheusContext

    K8sShowcaseDashboardProvider(Plugin plugin, MorpheusContext context) {
        this.plugin = plugin
        this.morpheusContext = context
    }

    @Override MorpheusContext getMorpheus() { morpheusContext }
    @Override Plugin getPlugin() { plugin }
    // code and dashboardId are the same string on purpose — whichever one core's
    // mount-div lookup actually keys off, this way it always matches.
    @Override String getCode() { 'kubecon-k8s-showcase' }
    @Override String getName() { 'K8s Showcase' }

    @Override
    Dashboard getDashboard() {
        log.info('[KUBECON-DASH] getDashboard() called')
        Dashboard rtn = new Dashboard()
        rtn.name = getName()
        rtn.code = getCode()
        rtn.dashboardId = 'kubecon-k8s-showcase'
        rtn.category = 'kubecon-k8s-showcase'
        rtn.title = 'K8s Showcase'
        rtn.description = 'KubeCon demo: what is deployed on the Kubernetes cluster, live'
        // MUST be true. With no ?code= param in the request, Morpheus looks up
        // the dashboard row with eq('dashboardId', id) AND eq('defaultView', true).
        // false here makes the row unmatched by that query, so the page never
        // calls renderDashboard() at all, no error, nothing in the log. This
        // was the actual cause of the "empty tile, no error" bug (confirmed
        // against biome's own working dashboard plugin, which sets this true
        // with the same warning in its own comment).
        rtn.defaultDashboard = true
        rtn.enabled = true
        rtn.sourceType = 'system'
        rtn.templatePath = 'hbs/dash/kubecon-k8s-showcase-shell'
        rtn.scriptPath = 'kubecon-k8s-showcase.js'
        rtn.dashboardItems = buildItems(['kubecon-k8s-showcase': ['kubecon-k8s-showcase-status']])
        log.info("[KUBECON-DASH] returning dashboard code=${rtn.code} dashboardId=${rtn.dashboardId} " +
                 "items=${rtn.dashboardItems?.size()}")
        return rtn
    }

    @Override
    HTMLResponse renderDashboard(Dashboard dashboard, Map<String, Object> opts) {
        log.info("[KUBECON-DASH] renderDashboard() CALLED code=${dashboard?.code} template=${dashboard?.templatePath}")
        try {
            HTMLResponse rtn = null
            String templatePath = dashboard?.templatePath
            if (templatePath) {
                ViewModel<Dashboard> model = new ViewModel<Dashboard>()
                model.object = dashboard
                model.opts = opts
                rtn = getRenderer().renderTemplate(templatePath, model)
            }
            log.info("[KUBECON-DASH] renderDashboard() produced ${rtn ? 'HTML' : 'NULL'}")
            return rtn
        } catch (Throwable t) {
            log.error('[KUBECON-DASH] renderDashboard() FAILED', t)
            throw t
        }
    }

    private List<DashboardItem> buildItems(Map<String, List<String>> groups) {
        List<DashboardItem> items = []
        int groupRow = 0
        groups.each { String section, List<String> codes ->
            int column = 0
            codes.each { String code ->
                def itemType = null
                try {
                    itemType = getMorpheus().getDashboard().getDashboardItemType(code).blockingGet()
                } catch (Throwable t) {
                    log.warn("[KUBECON-DASH] item type '${code}' not resolvable yet, will attach on next load")
                }
                if (!itemType) return
                DashboardItem item = new DashboardItem()
                item.type = itemType
                item.itemRow = 0
                item.itemColumn = column++
                item.itemGroup = section
                item.groupRow = groupRow
                items << item
            }
            groupRow++
        }
        items
    }
}
