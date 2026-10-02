package com.kubecon.dashboard

import com.morpheusdata.core.MorpheusContext
import com.morpheusdata.core.Plugin
import com.morpheusdata.core.dashboard.AbstractDashboardItemTypeProvider
import com.morpheusdata.model.DashboardItem
import com.morpheusdata.model.DashboardItemType
import com.morpheusdata.views.HTMLResponse
import com.morpheusdata.views.ViewModel
import groovy.util.logging.Slf4j

/** The one widget: live slot, blue vs green version/ready count, and a link to the app. */
@Slf4j
class K8sShowcaseItemProvider extends AbstractDashboardItemTypeProvider {

    Plugin plugin
    MorpheusContext morpheusContext

    K8sShowcaseItemProvider(Plugin plugin, MorpheusContext context) {
        this.plugin = plugin
        this.morpheusContext = context
    }

    @Override MorpheusContext getMorpheus() { morpheusContext }
    @Override Plugin getPlugin() { plugin }
    @Override String getCode() { 'kubecon-k8s-showcase-status' }
    @Override String getName() { 'K8s Showcase status' }

    @Override
    DashboardItemType getDashboardItemType() {
        log.info('[KUBECON-DASH] getDashboardItemType() called')
        DashboardItemType rtn = new DashboardItemType()
        rtn.name = getName()
        rtn.code = getCode()
        rtn.category = 'kubecon-k8s-showcase'
        rtn.title = 'K8s Showcase App'
        rtn.description = 'Live deploy status for the KubeCon K8s Showcase demo app'
        rtn.uiSize = 'md'
        rtn.templatePath = 'hbs/dash/kubecon-k8s-showcase'
        // Must be non-null or the asset manifest NPEs and the whole plugin fails to load.
        rtn.scriptPath = 'kubecon-k8s-showcase.js'
        // NO rtn.permission, NO rtn.accessTypes — deliberately absent, not forgotten.
        // Setting accessTypes without a matching permission appears to be what made
        // this widget 500 silently before render was ever reached (no permission to
        // check it against). This demo has nothing sensitive to gate anyway.
        log.info("[KUBECON-DASH] item type code=${rtn.code} template=${rtn.templatePath} script=${rtn.scriptPath}")
        return rtn
    }

    @Override
    HTMLResponse renderDashboardItem(DashboardItem dashboardItem, Map<String, Object> opts) {
        log.info("[KUBECON-DASH] renderDashboardItem() CALLED type=${dashboardItem?.type?.code}")
        try {
            K8sShowcaseSettings settings
            try {
                settings = K8sShowcaseSettings.parse(morpheusContext.getSettings(plugin).blockingGet())
            } catch (Throwable t) {
                log.error('[KUBECON-DASH] could not read plugin settings', t)
                settings = new K8sShowcaseSettings()
            }
            log.info("[KUBECON-DASH] settings configured=${settings.isConfigured()}")

            Map data = K8sShowcaseQuery.fetch(settings)
            log.info("[KUBECON-DASH] query result: ${data}")
            K8sShowcaseView view = K8sShowcaseView.of(settings, data)
            ViewModel<K8sShowcaseView> model = new ViewModel<K8sShowcaseView>()
            model.object = view
            HTMLResponse rtn = getRenderer().renderTemplate('hbs/dash/kubecon-k8s-showcase', model)
            log.info("[KUBECON-DASH] renderDashboardItem() produced ${rtn ? 'HTML' : 'NULL'}")
            return rtn
        } catch (Throwable t) {
            log.error('[KUBECON-DASH] renderDashboardItem() FAILED', t)
            throw t
        }
    }
}
