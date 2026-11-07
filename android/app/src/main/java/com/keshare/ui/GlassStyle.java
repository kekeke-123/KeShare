package com.keshare.ui;

/** 玻璃风格与画质档位。 */
public final class GlassStyle {
    public enum Kind { LIQUID, FROSTED }
    public enum Quality { FULL, REDUCED, FROSTED }

    public Kind kind = Kind.LIQUID;
    public Quality quality = Quality.FULL;

    public static GlassStyle liquid() {
        GlassStyle s = new GlassStyle();
        s.kind = Kind.LIQUID;
        return s;
    }

    public static GlassStyle frosted() {
        GlassStyle s = new GlassStyle();
        s.kind = Kind.FROSTED;
        return s;
    }

    private GlassStyle() {}
}