package com.keshare.core;

import android.util.Log;

import java.io.DataInputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.Socket;
import java.nio.charset.StandardCharsets;

/** 数据通道：按 16 字节帧头分块读写，支持进度回调与取消。 */
public class DataChannel {

    public interface Progress {
        void onBytes(long done, long total);
    }

    private static final String TAG = "KsData";
    private final Socket socket;
    private volatile boolean cancelled = false;

    public DataChannel(Socket socket) {
        this.socket = socket;
    }

    public void cancel() {
        cancelled = true;
        try { socket.close(); } catch (IOException ignored) { }
    }

    /** 接收方：写首行 "DATA <token> <id>\n" 后读帧写文件。 */
    public long receive(String token, String transferId, File dest, long expectSize, Progress progress)
            throws IOException {
        OutputStream out = socket.getOutputStream();
        out.write(("DATA " + token + " " + transferId + "\n").getBytes(StandardCharsets.US_ASCII));
        out.flush();

        DataInputStream in = new DataInputStream(socket.getInputStream());
        long done = 0;
        byte[] header = new byte[16];
        try (FileOutputStream fos = new FileOutputStream(dest)) {
            while (!cancelled) {
                in.readFully(header);
                int len = (int) Protocol.readU32(header, 12);
                if (len == 0) break;
                byte[] payload = new byte[len];
                in.readFully(payload);
                fos.write(payload);
                done += len;
                if (progress != null) progress.onBytes(done, expectSize);
            }
        }
        return done;
    }

    /** 发送方：写首行后按块写帧。 */
    public long send(String token, String transferId, File source, int chunkKB, Progress progress)
            throws IOException {
        InputStream fis = new java.io.FileInputStream(source);
        OutputStream out = socket.getOutputStream();
        out.write(("DATA " + token + " " + transferId + "\n").getBytes(StandardCharsets.US_ASCII));
        out.flush();

        long total = source.length();
        long offset = 0;
        long seq = 0;
        int chunk = Math.max(4, chunkKB) * 1024;
        byte[] buf = new byte[chunk];
        try {
            while (!cancelled) {
                int n = fis.read(buf);
                if (n <= 0) break;
                out.write(Protocol.dataHeader(seq++, offset, n));
                out.write(buf, 0, n);
                offset += n;
                if (progress != null) progress.onBytes(offset, total);
            }
            out.write(Protocol.dataHeader(seq, offset, 0));
            out.flush();
        } finally {
            fis.close();
        }
        Log.i(TAG, "发送完成 " + offset + " 字节");
        return offset;
    }
}