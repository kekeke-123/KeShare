package com.keshare.core;

import android.util.Log;

import org.json.JSONObject;

import java.io.DataInputStream;
import java.io.IOException;
import java.io.OutputStream;
import java.net.Socket;
import java.nio.charset.StandardCharsets;

/** 控制通道：CTL 帧收发 + 握手。 */
public class CtlSession {

    public interface Listener {
        void onMessage(JSONObject message);
        void onClosed(String reason);
    }

    private static final String TAG = "KsCtl";
    private final Socket socket;
    private final DataInputStream in;
    private final OutputStream out;
    private final Listener listener;
    private volatile boolean running = true;
    private Thread reader;

    public String token = "";
    public JSONObject peerInfo;

    public CtlSession(Socket socket, Listener listener) throws IOException {
        this.socket = socket;
        this.listener = listener;
        this.in = new DataInputStream(socket.getInputStream());
        this.out = socket.getOutputStream();
        socket.setTcpNoDelay(true);
    }

    public void start() {
        reader = new Thread(this::readLoop, "ks-ctl-read");
        reader.setDaemon(true);
        reader.start();
    }

    /** 发起方：先写 "CTL -\n" 前导行。 */
    public void writePreamble(String tokenOrDash) throws IOException {
        out.write(("CTL " + tokenOrDash + "\n").getBytes(StandardCharsets.US_ASCII));
        out.flush();
    }

    public synchronized void send(JSONObject message) throws IOException {
        byte[] json = message.toString().getBytes(StandardCharsets.UTF_8);
        out.write(Protocol.encodeCtl(json));
        out.flush();
    }

    private void readLoop() {
        try {
            while (running) {
                byte[] header = new byte[4];
                in.readFully(header);
                int length = Protocol.ctlLength(header);
                if (length <= 0 || length > 1 << 20) {
                    throw new IOException("CTL 长度异常: " + length);
                }
                byte[] body = new byte[length];
                in.readFully(body);
                JSONObject message = new JSONObject(new String(body, StandardCharsets.UTF_8));
                if ("by".equals(message.optString("t"))) break;
                listener.onMessage(message);
            }
        } catch (Exception e) {
            if (running) Log.w(TAG, "控制通道断开: " + e.getMessage());
        } finally {
            close("closed");
        }
    }

    /** 发起方握手：hello → 等 hello_ack → 计算 token。 */
    public JSONObject handshakeAsInitiator(JSONObject hello) throws Exception {
        writePreamble("-");
        send(hello);
        byte[] header = new byte[4];
        in.readFully(header);
        int length = Protocol.ctlLength(header);
        byte[] body = new byte[length];
        in.readFully(body);
        JSONObject ack = new JSONObject(new String(body, StandardCharsets.UTF_8));
        if (!"hello_ack".equals(ack.optString("t"))) {
            throw new IOException("期望 hello_ack，收到 " + ack.optString("t"));
        }
        token = Token.derive(hello.optString("did"), hello.optString("nonce"),
                             ack.optString("did"), ack.optString("nonce"));
        if (!token.equals(ack.optString("token", token))) {
            throw new IOException("token 不一致");
        }
        peerInfo = ack;
        return ack;
    }

    public void close(String reason) {
        running = false;
        try { socket.close(); } catch (IOException ignored) { }
        if (listener != null) listener.onClosed(reason);
    }
}