package com.keshare.core;

import java.nio.charset.StandardCharsets;

/** token = hex(SHA-256("KS1-CTL\0" + didA + nonceA + didB + nonceB))[:16] */
public final class Token {
    private Token() {}

    public static String derive(String didA, String nonceA, String didB, String nonceB) {
        StringBuilder sb = new StringBuilder(Protocol.CTL_LABEL);
        sb.append(didA == null ? "" : didA);
        sb.append(nonceA == null ? "" : nonceA);
        sb.append(didB == null ? "" : didB);
        sb.append(nonceB == null ? "" : nonceB);
        return Hashing.sha256Hex(sb.toString().getBytes(StandardCharsets.UTF_8)).substring(0, 16);
    }
}