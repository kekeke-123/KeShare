# KeShare

局域网多端文件互通。**蓝牙握手 → 蓝牙发请求 → WiFi 分块传文件 → 蓝牙回传哈希校验**。

- 不做应用层加密（依赖链路层 WPA2/WPA3 与 BLE 加密）
- 只做完整性校验，算法是自研的 **SHA-KE-1024**（1024 位 / 256 个十六进制字符）

```
A ──蓝牙(BLE)──> hello / hello_ack        握手，协商 token
A ──蓝牙(BLE)──> get / put_offer          发请求
A <──WiFi TCP──  seq|offset|len|payload   分块传文件（接收方主动连接）
A <──蓝牙(BLE)── hash(sha_ke_1024)        发送方声明摘要
A ──蓝牙(BLE)──> hash_resp                接收方校验后确认
```

协议全文： [`PROTOCOL.md`](PROTOCOL.md)

---

## 目录结构

```
KeShare/
├─ PROTOCOL.md                 协议规范（v1）
├─ protocol/
│  ├─ ks_ke1024.c / .h         SHA-KE-1024 的 C 实现（无依赖，可直接编进任何端）
│  ├─ sha_ke_1024.py           Python 参考实现（与 C 逐位一致）
│  └─ test/ke_selftest.c       自检（CI 第一步跑）
├─ ios/                        iOS 客户端（arm64，iOS 16.1+）
│  ├─ KeShare.h                公共接口
│  ├─ KSApp.m                  启动、液态玻璃外观、标签栏、动态背景
│  ├─ KSInternal.h/.m          内部共享符号
│  ├─ KSStore.m                settings.json、日志、路径白名单、文件哈希
│  ├─ KSNet.m                  BLE 控制链路
│  ├─ KSNetData.m              WiFi 数据通道（监听 47377、校验落地）
│  ├─ KSSession.m              会话状态机、WiFi 回退链路、数据接收端
│  ├─ KSUI.m                   设备 / 文件 / 传输 / 设置 / 开发者 页面
│  ├─ KSActivityManager.swift  实时活动（灵动岛）
│  ├─ KSActivityShim.m         ObjC → Swift 转发
│  ├─ Widget/                  灵动岛扩展（四种形态 + 锁屏横幅）
│  ├─ Info.plist               蓝牙 / 本地网络 / Bonjour / 实时活动权限
│  └─ Makefile                 clang + swiftc 直接构建
├─ windows/                    Windows 客户端（Python 3.8+，标准库）
│  ├─ main.pyw                 入口
│  ├─ keshare/                 协议、配置、日志、发现、服务端、客户端、UI
│  ├─ tests/                   41 个 unittest
│  └─ build.ps1                PyInstaller 打包
└─ .github/workflows/ios.yml   GitHub 云端编译 iOS
```

---

## SHA-KE-1024

自研 1024 位摘要，两端逐位一致（`protocol/ks_ke1024.c` 与 `keshare/ke1024.py`）。

| 项 | 取值 |
|---|---|
| 状态 | 16 道 × 64 位 = 1024 位 |
| 分组 | 128 字节（16 个 64 位字） |
| 轮数 | 16 |
| 轮函数 | BLAKE2b 风格 G，旋转 32 / 24 / 16 / 63 |
| 消息选择 | 标准 BLAKE2 sigma 表 |
| 前馈 | `h[i] ^= v[i] ^ v[(i+8) % 16]` |
| 填充 | `0x80` + 0，至 `len % 128 == 112`，再补 128 位大端比特长度 |
| 输出 | 128 字节 / 256 个十六进制字符 |

测试向量（两端都必须一致）：

```
""                      c4c3cc4ab16aff9d0ac4802468fd7dfac2785133349d14a4a4b74b3bb3c591a3…
"abc"                   19e537f5fabe26188455cfc2c5aadca000122fbeb2fa4dd6a9145f790871a24a…
"KeShare protocol v1"   05d719ae6e3682fe3c7df34628f804f29d1d6198bd3d4dcea94ef08a688602e7…
768 字节 0..255 循环      0438a990feafb49147b4bc5d1546cc5ca405da5d57f462f1d5e4209d50e636e…
```

自检：

```bash
cd protocol
clang -O2 -std=c99 -I. -o ke_test test/ke_selftest.c ks_ke1024.c && ./ke_test
```

---

## Windows 端

```powershell
python D:\KeShare\windows\main.pyw           # 运行
python D:\KeShare\windows\main.pyw --debug   # 详细日志
```

- 设置文件：`%APPDATA%\KeShare\settings.json`
- 分享目录默认 `%USERPROFILE%\KeShare`，收到的文件进 `Received\`
- **8 次点击版本号**进入开发者模式：可导出日志 TXT / 配置 JSON / 调试参数 JSON
- 测试：`python -m unittest discover -s D:\KeShare\windows\tests -v`（41 项）

打包单文件 exe（需要 PyInstaller，没有则只打印说明）：

```powershell
powershell -ExecutionPolicy Bypass -File D:\KeShare\windows\build.ps1
```

## iOS 端

必须用 macOS + Xcode 命令行工具构建（Windows 上无法编译 iOS）。

```bash
cd ios
make            # 构建 build/KeShare.app（含 PlugIns/KeShareWidget.appex）
make ipa        # 打包 build/KeShare.ipa
```

手动安装到越狱设备：`scp KeShare.ipa` 后用 Filza 安装，或

```bash
unzip -o KeShare.ipa -d /tmp/ks
scp -r /tmp/ks/Payload/KeShare.app root@<device>:/Applications/
ssh root@<device> "uicache -a"
```

### 用 GitHub 编译（推荐）

仓库推到 GitHub 后，`.github/workflows/ios.yml` 会自动：

1. 跑 SHA-KE-1024 自检
2. `make app` 编译 arm64（`-target arm64-apple-ios16.1`）
3. `lipo -info` 校验架构、`otool -l` 校验最低系统
4. `make ipa` 打包
5. 上传 artifact，打 tag 时自动建 Release

触发方式：push 改动到 `ios/**` 或 `protocol/**`，或在 Actions 页面手动 `Run workflow`。

---

## 联调

1. 两台设备连同一个 WiFi
2. 都打开 KeShare，确认状态栏显示 `控制通道: 蓝牙` 或 `WiFi 回退`、`数据通道: WiFi`、`完整性: SHA-KE-1024`
3. 在「附近设备」里点击对端 → 握手 → 自动请求文件列表
4. 传输页会实时显示进度；iOS 端同时会在灵动岛/锁屏显示进度
5. 传完后接收端用 SHA-KE-1024 校验，不一致会删除文件并标记「校验失败」

排障：设置里连点版本号 8 次 → 开发者选项 → 导出日志 TXT。

---

## 版本

- 客户端 1.0.0，协议 v1
- iOS 最低 16.1（实时活动需要），Windows Vista+（32/64 位 Python 均可）
