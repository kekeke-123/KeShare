# -*- coding: utf-8 -*-
"""Windows 蓝牙（经典 + BLE）与移动热点。

实现方式：用 PowerShell 调用 Win32 `bthprops.cpl` 与 WinRT API。
Python 标准库无法直接访问这些接口，而引入第三方包会破坏「无依赖」的约定，
因此这里统一走「生成临时 .ps1 → 执行 → 读回 JSON」的方式，可读可调试。

能力与限制：
  * 经典蓝牙搜索（BluetoothFindFirstDevice）—— 可用，需蓝牙适配器
  * BLE 扫描（BluetoothLEAdvertisementWatcher）—— 可用，Win10 1709+
  * 开/关移动热点（NetworkOperatorTetheringManager）—— 可用，**需要管理员权限**且网卡支持
  * 经典蓝牙 RFCOMM 握手 —— 需要对方固定信道；iOS 不开放 RFCOMM，故仅用于 Windows↔Windows
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile

_PS = ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File"]

CLASSIC_CS = r"""
using System;
using System.Runtime.InteropServices;
using System.Text;
public class KS_BthInq {
  [StructLayout(LayoutKind.Sequential)]
  public struct PARAMS { public int dwSize; public bool a,b,c,d,e; public byte t; public IntPtr h; }
  [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)]
  public struct SYSTEMTIME { public ushort wYear,wMonth,wDayOfWeek,wDay,wHour,wMinute,wSecond,wMs; }
  [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)]
  public struct INFO {
    public int dwSize; public ulong Address; public uint dwClass;
    public bool fConnected, fRemembered, fAuthenticated;
    public SYSTEMTIME stLastSeen, stLastUsed;
    [MarshalAs(UnmanagedType.ByValTStr, SizeConst=248)] public string szName;
  }
  [DllImport("bthprops.cpl", SetLastError=true)]
  public static extern IntPtr BluetoothFindFirstDevice(ref PARAMS p, ref INFO i);
  [DllImport("bthprops.cpl", SetLastError=true)]
  public static extern bool BluetoothFindNextDevice(IntPtr h, ref INFO i);
  [DllImport("bthprops.cpl", SetLastError=true)]
  public static extern bool BluetoothFindDeviceClose(IntPtr h);

  public static string Run() {
    var p = new PARAMS();
    p.dwSize = Marshal.SizeOf(p);
    p.a = true; p.b = true; p.c = true; p.d = true; p.e = true; p.t = 2; p.h = IntPtr.Zero;
    var i = new INFO(); i.dwSize = Marshal.SizeOf(i);
    var sb = new StringBuilder();
    IntPtr h = BluetoothFindFirstDevice(ref p, ref i);
    if (h == IntPtr.Zero) return "[]";
    int guard = 0;
    do {
      if (guard++ > 60) break;
      string mac = string.Format("{0:X2}:{1:X2}:{2:X2}:{3:X2}:{4:X2}:{5:X2}",
        (i.Address) & 0xff, (i.Address >> 8) & 0xff, (i.Address >> 16) & 0xff,
        (i.Address >> 24) & 0xff, (i.Address >> 32) & 0xff, (i.Address >> 40) & 0xff);
      string name = (i.szName ?? "").Trim().Replace("\"", "'");
      sb.Append("{");
      sb.AppendFormat("\"address\":\"{0}\",\"name\":\"{1}\",\"connected\":{2},\"remembered\":{3}",
        mac, name, i.fConnected ? "true" : "false", i.fRemembered ? "true" : "false");
      sb.Append("}\n");
      i.dwSize = Marshal.SizeOf(i);
    } while (BluetoothFindNextDevice(h, ref i));
    BluetoothFindDeviceClose(h);
    return "[" + sb.ToString().Trim().Replace("\n", ",") + "]";
  }
}
"""

BLE_PS = r"""
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$devs = @{}
$watcher = New-Object Windows.Devices.Bluetooth.Advertisement.BluetoothLEAdvertisementWatcher
$watcher.ScanningMode = [Windows.Devices.Bluetooth.Advertisement.BluetoothLEScanningMode]::Active
Register-ObjectEvent -InputObject $watcher -EventName Received -Action {
    $e = $Event.SourceEventArgs
    $addr = $e.BluetoothAddress
    $name = ""
    if ($e.Advertisement.LocalName) { $name = $e.Advertisement.LocalName }
    $mac = ($addr.ToString("X12") -replace '(.{2})(?!$)', '$1:')
    $script:devs[$mac] = [pscustomobject]@{ address = $mac; name = $name; rssi = $e.RawSignalStrengthInDBm }
} | Out-Null
$watcher.Start()
Start-Sleep -Seconds __SECONDS__
$watcher.Stop()
$out = @()
foreach ($k in $script:devs.Keys) { $out += $script:devs[$k] }
if ($out.Count -eq 0) { "[]" } else { $out | ConvertTo-Json -Compress -AsArray }
"""

HOTSPOT_PS = r"""
$ErrorActionPreference = "Stop"
$mode = "__MODE__"
$ssid = "__SSID__"
$pass = "__PASS__"
$profiles = [Windows.Networking.Connectivity.NetworkInformation]::GetConnectionProfiles()
$target = $null
foreach ($p in $profiles) {
    if ($p.IsWlanConnectionProfile) { $target = $p; break }
}
if (-not $target) { foreach ($p in $profiles) { if ($p.GetNetworkConnectivityLevel() -ne $null) { $target = $p; break } } }
if (-not $target) { Write-Output '{"ok":false,"err":"没有可用的上网连接（无法共享）"}'; exit 0 }
$mgr = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager]::CreateFromConnectionProfile($target)
if ($mode -eq "status") {
    Write-Output ("{`"ok`":true,`"state`":`"" + $mgr.TetheringOperationalState + "`",`"clients`":" + $mgr.ClientCount + "}")
    exit 0
}
if ($mode -eq "stop") {
    $r = $mgr.StopTetheringAsync()
    while ($r.Status -eq [Windows.Foundation.AsyncStatus]::Started) { Start-Sleep -Milliseconds 120 }
    Write-Output '{"ok":true,"state":"stopped"}'
    exit 0
}
$cfg = New-Object Windows.Networking.NetworkOperators.NetworkOperatorTetheringAccessPointConfiguration
$cfg.Ssid = $ssid
$cfg.Passphrase = $pass
$r = $mgr.StartTetheringAsync($cfg)
while ($r.Status -eq [Windows.Foundation.AsyncStatus]::Started) { Start-Sleep -Milliseconds 150 }
$res = $r.GetResults()
Write-Output ("{`"ok`":true,`"state`":`"" + $mgr.TetheringOperationalState + "`",`"ssid`":`"" + $ssid + "`",`"status`":`"" + $res.Status + "`"}")
"""


def _run_ps(script, timeout=40):
    """把脚本写到临时文件再执行，输出重定向到文件后读回（避开管道限制与编码问题）。"""
    workdir = tempfile.mkdtemp(prefix="ks-bt-")
    ps1 = os.path.join(workdir, "run.ps1")
    out = os.path.join(workdir, "out.txt")
    with open(ps1, "w", encoding="utf-8-sig") as fh:
        fh.write(script)
    try:
        subprocess.run(_PS + [ps1], timeout=timeout, check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if not os.path.exists(out):
            return ""
        with open(out, "r", encoding="utf-8-sig", errors="replace") as fh:
            return fh.read().strip()
    except Exception:
        return ""
    finally:
        try:
            for name in ("run.ps1", "out.txt"):
                path = os.path.join(workdir, name)
                if os.path.exists(path):
                    os.remove(path)
            os.rmdir(workdir)
        except Exception:
            pass


def _wrap(body, timeout=40):
    """把 body 包成脚本，并把结果写到 out.txt。"""
    return (
        "$ErrorActionPreference = 'Stop'\n"
        "$outFile = Join-Path $PSScriptRoot 'out.txt'\n"
        "try {\n" + body + "\n} catch { '[]' | Out-File -FilePath $outFile -Encoding utf8 }\n"
    )


def classic_inquiry(timeout=25):
    """经典蓝牙搜索，返回 [{address,name,connected,remembered}]。"""
    script = (
        "$ErrorActionPreference = 'SilentlyContinue'\n"
        "$outFile = Join-Path $PSScriptRoot 'out.txt'\n"
        "Add-Type -TypeDefinition @'\n" + CLASSIC_CS + "\n'@ -Language CSharp | Out-Null\n"
        "[KS_BthInq]::Run() | Out-File -FilePath $outFile -Encoding utf8\n"
    )
    raw = _run_ps(script, timeout=timeout)
    return _parse_list(raw)


def ble_scan(seconds=6, timeout=30):
    """BLE 扫描，返回 [{address,name,rssi}]。"""
    script = _wrap(BLE_PS.replace("__SECONDS__", str(int(seconds))))
    script = script.replace("if ($out.Count -eq 0) { \"[]\" }", "if ($out.Count -eq 0) { \"[]\" | Out-File -FilePath $outFile -Encoding utf8 }")
    script = script.replace("else { $out | ConvertTo-Json -Compress -AsArray }",
                            "else { $out | ConvertTo-Json -Compress -AsArray | Out-File -FilePath $outFile -Encoding utf8 }")
    script = script.replace('"[]" | Out-File', '"[]" | Out-File')
    raw = _run_ps(script, timeout=timeout)
    return _parse_list(raw)


def _parse_list(raw):
    if not raw:
        return []
    raw = raw.strip()
    try:
        data = json.loads(raw.replace("\n", "").replace("\r", ""))
        if isinstance(data, dict):
            data = [data]
        return [d for d in data if isinstance(d, dict)]
    except Exception:
        # ConvertTo-Json 可能输出多行对象，做一次兜底解析
        items = []
        for line in raw.splitlines():
            line = line.strip().rstrip(",")
            if line.startswith("{") and line.endswith("}"):
                try:
                    items.append(json.loads(line))
                except Exception:
                    pass
        return items


def hotspot_status(timeout=20):
    return _hotspot("status", "", "", timeout)


def hotspot_start(ssid, password, timeout=60):
    if not ssid or len(password) < 8:
        return {"ok": False, "err": "热点名不能为空，密码至少 8 位"}
    return _hotspot("start", ssid, password, timeout)


def hotspot_stop(timeout=40):
    return _hotspot("stop", "", "", timeout)


def _hotspot(mode, ssid, password, timeout):
    body = HOTSPOT_PS.replace("__MODE__", mode).replace("__SSID__", ssid).replace("__PASS__", password)
    script = (
        "$ErrorActionPreference = 'Stop'\n"
        "$outFile = Join-Path $PSScriptRoot 'out.txt'\n"
        "try {\n" + body + "\n} catch {\n"
        "  ('{\"ok\":false,\"err\":\"' + ($_.Exception.Message -replace '\"','\\\"') + '\"}')"
        " | Out-File -FilePath $outFile -Encoding utf8\n"
        "}\n"
    )
    raw = _run_ps(script, timeout=timeout)
    try:
        return json.loads(raw) if raw else {"ok": False, "err": "无输出（可能没有管理员权限）"}
    except Exception:
        return {"ok": False, "err": raw[:200] or "热点操作失败"}


if __name__ == "__main__":
    print("classic:", json.dumps(classic_inquiry()[:5], ensure_ascii=False))
    print("ble:", json.dumps(ble_scan(4)[:5], ensure_ascii=False))
    print("hotspot:", json.dumps(hotspot_status(), ensure_ascii=False))

def ble_peers(seconds=4):
    """扫描 BLE 广播，解析形如 ``K|<ip>|<id>`` 的名称，返回可直连的 peer。

    iOS 端把 IP 编进广播名，因此即使 UDP 广播被防火墙拦截也能发现设备。
    """
    out = []
    for item in ble_scan(seconds):
        name = (item.get("name") or "").strip()
        if not name.startswith("K|"):
            continue
        parts = name.split("|")
        if len(parts) < 2 or not parts[1]:
            continue
        host = parts[1].strip()
        if ":" in host:
            host, _, port = host.partition(":")
        else:
            port = "47377"
        try:
            port_num = int(port)
        except ValueError:
            port_num = 47377
        out.append({
            "did": "ble-" + (parts[2] if len(parts) > 2 else host.replace(".", "")),
            "name": "iPhone (%s)" % host,
            "kind": "ios",
            "osv": "",
            "ip": host,
            "port": port_num,
            "rssi": item.get("rssi", 0),
        })
    return out
