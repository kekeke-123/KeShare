package com.keshare.ui;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.LinearGradient;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.RectF;
import android.graphics.Shader;
import android.util.AttributeSet;
import android.view.View;

/**
 * Haze 面板：磨砂玻璃（圆角 + 半透明渐变 + 颗粒噪点 + 描边 + 顶部柔光）。
 *
 * <p>与 Windows 端 haze.py 的 haze_layer()、iOS 端 KSHaze 保持同一套观感：
 * 不做折射，只做"雾面"。
 */
public class HazePanel extends View {

    private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Path path = new Path();
    private final RectF rect = new RectF();
    private final float[] noise;

    private float radiusDp = HazeTokens.RADIUS_PANEL_DP;
    private float alpha = HazeTokens.ALPHA;
    private float noiseStrength = HazeTokens.NOISE;
    private float highlight = HazeTokens.HIGHLIGHT;
    private float border = HazeTokens.BORDER;
    private int tint = HazeTokens.TINT;

    public HazePanel(Context context) {
        this(context, null);
    }

    public HazePanel(Context context, AttributeSet attrs) {
        super(context, attrs);
        setLayerType(LAYER_TYPE_SOFTWARE, null);
        noise = new float[320];
        java.util.Random rng = new java.util.Random(20261107L);
        for (int i = 0; i < noise.length; i++) noise[i] = rng.nextFloat();
    }

    /** 圆角（dp）。 */
    public void setRadiusDp(float dp) {
        radiusDp = dp;
        invalidate();
    }

    /** 底色权重：0 全透，1 实心。 */
    public void setAlpha(float value) {
        alpha = Math.max(0f, Math.min(1f, value));
        invalidate();
    }

    public void setTint(int color) {
        tint = color;
        invalidate();
    }

    /** 颗粒强度（0 关闭，画质降级时可调低）。 */
    public void setNoiseStrength(float value) {
        noiseStrength = Math.max(0f, value);
        invalidate();
    }

    @Override
    protected void onDraw(Canvas canvas) {
        super.onDraw(canvas);
        float w = getWidth();
        float h = getHeight();
        if (w <= 0 || h <= 0) return;

        float density = getResources().getDisplayMetrics().density;
        float r = Math.min(radiusDp * density, Math.min(w, h) / 2f);

        rect.set(1, 1, w - 1, h - 1);
        path.reset();
        path.addRoundRect(rect, r, r, Path.Direction.CW);

        // 1) 半透明底色：上浅下深的极缓渐变，模拟环境光衰减
        int top = HazeTokens.withAlpha(HazeTokens.mix(tint, 0xFFFFFFFF, 0.06f), alpha);
        int bottom = HazeTokens.withAlpha(HazeTokens.mix(tint, 0xFF000000, 0.12f), alpha);
        paint.setShader(new LinearGradient(0, 0, 0, h, top, bottom, Shader.TileMode.CLAMP));
        canvas.drawPath(path, paint);
        paint.setShader(null);

        // 2) 颗粒噪点（磨砂质感）
        if (noiseStrength > 0.05f) {
            paint.setColor(HazeTokens.withAlpha(0xFFFFFFFF, 0.035f * noiseStrength));
            for (int i = 0; i + 1 < noise.length; i += 2) {
                float x = 2 + noise[i] * (w - 4);
                float y = 2 + noise[i + 1] * (h - 4);
                canvas.drawCircle(x, y, 0.7f, paint);
            }
        }

        // 3) 顶部柔光
        if (highlight > 0.01f) {
            int glow = HazeTokens.withAlpha(0xFFFFFFFF, highlight * 0.16f);
            paint.setShader(new LinearGradient(0, 0, 0, h * 0.42f,
                    glow, 0x00FFFFFF, Shader.TileMode.CLAMP));
            canvas.drawPath(path, paint);
            paint.setShader(null);
        }

        // 4) 1px 描边
        if (border > 0.01f) {
            paint.setStyle(Paint.Style.STROKE);
            paint.setStrokeWidth(Math.max(1f, density));
            paint.setColor(HazeTokens.withAlpha(0xFFFFFFFF, border));
            canvas.drawPath(path, paint);
            paint.setStyle(Paint.Style.FILL);
        }
    }
}