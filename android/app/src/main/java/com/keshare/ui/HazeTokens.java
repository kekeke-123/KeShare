package com.keshare.ui;

/**
 * Haze —— KeShare 的统一磨砂视觉语言（Android 实现）。
 *
 * <p>"Haze" 指磨砂玻璃：半透明底色 + 背景模糊 + 颗粒噪点 + 圆角 + 1px 描边 + 顶部柔光。
 * 不含折射、不含色散 —— 那套（液态玻璃）已从工程移除。
 *
 * <p>令牌数值与 Windows 端 haze.py、iOS 端 KSHaze.m 保持一致，改这里即三端同改。
 */
public final class HazeTokens {

    public static final String HAZE_VERSION = "1.0";
    public static final String HAZE_NAME = "Haze";

    // ---- 颜色 ----
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

    /** Haze 面板底色（与 Windows haze.py 的 tint 相同）。 */
    public static final int TINT = 0xFF12182A;

    // ---- 度量 ----
    public static final float RADIUS_PANEL_DP = 22f;
    public static final float RADIUS_PILL_DP = 34f;
    public static final float BLUR_RADIUS_DP = 12f;
    public static final float ALPHA = 0.62f;         // 底色权重
    public static final float NOISE = 1.8f;          // 颗粒噪点
    public static final float HIGHLIGHT = 0.20f;     // 顶部柔光
    public static final float BORDER = 0.16f;        // 描边不透明度

    private HazeTokens() {}

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