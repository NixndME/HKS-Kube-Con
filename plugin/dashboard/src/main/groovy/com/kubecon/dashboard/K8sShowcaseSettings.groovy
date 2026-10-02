package com.kubecon.dashboard

import groovy.json.JsonSlurper

/** The three settings this plugin needs, read as one JSON blob from Morpheus. */
class K8sShowcaseSettings {

    static final String SSH_USER_FIELD = 'sshUsername'
    static final String SSH_PASS_FIELD = 'sshPassword'
    static final String API_TOKEN_FIELD = 'apiToken'

    String sshUsername
    String sshPassword
    String apiToken

    boolean isConfigured() {
        return sshUsername != null && sshPassword != null && apiToken != null
    }

    static K8sShowcaseSettings parse(String json) {
        K8sShowcaseSettings s = new K8sShowcaseSettings()
        if (!json?.trim()) return s
        Object doc
        try {
            doc = new JsonSlurper().parseText(json)
        } catch (Exception ignored) {
            return s
        }
        if (!(doc instanceof Map)) return s
        s.sshUsername = value(doc[SSH_USER_FIELD])
        s.sshPassword = value(doc[SSH_PASS_FIELD])
        s.apiToken = value(doc[API_TOKEN_FIELD])
        return s
    }

    private static String value(Object raw) {
        return raw?.toString()?.trim() ?: null
    }
}
