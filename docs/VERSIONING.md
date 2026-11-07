# KeShare 版本与发布节奏

## 版本号规则

单一版本源：仓库根目录 [`version.txt`](../version.txt)，格式 `<主>.<次>.<修订>-<阶段>.<序号>`。

| 阶段 | 含义 | 示例 |
|---|---|---|
| `alpha.N` | 内测，功能未冻结，可能天天变 | `0.0.1-alpha.7` |
| `beta.N` | 公测，功能冻结，只修 bug | `0.0.1-beta.1` |
| `rc.N` | 候选发布，只允许修致命问题 | `0.0.1-rc.0` |
| 无后缀 | 正式发布 | `0.0.1` |

展示文案（UI/包内）统一为：`0.0.1 beta1`（阶段小写、序号紧随其后，无点号）。

## 0.0.1 的节奏

| 里程碑 | 内容 | 状态 |
|---|---|---|
| alpha.1–alpha.N | 功能开发期（BLE 发现、USB 隧道、热点、SHA-256、界面） | 已归档为内测，不对外 |
| **beta1** | 首次公测：连接链路可用性 + 已知 bug 修复 | **当前** |
| beta2 | 传输稳定性：断点、超时、重试、进度 | 计划 |
| beta3 | 体验与性能：界面重做、并发、大文件 | 计划 |
| beta4 | 收尾：日志、诊断、异常路径全覆盖 | 计划 |
| rc.0 | 候选版，只修致命问题 | 计划 |

## 各端如何读版本

* Windows：`keshare/__init__.py` 里 `KSAPP_VERSION` + `KSAPP_STAGE`，`KSAPP_DISPLAY` 由两者拼出。
* iOS：`ios/KeShare.h` 的 `KS_VERSION_STRING`（展示用）与 `ios/Info.plist` 的
  `CFBundleShortVersionString`（纯数字，如 `0.0.1`）+ `CFBundleVersion`（递增构建号）。
* CI：`.github/workflows/ios.yml` 里的 `VERSION=` 必须与 `version.txt` 的主干一致。

## 发版检查单

1. `version.txt` 已更新
2. 两端显示版本一致（Windows 关于页 / iOS 设置页）
3. 单元测试全绿；端到端脚本通过
4. IPA 无 `PlugIns`，`MinimumOSVersion` 与预期一致
5. `PROTOCOL.md` 与实现无偏差
