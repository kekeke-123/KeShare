package com.keshare.discovery;

import android.content.Context;
import android.net.wifi.WifiManager;
import android.util.Log;

import com.keshare.core.Peer;
import com.keshare.core.Protocol;

import org.json.JSONObject;

import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.InetAddress;
import java.net.NetworkInterface;
import java.util.Collections;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

/** UDP 发现：广播 probe，收到 probe 单播 announce，收到 announce 上报对端。 */
public class UdpDiscovery {

    public interface Listener {
        void onPeer(Peer peer);
    }

    private static final String TAG = "KsUdp";
    private final Peer self;
    private final Listener listener;
    private final Map<String, Long> seen = new ConcurrentHashMap<>();
    private volatile boolean running = false;
    private DatagramSocket socket;
    private WifiManager.MulticastLock lock;

    public UdpDiscovery(Context context, Peer self, Listener listener) {
        this.self = self;
        this.listener = listener;
        WifiManager wifi = (WifiManager) context.getApplicationContext()
                .getSystemService(Context.WIFI_SERVICE);
        if (wifi != null) {
            lock = wifi.createMulticastLock("keshare-udp");
            lock.setReferenceCounted(false);
        }
    }

    public void start() {
        if (running) return;
        running = true;
        if (lock != null) lock.acquire();
        new Thread(this::loop, "ks-udp").start();
    }

    public void stop() {
        running = false;
        if (socket != null) socket.close();
        if (lock != null && lock.isHeld()) lock.release();
    }

    private void loop() {
        try {
            socket = new DatagramSocket(Protocol.PORT);
            socket.setBroadcast(true);
            socket.setSoTimeout(1000);
        } catch (Exception e) {
            Log.e(TAG, "绑定 UDP 失败", e);
            running = false;
            return;
        }

        byte[] buf = new byte[2048];
        long lastProbe = 0;
        while (running) {
            long now = System.currentTimeMillis();
            if (now - lastProbe > 5000) {
                lastProbe = now;
                broadcastProbe();
            }
            try {
                DatagramPacket packet = new DatagramPacket(buf, buf.length);
                socket.receive(packet);
                String text = new String(packet.getData(), 0, packet.getLength(), "UTF-8");
                JSONObject json = new JSONObject(text);
                String type = json.optString("t", "");
                String did = json.optString("did", "");
                if (did.isEmpty() || did.equals(self.deviceId)) continue;

                String from = packet.getAddress().getHostAddress();
                if ("probe".equals(type)) {
                    sendAnnounce(packet.getAddress());
                } else if ("announce".equals(type)) {
                    Long prev = seen.get(did);
                    if (prev != null && System.currentTimeMillis() - prev < 2000) continue;
                    seen.put(did, System.currentTimeMillis());
                    Peer peer = Peer.fromAnnounce(json, from);
                    peer.source = "udp";
                    listener.onPeer(peer);
                }
            } catch (java.net.SocketTimeoutException ignored) {
            } catch (Exception e) {
                if (!running) break;
            }
        }
        if (socket != null) socket.close();
    }

    private void broadcastProbe() {
        try {
            byte[] data = self.probe().toString().getBytes("UTF-8");
            DatagramPacket global = new DatagramPacket(data, data.length,
                    InetAddress.getByName("255.255.255.255"), Protocol.PORT);
            socket.send(global);

            List<NetworkInterface> interfaces = Collections.list(NetworkInterface.getNetworkInterfaces());
            for (NetworkInterface ni : interfaces) {
                if (!ni.isUp() || ni.isLoopback()) continue;
                for (java.net.InterfaceAddress addr : ni.getInterfaceAddresses()) {
                    InetAddress b = addr.getBroadcast();
                    if (b == null) continue;
                    socket.send(new DatagramPacket(data, data.length, b, Protocol.PORT));
                }
            }
        } catch (Exception e) {
            Log.w(TAG, "广播失败: " + e.getMessage());
        }
    }

    private void sendAnnounce(InetAddress to) {
        try {
            byte[] data = self.announce().toString().getBytes("UTF-8");
            socket.send(new DatagramPacket(data, data.length, to, Protocol.PORT));
        } catch (Exception ignored) { }
    }
}