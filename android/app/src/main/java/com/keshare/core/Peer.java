package com.keshare.core;

import org.json.JSONObject;

/** 一个对端设备。 */
public class Peer {
    public String deviceId = "";
    public String name = "Android";
    public String kind = "android";
    public String osVersion = "";
    public String host = "";
    public int port = Protocol.PORT;
    public int rssi = 0;
    public boolean connected = false;
    public String source = "udp";   // udp | ble | manual

    public static Peer fromAnnounce(JSONObject json, String fallbackHost) {
        Peer p = new Peer();
        p.deviceId = json.optString("did", "");
        p.name = json.optString("name", "Android");
        p.kind = json.optString("kind", "android");
        p.osVersion = json.optString("osv", "");
        p.host = json.optString("host", fallbackHost);
        if (p.host == null || p.host.isEmpty()) p.host = fallbackHost;
        p.port = json.optInt("port", Protocol.PORT);
        return p;
    }

    public JSONObject announce() {
        JSONObject j = new JSONObject();
        try {
            j.put("ks", 1).put("t", "announce").put("did", deviceId)
             .put("name", name).put("kind", kind).put("osv", osVersion)
             .put("port", port).put("host", host);
        } catch (Exception ignored) { }
        return j;
    }

    public JSONObject probe() {
        JSONObject j = announce();
        try { j.put("t", "probe"); } catch (Exception ignored) { }
        return j;
    }

    public String key() { return deviceId.isEmpty() ? host : deviceId; }
    public String label() { return name + " · " + host + ":" + port; }
}