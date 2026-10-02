package com.kubecon.dashboard

import com.morpheusdata.core.Plugin
import com.morpheusdata.model.OptionType
import groovy.util.logging.Slf4j

/**
 * K8s Showcase Dashboard — an ArgoCD style view of the KubeCon demo app:
 * which color (blue/green) is live, what version each side runs, and a
 * link to open the running app.
 *
 * Kept as its own plugin, separate from the Task/Catalog side of the demo
 * (which is plain Morpheus objects created by plugin/install.py). This
 * plugin only adds the dashboard. Nothing here deploys anything.
 */
@Slf4j
class K8sShowcasePlugin extends Plugin {

    @Override
    String getCode() { 'kubecon-k8s-showcase-dashboard' }

    @Override
    List<OptionType> getSettings() {
        [
            new OptionType(
                name: 'Cluster SSH username',
                code: 'kubecon.sshUsername',
                fieldName: K8sShowcaseSettings.SSH_USER_FIELD,
                fieldLabel: 'Cluster SSH username',
                inputType: OptionType.InputType.TEXT,
                helpText: 'Login used to SSH into the Kubernetes cluster\'s master node to read live status.',
                required: false,
                displayOrder: 0
            ),
            new OptionType(
                name: 'Cluster SSH password',
                code: 'kubecon.sshPassword',
                fieldName: K8sShowcaseSettings.SSH_PASS_FIELD,
                fieldLabel: 'Cluster SSH password',
                inputType: OptionType.InputType.PASSWORD,
                helpText: 'Password for the SSH username above.',
                required: false,
                displayOrder: 1
            ),
            new OptionType(
                name: 'Morpheus API token',
                code: 'kubecon.apiToken',
                fieldName: K8sShowcaseSettings.API_TOKEN_FIELD,
                fieldLabel: 'Morpheus API token',
                inputType: OptionType.InputType.PASSWORD,
                helpText: 'A Morpheus API access token, used by this widget to look up the ' +
                    'Kubernetes cluster and its master node. Create one under your user menu ' +
                    '> API Access, or Administration > API tokens.',
                required: false,
                displayOrder: 2
            ),
        ]
    }

    @Override
    void initialize() {
        this.setName('K8s Showcase Dashboard')
        this.setDescription('ArgoCD-style dashboard for the KubeCon K8s Showcase demo app.')

        // Widget before dashboard: Dashboard.getDashboard() resolves its items by
        // code from the dashboard service at registration time, and silently drops
        // any it cannot yet resolve.
        this.registerProvider(new K8sShowcaseItemProvider(this, this.morpheus))
        this.registerProvider(new K8sShowcaseDashboardProvider(this, this.morpheus))
    }

    @Override
    void onDestroy() {}
}
