package com.keshare.ui;

import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.os.BatteryManager;

/**
 * 温控降质：与 Windows 端 ThermalGuard 同一策略。
 * 60°C 以上降质，80°C 以上自动切磨砂；默认不开启自动切换。
 * Android 通过电池温度估算机身温度（系统不向普通应用开放 CPU 温度）。
 */
public class ThermalGuard {

    public interface Listener {
        void onLevel(String level);   // full | reduced | frosted
    }

    public boolean enabled = false;
    public float hotC = 60f;
    public float switchC = 80f;
    public float temperature = Float.NaN;
    public String level = "full";

    private Listener listener;
    private final Context context;

    public ThermalGuard(Context context) {
        this.context = context.getApplicationContext();
    }

    public void setListener(Listener listener) {
        this.listener = listener;
    }

    public float readTemperature() {
        try {
            Intent intent = context.registerReceiver(null,
                    new IntentFilter(Intent.ACTION_BATTERY_CHANGED));
            if (intent == null) return Float.NaN;
            int tenths = intent.getIntExtra(BatteryManager.EXTRA_TEMPERATURE, -1);
            if (tenths < 0) return Float.NaN;
            return tenths / 10f;
        } catch (Exception e) {
            return Float.NaN;
        }
    }

    public String poll() {
        if (!enabled) {
            setLevel("full");
            return level;
        }
        temperature = readTemperature();
        if (Float.isNaN(temperature)) {
            setLevel("full");
        } else if (temperature >= switchC) {
            setLevel("frosted");
        } else if (temperature >= hotC) {
            setLevel("reduced");
        } else {
            setLevel("full");
        }
        return level;
    }

    private void setLevel(String next) {
        if (next.equals(level)) return;
        level = next;
        if (listener != null) listener.onLevel(next);
    }
}