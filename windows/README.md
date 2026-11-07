# KeShare for Windows 1.0

局域网文件互传的 Windows 客户端。纯 Python 3（目标 3.8+，同时兼容 3.6/3.7 语法），
只使用标准库 + tkinter，无第三方依赖。

- 版本：`1.0.0`
- 端口：TCP/UDP **47377**
- 完整性：**SHA-KE-1024**（128 字节 / 256 位十六进制；**不加密**，明文传输，仅校验）

## 运行

```bat
python main.pyw                :: 图形界面
python main.pyw --version      :: 打印版本
python main.pyw --debug        :: 打开详细日志
python main.pyw --no-ui        :: 只跑服务（做文件服务器用，Ctrl+C 退出）
```

也可以把 `main.pyw` 复制成 `main.py` 后 `python main.py` 运行（两者内容一致）。

首次运行会生成 `%APPDATA%\KeShare\settings.json`（UTF-8、缩进 2、键名排序），
并把共享目录设为 `%USERPROFILE%\KeShare`。设置里可以改设备名、共享目录、端口、
限速、分块大小、发现方式、主题等，点“保存”立即生效（改端口会重新绑定监听）。

## 界面

| 标签页 | 说明 |
|---|---|
| 附近设备 | UDP 探测/mDNS 发现的设备列表，“连接”建立控制通道，双击也可连接 |
| 我的文件 | 本地共享目录浏览；“浏览远端…”用 `list` 浏览对端目录（双击下载），也可“推送到设备”或“下载远端文件…”手输路径 |
| 传输记录 | 每个传输一行，带进度条；失败原因（`checksum` 等）显示在行尾 |
| 设置 | 设备/目录/限速/端口/发现方式/主题 + 版本号（8 连击解锁开发者模式） |
| 开发者 | 解锁后出现：导出日志/配置/调试参数，详细日志、协议调试帧、日志条数 |

窗口顶部状态栏固定显示：

```
控制通道: 蓝牙 / WiFi 回退        数据通道: WiFi        完整性: SHA-KE-1024
```

## 协议概要

两个通道，同一个端口 47377：

**CTL（控制通道）**：JSON 消息，4 字节大端长度 + UTF-8 JSON，单帧 ≤ 4096 字节。
优先走蓝牙 RFCOMM（`socket.AF_BTH` / `BTHPROTO_RFCOMM`）；Python 构建或主机不支持时，
同样的字节走 WiFi TCP，界面显示“控制通道: WiFi 回退”。WiFi 回退时连接建立后先写一行
`CTL <token>\n`（握手前用 `-` 占位），随后才是长度前缀帧；服务端同时接受
`DATA ` / `CTL ` / 直接长度帧三种开头。

```
A -> B  hello      {t,v,did,name,kind,osv,port,caps,nonce}
B -> A  hello_ack  {...,token}   token = hex(sha256("KS1-CTL\0"+did_A+nonce_A+did_B+nonce_B))[:16]
A -> B  list       {token,path}             # 浏览对端共享根目录
B -> A  list_resp  {ok,files:[{path,name,size,mtime,kind}]}
A -> B  stat       {token,path}
B -> A  stat_resp  {ok,path,size,mtime,hash}  # hash = SHA-KE-1024
A -> B  get        {token,id,path}          # A 想下载 B 的文件（A 是接收方）
B -> A  get_resp   {id,ok,size,name}
        # A 主动连 B:47377 发 "DATA <token> <id>\n"，B 写数据帧，A 读
B -> A  hash       {id,hash,size}           # hash = SHA-KE-1024，256 hex
A -> B  hash_resp  {id,ok}                  # 失败时 ok:false,err:"checksum"，文件被删除

推送（A -> B，B 是接收方）：
A -> B  put_offer  {token,id,name,size}
B -> A  put_resp   {id,ok}
        # B 主动连 A:47377 发 "DATA <token> <id>\n"，A 写数据帧，B 读
A -> B  hash       {id,hash,size}
B -> A  hash_resp  {id,ok}

A -> B  bye        {}
```

