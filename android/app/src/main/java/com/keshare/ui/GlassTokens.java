package com.keshare.ui;

/** 视觉令牌：与 Windows 端 glass.py 保持同一套色板与尺度。 */
public final class GlassTokens {
    public static final int BG = 0xFF0B0F1A;
    public static final int BG2 = 0xFF0E1422;
    public static final int PANEL = 0xFF151B2B;
    public static final int FIELD = 0xFF1E2637;
    public static final int STROKE = 0xFF2A3550;
    public static final int FG = 0xFFF2F5FA;
    public static final int MUTED = 0xFF8A94AC;
    public static final int ACCENT = 0xFF5CB8FF;
    public static final int ACCENT2 = 0xFFA06BFF;
    public static final int OK = 0xFF4ADE80;
    public static final int WARN = 0xFFFBBF24;
    public static final int ERR = 0xFFF87171;

    public static final float RADIUS_DP = 18f;
    public static final float PAD_DP = 14f;

    private GlassTokens() {}

    public static int mix(int c1, int c2, float t) {
        int a1 = (c1 >>> 24) & 0xFF, r1 = (c1 >> 16) & 0xFF, g1 = (c1 >> 8) & 0xFF, b1 = c1 & 0xFF;
        int a2 = (c2 >>> 24) & 0xFF, r2 = (c2 >> 16) & 0xFF, g2 = (c2 >> 8) & 0xFF, b2 = c2 & 0xFF;
        int a = (int) (a1 + (a2 - a1) * t);
        int r = (int) (r1 + (r2 - r1) * t);
        int g = (int) (g1 + (g2 - g1) * t);
        int b = (int) (b1 + (b2 - b1) * t);
        return (a << 24) | (r << 16) | (g << 8) | b;
    }

    public static int withAlpha(int color, float alpha) {
        int a = (int) (255 * Math.max(0f, Math.min(1f, alpha)));
        return (a << 24) | (color & 0x00FFFFFF);
    }
}