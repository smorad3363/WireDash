import {DashboardConfigurationStore} from "@/stores/DashboardConfigurationStore.js";
import router from "@/router/router.js";

const getHeaders = () => {
    const headers = {"Content-Type": "application/json"};
    const store = DashboardConfigurationStore();
    const crossServer = store.getActiveCrossServer();
    if (crossServer) {
        headers["wg-dashboard-apikey"] = crossServer.apiKey;
        if (crossServer.headers) {
            for (const header of Object.values(crossServer.headers)) {
                if (header.key && header.value && !Object.keys(headers).includes(header.key)) {
                    headers[header.key] = header.value;
                }
            }
        }
    }
    return headers;
};

export const getUrl = (url) => {
    const store = DashboardConfigurationStore();
    const apiKey = store.getActiveCrossServer();
    if (apiKey) return `${apiKey.host}${url}`;
    if (import.meta.env.MODE === "development") return url;
    return `./.${url}`;
};

// A failed peer configuration/QR request must NOT end the admin session.
// Only an explicit HTTP 401 from our local panel is an authentication failure.
// All other responses (including 403, 404, 500 and network errors) are
// returned to the caller so it can display an error without navigating away.
const requestJson = async (url, options, callback) => {
    const store = DashboardConfigurationStore();
    let response;
    try {
        response = await fetch(url, options);
    } catch (error) {
        console.error("API network request failed:", error);
        callback?.({status: false, message: "Could not reach the server. Please try again."});
        return;
    }

    let body;
    try {
        body = await response.json();
    } catch (error) {
        console.error("API response was not valid JSON:", error);
        body = null;
    }

    if (response.status === 401 && !store.getActiveCrossServer()) {
        store.newMessage("WGDashboard", "Sign in session ended, please sign in again", "warning");
        await router.push({path: "/signin"});
        return;
    }

    if (!response.ok) {
        callback?.({
            status: false,
            message: body?.message || `Server request failed (HTTP ${response.status}).`
        });
        return;
    }

    if (!body || typeof body !== "object") {
        callback?.({status: false, message: "Unexpected response from the server."});
        return;
    }

    callback?.(body);
};

export const fetchGet = async (url, params=undefined, callback=undefined) => {
    const query = new URLSearchParams(params).toString();
    return requestJson(`${getUrl(url)}?${query}`, {headers: getHeaders()}, callback);
};

export const fetchPost = async (url, body, callback) => {
    return requestJson(getUrl(url), {
        headers: getHeaders(),
        method: "POST",
        body: JSON.stringify(body)
    }, callback);
};