**DATA（数据通道）**：只传文件字节。连接首行 `DATA <token> <id>\n`，随后是分块帧：

```
seq:u32 | offset:u64 | len:u32 | payload        (大端；末帧 len=0，offset=文件总长)
```

**接收方永远主动连接发送方**（PROTOCOL.md §4.0）：下载时请求方连文件方，推送时接收方
连推送方，两端实现对称、不需要端口映射。接收端只在 `hash_resp.ok == true` 后把 `.part`
改名成最终文件；摘要不符则删除并回 `checksum`。

监听端根据前 4 个字节区分连接类型：`b"DATA"` 是数据通道，`b"CTL "` 是先导行 +
长度帧，其余按 4 字节长度前缀读取 CTL（限制 ≤ 4096，不会与 `"DATA"` 混淆）。

发现：UDP 广播探测（每 5 s，15 s 未刷新标灰）+ 最小化 mDNS（`_keshare._tcp.local`，
224.0.0.251:5353）；mDNS 失败只记日志，UDP 探测继续工作。

## SHA-KE-1024

KeShare 自有的 1024 位摘要（`keshare/ke1024.py`，移植自 `protocol/sha_ke_1024.py`）：

- 状态 16×64 bit，块 128 字节，16 轮 BLAKE2b 风格 G（旋转 32/24/16/63），标准差 sigma 表
- 计数器异或进 `v[12]`；仅最后一轮 `v[14] ^= v[15] ^= 0xFFFF_FFFF_FFFF_FFFF`
- 喂入 `h[i] ^= v[i] ^ v[(i+8) % 16]`
- 填充 `0x80` + 0 + 128 位大端**比特**长度
- 摘要 128 字节 = **256 位小写十六进制**

会话 token 仍用 SHA-256（协议 §2），与文件摘要无关。

## 开发者模式

设置页底部点版本号 **8 次**（3 秒不点会清零，第 4 次起提示“再点 N 次进入开发者模式”），
写入 `developer.unlocked = true` 并出现“开发者”标签页：

- 导出日志 (TXT)、导出配置 (JSON)、导出调试参数 (JSON：pid、Python 版本、平台、cwd、
  配置路径、`integrity: SHA-KE-1024`、本机 IP、打开的套接字数、线程列表)
- 开关：详细日志、显示协议调试帧、日志条数

## 打包

```powershell
.\build.ps1            # 有 PyInstaller 时生成 dist\KeShare.exe（单文件、无控制台）
.\build.ps1 -Console   # 带控制台的版本，便于看 traceback
.\build.ps1 -Python C:\Python38\python.exe
```

没有安装 PyInstaller 时脚本只打印安装说明并 **正常退出（exit 0）**，不会让构建失败。

## 测试

```bat
python -m unittest discover -s D:\KeShare\windows\tests -v
```

覆盖：SHA-KE-1024 四组官方向量 + 流式/一次性一致（1/127/128/129/1000 字节分块）、
CTL 帧往返/拆包/粘包/超长拒绝、token 推导、`CTL <token>\n` 先导行的完整会话
（hello → hello_ack → list 同一条 socket）、分块帧往返与 300 KB 文件经环回 TCP 重组、
SHA-KE-1024 损坏检测（翻转一个字节 → `checksum`，文件删除）、配置默认值/往返/未知键保留、
路径沙箱（`..`、绝对路径、`CON`/`NUL` 等）、`list`/`stat` 正常路径与越权拒绝、
完整的 server+client 环回下载与推送，以及 §4.0 方向断言（下载由请求方连接、推送由接收方连接）。

## 已知限制

- 蓝牙控制通道是尽力而为：PyInstaller 打包或某些 Python 构建没有 `AF_BTH`；标准库也
  无法注册 SDP 服务记录，所以固定使用 RFCOMM 通道 3。不可用时自动使用 WiFi 回退。
- 协议没有加密（按 v1 规范），请只在可信局域网使用。
- `clip`/`progress` 消息尚未实现（收到时回 `{"t":"err","err":"unsupported"}`）；
  `list`/`stat` 已实现，界面提供“浏览远端…”窗口。
