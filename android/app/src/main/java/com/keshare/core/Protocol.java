package com.keshare.core;

/** 协议常量与帧编解码，与 iOS / Windows 端逐字节一致。 */
public final class Protocol {
    public static final int PORT = 47377;
    public static final int VERSION = 1;
    public static final String CTL_LABEL = "KS1-CTL\0";
    public static final String SERVICE_UUID = "6B657368-6172-6542-4C45-000000000001";
    public static final String CHAR_RX_UUID = "6B657368-6172-6542-4C45-000000000002";
    public static final String CHAR_TX_UUID = "6B657368-6172-6542-4C45-000000000003";

    private Protocol() {}

    public static byte[] encodeCtl(byte[] json) {
        byte[] out = new byte[4 + json.length];
        putU32(out, 0, json.length);
        System.arraycopy(json, 0, out, 4, json.length);
        return out;
    }

    public static int ctlLength(byte[] h) {
        return (int) readU32(h, 0);
    }

    public static byte[] dataHeader(long seq, long offset, int len) {
        byte[] out = new byte[16];
        putU32(out, 0, seq);
        putU64(out, 4, offset);
        putU32(out, 12, len);
        return out;
    }

    public static void putU32(byte[] b, int p, long v) {
        b[p] = (byte) (v >>> 24); b[p + 1] = (byte) (v >>> 16);
        b[p + 2] = (byte) (v >>> 8); b[p + 3] = (byte) v;
    }

    public static void putU64(byte[] b, int p, long v) {
        putU32(b, p, v >>> 32); putU32(b, p + 4, v & 0xFFFFFFFFL);
    }

    public static long readU32(byte[] b, int p) {
        return ((b[p] & 0xFFL) << 24) | ((b[p + 1] & 0xFFL) << 16)
             | ((b[p + 2] & 0xFFL) << 8) | (b[p + 3] & 0xFFL);
    }

    public static long readU64(byte[] b, int p) {
        return (readU32(b, p) << 32) | readU32(b, p + 4);
    }
}