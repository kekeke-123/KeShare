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
 * 玻璃面板：液态 / 磨砂，圆角 + 渐变 + 顶部高光 + 磨砂噪点。
 * 与 Windows 端 GlassPanel 绘制策略一致（自绘，不用系统模糊）。
 */
public class GlassPanel extends View {

    private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Path path = new Path();
    private final RectF rect = new RectF();
    private final float[] noise;

    private GlassStyle style = GlassStyle.liquid();

    public GlassPanel(Context context) {
        this(context, null);
    }

    public GlassPanel(Context context, AttributeSet attrs) {
        super(context, attrs);
        setLayerType(LAYER_TYPE_SOFTWARE, null);
        noise = new float[240];
        java.util.Random rng = new java.util.Random(20261107L);
        for (int i = 0; i < noise.length; i++) noise[i] = rng.nextFloat();
    }

    public void setGlassStyle(GlassStyle style) {
        this.style = style;
        invalidate();
    }

    @Override
    protected void onDraw(Canvas canvas) {
        super.onDraw(canvas);
        float w = getWidth();
        float h = getHeight();
        float r = GlassTokens.RADIUS_DP * getResources().getDisplayMetrics().density;

        rect.set(1, 1, w - 1, h - 1);
        path.reset();
        path.addRoundRect(rect, r, r, Path.Direction.CW);

        int base = style.kind == GlassStyle.Kind.LIQUID
                ? GlassTokens.mix(GlassTokens.PANEL, GlassTokens.BG, 0.25f)
                : GlassTokens.mix(GlassTokens.PANEL, GlassTokens.BG, 0.55f);

        if (style.kind == GlassStyle.Kind.LIQUID) {
            paint.setShader(new LinearGradient(0, 0, 0, h,
                    new int[]{
                            GlassTokens.withAlpha(GlassTokens.mix(base, 0xFFFFFFFF, 0.18f), 0.92f),
                            GlassTokens.withAlpha(base, 0.86f),
                            GlassTokens.withAlpha(GlassTokens.mix(base, 0xFF000000, 0.25f), 0.90f)},
                    new float[]{0f, 0.55f, 1f}, Shader.TileMode.CLAMP));
            canvas.drawPath(path, paint);
            paint.setShader(null);

            // 内描边：模拟玻璃厚度
            paint.setStyle(Paint.Style.STROKE);
            paint.setStrokeWidth(1.2f);
            paint.setColor(GlassTokens.withAlpha(0xFFFFFFFF, 0.16f));
            canvas.drawPath(path, paint);
            paint.setStyle(Paint.Style.FILL);
        } else {
            paint.setShader(null);
            paint.setColor(GlassTokens.withAlpha(base, 0.94f));
            canvas.drawPath(path, paint);

            paint.setStyle(Paint.Style.STROKE);
            paint.setStrokeWidth(1f);
            paint.setColor(GlassTokens.withAlpha(GlassTokens.STROKE, 0.9f));
            canvas.drawPath(path, paint);
            paint.setStyle(Paint.Style.FILL);

            if (style.quality != GlassStyle.Quality.REDUCED) {
                paint.setColor(GlassTokens.withAlpha(0xFFFFFFFF, 0.05f));
                for (int i = 0; i + 1 < noise.length; i += 2) {
                    float x = 2 + noise[i] * (w - 4);
                    float y = 2 + noise[i + 1] * (h - 4);
                    canvas.drawCircle(x, y, 0.7f, paint);
                }
            }
        }
    }
}