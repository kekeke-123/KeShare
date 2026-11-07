# KeShare 协议 v1

> 局域网多端文件互通。**控制走蓝牙，数据走 WiFi，只做完整性校验，不做加密。**

- 版本：v1.0（冻结）
- 端口：TCP **47377**（数据通道）；UDP 47377（发现兜底，可选）
- 摘要：SHA-256（十六进制小写）
- 编码：JSON UTF-8；整数为大端

---

## 1. 两个通道

| 通道 | 用途 | 传输 |
|---|---|---|
| **CTL（控制）** | 握手、请求、状态、最终哈希 | 蓝牙（iOS 用 BLE GATT；Windows 用 RFCOMM）。不可用时回退到 WiFi 同一端口，帧格式相同 |
| **DATA（数据）** | 文件字节 | WiFi TCP 47377 |

### 1.1 CTL 帧

```
4 字节大端长度 N + N 字节 UTF-8 JSON
```

单帧 ≤ 4096 字节（BLE 单包可分片，见 §5）。

### 1.2 DATA 帧

TCP 连接建立后先发一行 ASCII：

```
DATA <token> <id>\n
```

随后是数据帧：

```
seq     u32   帧序号，从 0 开始
offset  u64   本帧数据在文件中的偏移
len     u32   本帧负载长度（最后一帧为 0）
payload len 字节
```

当 `len == 0` 时表示数据发送完毕，`offset` 必须等于文件总长度。
接收方按 `seq` 顺序校验，`offset` 必须连续，否则断开并报 `bad_order`。

---

## 2. 会话令牌

控制会话与数据连接靠 token 绑定（**仅用于防串线，不是加密**）：

```
token = hex( SHA-256( "KS1-CTL\0" + did_A + nonce_A + did_B + nonce_B ) )[:16]
```

- `did`：设备 ID，16 位十六进制，首次启动生成并写入配置文件
- `nonce`：16 字节随机数，十六进制 32 字符
- A = 发起方（点击"连接"的一端），B = 应答方

数据连接必须带正确 token，否则服务端立刻断开（`403`）。

---

## 3. 消息

所有消息都必须带 `"t"` 字段。未知 `t` 忽略并回 `{"t":"err","err":"unsupported"}`。

| 方向 | 消息 | 字段 |
|---|---|---|
| A→B | `hello` | `v,did,name,kind,osv,port,caps,nonce` |
| B→A | `hello_ack` | `v,did,name,kind,osv,port,caps,nonce,token` |
| A→B | `list` | `token,path`(可省) |
| B→A | `list_resp` | `ok,files:[{path,name,size,mtime,kind}]` |
| A→B | `stat` | `token,path` |
| B→A | `stat_resp` | `ok,path,size,mtime,sha256`(可省) |
| A→B | `get` | `token,id,path`（下载：A 拉 B 的文件） |
| B→A | `get_resp` | `ok,id,size,name` / `ok:false,err` |
| A→B | `put_offer` | `token,id,name,size`（A 推文件给 B） |
| B→A | `put_resp` | `ok,id` / `ok:false,err` |
| A→B | `hash` | `id,sha256,size` |
| B→A | `hash_resp` | `ok,id` / `ok:false,id,err:"checksum"` |
| 双向 | `clip` | `mode:"get"\|"set",text` |
| 双向 | `progress` | `id,dir:"up"\|"down",done,total` |
| 双向 | `bye` | — |
| 双向 | `err` | `err,msg` |

`err` 取值：`badreq | notfound | denied | bad_order | checksum | too_large | io | unsupported | bad_token`。

---

## 4. 标准流程

### 4.0 数据连接方向（重要）

**接收方主动连接发送方**，发送方在自己的 47377 端口等待并写出数据帧。

- 下载（A 拉 B 的文件）：A 是接收方 → A 连 B:47377 并读取
- 上传（A 推给 B）：B 是接收方 → B 连 A:47377 并读取

这样两端实现对称，也不需要任何端口映射。

### 4.1 下载（A 拉取 B 的文件）

```
A  --hello-------------------->  B      （CTL / 蓝牙）
A  <--------------------hello_ack--  B   （含 token）
A  --get{id,path}------------->  B
A  <--------------------get_resp--  B   （含 size）
A  ==TCP 47377 (A 连接 B)==>  首行 "DATA <token> <id>\n"，随后 B 写数据帧，A 读
A  <--hash{id,sha_ke_1024,size}--  B     （B 传完后在 CTL 上发摘要）
A  --hash_resp{ok}----------->  B
```

### 4.2 上传（A 推文件给 B）

```
A  --hello / hello_ack-------------->  B
A  --put_offer{id,name,size}-------->  B
A  <--------------------put_resp----  B   （ok:true 才允许发送）
A  <==TCP 47377 (B 连接 A)==  首行 "DATA <token> <id>\n"，A 写数据帧，B 读
A  --hash{id,sha_ke_1024,size}------>  B
A  <--------------------hash_resp---  B
```

**接收端只在 `hash_resp.ok == true` 后把文件改名到最终路径**；不匹配则删除临时文件并回 `ok:false,err:"checksum"`。

---

## 5. 蓝牙（CTL）实现说明

### 5.1 iOS
- BLE GATT 自定义服务：`5908C1A1-7F4E-4B2A-9C37-0B7C1B3B0001`
  - 特征 `…0002`（**RX**，写入，无应答可分片）
  - 特征 `…0003`（**TX**，通知）
- 一帧 CTL JSON 超过 180 字节时按 180 字节分片，分片头 3 字节：`idx:u16 | more:u8`，接收方按序重组。
- iOS 同时可作 peripheral（广播 + GATT 服务）与 central（扫描 + 连接）。

### 5.2 Windows
- 优先 RFCOMM（`socket.AF_BTH`，服务名 `KeShare`）。
- 若系统/Build 不支持则以 WiFi 回退（同端口同帧格式），UI 明示"控制通道: WiFi 回退"。

### 5.3 蓝牙不可用时的回退
两端都支持在 WiFi 上承载 CTL：连接 47377 后发送 `CTL <token>\n`，随后是 §1.1 的 4 字节长度帧。iOS 端在 BLE 不可用（模拟器/老系统）时自动回退。

---

## 6. 发现

1. BLE 广播本地名 `KeShare`（iOS `CBPeripheralManager`；Windows 可选）。
2. WiFi 兜底：UDP 47377 广播
   `{"ks":1,"t":"probe","did":..,"name":..,"port":47377,"caps":..}`
   应答 `{"ks":1,"t":"announce","did":..,"name":..,"kind":..,"osv":..,"port":47377,"caps":..}`
3. 收到 announce 即出现在"附近设备"。

---

## 7. 安全边界（明确不做的事）

- **不加密**传输内容；同一局域网内任何人抓到 TCP 流都能看到文件内容。
- 仅保证**完整性**：SHA-256 由发送方在 CTL 上声明，接收方核对；同时校验 token 防止串线。
- 路径白名单：只允许访问共享根目录内的文件；拒绝 `..`、绝对路径、Windows 保留名（`CON/NUL/AUX/PRN/COM1-9/LPT1-9`）、控制字符、超长路径。
- 文件名长度 ≤ 255 字节；总路径 ≤ 1024 字节。
