package com.keshare.discovery;

import android.bluetooth.BluetoothAdapter;
import android.bluetooth.BluetoothManager;
import android.bluetooth.le.AdvertiseCallback;
import android.bluetooth.le.AdvertiseData;
import android.bluetooth.le.AdvertiseSettings;
import android.bluetooth.le.BluetoothLeAdvertiser;
import android.bluetooth.le.BluetoothLeScanner;
import android.bluetooth.le.ScanCallback;
import android.bluetooth.le.ScanResult;
import android.bluetooth.le.ScanSettings;
import android.content.Context;
import android.util.Log;

import com.keshare.core.Peer;
import com.keshare.core.Protocol;

import java.nio.charset.StandardCharsets;

/**
 * BLE 发现：广播名形如 K|192.168.1.5|47377|a1b2c3，对方无需 UDP 即可拿到 IP。
 * 与 iOS 端 KSNet.m 的广播格式保持一致。
 */
public class BleDiscovery {

    public interface Listener {
        void onPeer(Peer peer);
    }

    private static final String TAG = "KsBle";
    private final BluetoothAdapter adapter;
    private final Peer self;
    private final Listener listener;
    private BluetoothLeAdvertiser advertiser;
    private BluetoothLeScanner scanner;

    public BleDiscovery(Context context, Peer self, Listener listener) {
        BluetoothManager manager = (BluetoothManager) context.getSystemService(Context.BLUETOOTH_SERVICE);
        this.adapter = manager == null ? null : manager.getAdapter();
        this.self = self;
        this.listener = listener;
    }

    public void start() {
        if (adapter == null || !adapter.isEnabled()) {
            Log.w(TAG, "蓝牙不可用");
            return;
        }
        startAdvertising();
        startScanning();
    }

    private void startAdvertising() {
        try {
            advertiser = adapter.getBluetoothLeAdvertiser();
            if (advertiser == null) {
                Log.w(TAG, "设备不支持 BLE 广播");
                return;
            }
            String shortId = self.deviceId.length() > 6
                    ? self.deviceId.substring(self.deviceId.length() - 6) : self.deviceId;
            String name = "K|" + self.host + "|" + self.port + "|" + shortId;

            AdvertiseData data = new AdvertiseData.Builder()
                    .setIncludeDeviceName(true)
                    .addServiceUuid(android.os.ParcelUuid.fromString(
                            "0000fd5a-0000-1000-8000-00805f9b34fb"))
                    .build();

            AdvertiseSettings settings = new AdvertiseSettings.Builder()
                    .setAdvertiseMode(AdvertiseSettings.ADVERTISE_MODE_LOW_LATENCY)
                    .setTxPowerLevel(AdvertiseSettings.ADVERTISE_TX_POWER_HIGH)
                    .setConnectable(false)
                    .build();

            adapter.setName(name);
            advertiser.startAdvertising(settings, data, new AdvertiseCallback() {
                @Override public void onStartSuccess(AdvertiseSettings s) {
                    Log.i(TAG, "BLE 广播已启动: " + name);
                }
                @Override public void onStartFailure(int code) {
                    Log.w(TAG, "BLE 广播失败 code=" + code);
                }
            });
        } catch (SecurityException e) {
            Log.w(TAG, "缺少蓝牙权限: " + e.getMessage());
        }
    }

    private void startScanning() {
        try {
            scanner = adapter.getBluetoothLeScanner();
            if (scanner == null) return;
            ScanSettings settings = new ScanSettings.Builder()
                    .setScanMode(ScanSettings.SCAN_MODE_LOW_LATENCY)
                    .build();
            scanner.startScan(null, settings, new ScanCallback() {
                @Override public void onScanResult(int type, ScanResult result) {
                    handle(result);
                }
                @Override public void onBatchScanResults(java.util.List<ScanResult> results) {
                    for (ScanResult r : results) handle(r);
                }
            });
        } catch (SecurityException e) {
            Log.w(TAG, "缺扫描权限: " + e.getMessage());
        }
    }

    private void handle(ScanResult result) {
        String name = result.getScanRecord() == null ? null : result.getScanRecord().getDeviceName();
        if (name == null || !name.startsWith("K|")) return;
        String[] parts = name.split("\\|");
        if (parts.length < 3) return;
        Peer peer = new Peer();
        peer.host = parts[1];
        peer.port = parseInt(parts[2], Protocol.PORT);
        peer.deviceId = parts.length > 3 ? "ble-" + parts[3] : "ble-" + peer.host.replace(".", "");
        peer.name = "KeShare (" + peer.host + ")";
        peer.kind = "peer";
        peer.rssi = result.getRssi();
        peer.source = "ble";
        listener.onPeer(peer);
    }

    private static int parseInt(String text, int fallback) {
        try { return Integer.parseInt(text.trim()); } catch (Exception e) { return fallback; }
    }

    public void stop() {
        try {
            if (advertiser != null) advertiser.stopAdvertising(null);
        } catch (Exception ignored) { }
        try {
            if (scanner != null) scanner.stopScan(null);
        } catch (Exception ignored) { }
    }
}