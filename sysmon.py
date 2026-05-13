#!/usr/bin/env python3
"""
System Monitor — Claude Terminal Dashboard
  Q  — quit
  S  — run speedtest
"""

import os, sys, time, threading, subprocess, platform, re
from datetime import datetime
from collections import deque
from typing import Optional, Dict, List

import psutil
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box

# ── Optional deps ──────────────────────────────────────────────────────────────
try:
    import GPUtil as _gputil
    _GPU = True
except ImportError:
    _GPU = False

try:
    import wmi as _wmimod
    _WMI = True
except ImportError:
    _WMI = False

# ── Theme (Claude dark) ────────────────────────────────────────────────────────
T = {
    "border": "grey27",
    "dim":    "grey54",
    "text":   "grey93",
    "accent": "#C9A96E",
    "cpu":    "#58A6FF",
    "gpu":    "#3FB950",
    "ram":    "#E3B341",
    "disk":   "#BC8CFF",
    "nd":     "#56D364",
    "nu":     "#FF7B72",
    "ok":     "#3FB950",
    "warn":   "#E3B341",
    "err":    "#F85149",
    "von":    "#3FB950",
    "voff":   "grey54",
    "pwr":    "#FF9500",
}

HIST     = 26   # sparkline history length
PWR_HIST = 60   # power history (~3 min at 3s interval)
_NO_WIN = subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0

# ── Utils ──────────────────────────────────────────────────────────────────────
_SPARKS = " ▁▂▃▄▅▆▇█"

def spark(data: deque, color: str, w: int = HIST) -> Text:
    vals = list(data)[-w:]
    mx = max(vals) if vals else 1
    out = Text()
    for v in vals:
        p = v / max(mx, 1)
        ch = _SPARKS[min(int(p * 8), 8)]
        c = T["err"] if p > 0.85 else T["warn"] if p > 0.6 else color
        out.append(ch, style=c)
    return Text(" " * (w - len(vals)), style=T["dim"]) + out


def bar(pct: float, w: int = 20, color: str = "white") -> Text:
    n = int(min(pct, 100) / 100 * w)
    c = T["err"] if pct > 85 else T["warn"] if pct > 60 else color
    t = Text()
    t.append("█" * n, style=c)
    t.append("░" * (w - n), style=T["dim"])
    return t


def fmt_b(b: float) -> str:
    for u in ("B", "KB", "MB", "GB", "TB"):
        if abs(b) < 1024:
            return f"{b:.1f}{u}"
        b /= 1024
    return f"{b:.1f}PB"


def fmt_spd(bps: float) -> str:
    m = bps / 1_048_576
    if m >= 1:
        return f"{m:.1f} MB/s"
    return f"{bps/1024:.1f} KB/s"


def tc(c: float) -> str:
    return T["err"] if c > 85 else T["warn"] if c > 70 else T["ok"]


def ping_parse(stdout: str):
    times = [int(x) for x in re.findall(r"(?:[Tt]ime[=<]|время=)(\d+)", stdout)]
    loss  = re.search(r"(\d+)\s*%\s*(?:loss|потер)", stdout, re.I)
    ms    = sum(times) / len(times) if times else None
    pl    = float(loss.group(1)) if loss else 0.0
    return ms, pl


# ── Collector ──────────────────────────────────────────────────────────────────
class Collector:
    def __init__(self):
        self._lk = threading.Lock()
        self._alive = True

        # CPU
        self.cpu_pct:   float = 0.0
        self.cpu_freq:  float = 0.0
        self.cpu_temp:  Optional[float] = None
        self.cpu_power: Optional[float] = None
        self.cpu_name:  str = "Unknown CPU"
        self.cpu_cores: List[float] = []
        self.cpu_h = deque(maxlen=HIST)

        # GPU
        self.gpu_ok:    bool = False
        self.gpu_name:  str  = ""
        self.gpu_pct:   float = 0.0
        self.gpu_temp:  Optional[float] = None
        self.gpu_power: Optional[float] = None
        self.gpu_vu:   float = 0.0
        self.gpu_vt:   float = 0.0
        self.gpu_h = deque(maxlen=HIST)

        # RAM
        self.ram_pct:   float = 0.0
        self.ram_used:  float = 0.0
        self.ram_total: float = 0.0
        self.ram_h = deque(maxlen=HIST)

        # Disk
        self.disks:  List[Dict] = []
        self.disk_r: float = 0.0
        self.disk_w: float = 0.0
        self._dp = None
        self._dt = time.time()

        # Network
        self.net_d:  float = 0.0
        self.net_u:  float = 0.0
        self.net_dh = deque(maxlen=HIST)
        self.net_uh = deque(maxlen=HIST)
        self._np = None

        # Ping
        self.ping_ms: Optional[float] = None
        self.pkt_loss: float = 0.0

        # Processes
        self.procs: List[Dict] = []

        # VPN
        self.vpn: Dict = {}
        self.vpn_checked_at: float = 0.0
        self.vpn_checking: bool = False

        # Power
        self.pwr_total:    float = 0.0
        self.pwr_avg:      float = 0.0
        self.pwr_peak:     float = 0.0
        self.pwr_sys_base: float = 45.0   # estimated chipset/RAM/fans/drives
        self.pwr_h: deque = deque(maxlen=PWR_HIST)

        # Speedtest
        self.st_d:  Optional[float] = None
        self.st_u:  Optional[float] = None
        self.st_ts: str = ""
        self.st_busy: bool = False

        self._init_names()
        threading.Thread(target=self._main,      daemon=True).start()
        threading.Thread(target=self._temp_loop, daemon=True).start()
        threading.Thread(target=self._ping,      daemon=True).start()
        threading.Thread(target=self._vpn_check, daemon=True).start()
        threading.Thread(target=self._vpn_ping,  daemon=True).start()

    # ── init ──
    def _init_names(self):
        try:
            import winreg
            k = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
            )
            self.cpu_name = winreg.QueryValueEx(k, "ProcessorNameString")[0].strip()
        except Exception:
            self.cpu_name = platform.processor() or "Unknown CPU"

        if _GPU:
            try:
                gpus = _gputil.getGPUs()
                if gpus:
                    self.gpu_ok   = True
                    self.gpu_name = gpus[0].name
            except Exception:
                pass

    # ── temperature ──
    def _cpu_temp(self) -> Optional[float]:
        # Python wmi module path
        if _WMI:
            for ns in ("root\\OpenHardwareMonitor", "root\\LibreHardwareMonitor"):
                try:
                    w = _wmimod.WMI(namespace=ns)
                    vals = [float(s.Value) for s in w.Sensor()
                            if s.SensorType == "Temperature" and "CPU" in s.Name]
                    if vals:
                        return max(vals)
                except Exception:
                    pass

        # PowerShell fallback — works without Python wmi package
        for ns in ("root/OpenHardwareMonitor", "root/LibreHardwareMonitor"):
            try:
                r = subprocess.run(
                    ["powershell", "-NoProfile", "-Command",
                     f"Get-CimInstance -Namespace {ns} -ClassName Sensor "
                     f"| Where-Object {{$_.SensorType -eq 'Temperature' -and $_.Name -like '*CPU*'}} "
                     f"| Measure-Object -Property Value -Maximum "
                     f"| Select-Object -ExpandProperty Maximum"],
                    capture_output=True, text=True, timeout=6, creationflags=_NO_WIN,
                )
                val = float(r.stdout.strip())
                if 0 < val < 150:
                    return val
            except Exception:
                pass

        # ACPI thermal zone last resort
        if _WMI:
            try:
                w = _wmimod.WMI(namespace="root\\wmi")
                zones = w.MSAcpi_ThermalZoneTemperature()
                if zones:
                    c = zones[0].CurrentTemperature / 10.0 - 273.15
                    if 0 < c < 150:
                        return c
            except Exception:
                pass
        return None

    # ── power query (CPU package + GPU watts via OHM/LHM or nvidia-smi) ──
    def _query_power(self):
        cpu_w = gpu_w = None

        if _WMI:
            for ns in ("root\\OpenHardwareMonitor", "root\\LibreHardwareMonitor"):
                try:
                    w = _wmimod.WMI(namespace=ns)
                    for s in w.Sensor():
                        if s.SensorType != "Power":
                            continue
                        n = s.Name.lower()
                        v = float(s.Value)
                        if cpu_w is None and ("cpu package" in n or "cpu total" in n):
                            cpu_w = v
                        if gpu_w is None and "gpu" in n:
                            gpu_w = v
                    if cpu_w is not None or gpu_w is not None:
                        break
                except Exception:
                    pass

        for ns in ("root/OpenHardwareMonitor", "root/LibreHardwareMonitor"):
            changed = False
            if cpu_w is None:
                try:
                    r = subprocess.run(
                        ["powershell", "-NoProfile", "-Command",
                         f"Get-CimInstance -Namespace {ns} -ClassName Sensor "
                         "| Where-Object {$_.SensorType -eq 'Power' -and "
                         "($_.Name -like '*CPU Package*' -or $_.Name -like '*CPU Total*')} "
                         "| Measure-Object -Property Value -Maximum "
                         "| Select-Object -ExpandProperty Maximum"],
                        capture_output=True, text=True, timeout=6, creationflags=_NO_WIN,
                    )
                    val = r.stdout.strip()
                    if val:
                        v = float(val)
                        if 0 < v < 1000:
                            cpu_w = v
                            changed = True
                except Exception:
                    pass
            if gpu_w is None:
                try:
                    r = subprocess.run(
                        ["powershell", "-NoProfile", "-Command",
                         f"Get-CimInstance -Namespace {ns} -ClassName Sensor "
                         "| Where-Object {$_.SensorType -eq 'Power' -and $_.Name -like '*GPU*'} "
                         "| Measure-Object -Property Value -Maximum "
                         "| Select-Object -ExpandProperty Maximum"],
                        capture_output=True, text=True, timeout=6, creationflags=_NO_WIN,
                    )
                    val = r.stdout.strip()
                    if val:
                        v = float(val)
                        if 0 < v < 500:
                            gpu_w = v
                            changed = True
                except Exception:
                    pass
            if changed:
                break

        if gpu_w is None:
            try:
                r = subprocess.run(
                    ["nvidia-smi", "--query-gpu=power.draw",
                     "--format=csv,noheader,nounits"],
                    capture_output=True, text=True, timeout=4, creationflags=_NO_WIN,
                )
                v = float(r.stdout.strip())
                if v > 0:
                    gpu_w = v
            except Exception:
                pass

        return cpu_w, gpu_w

    # ── temperature polling loop (separate thread, slow queries ok) ──
    def _temp_loop(self):
        while self._alive:
            t = self._cpu_temp()
            cpu_w, gpu_w = self._query_power()
            with self._lk:
                if t is not None:
                    self.cpu_temp = t
                self.cpu_power = cpu_w
                self.gpu_power = gpu_w
                if cpu_w is not None or gpu_w is not None:
                    total = (cpu_w or 0.0) + (gpu_w or 0.0) + self.pwr_sys_base
                    self.pwr_total = total
                    self.pwr_h.append(total)
                    self.pwr_avg  = sum(self.pwr_h) / len(self.pwr_h)
                    self.pwr_peak = max(self.pwr_h)
            time.sleep(3)

    # ── main loop ──
    def _main(self):
        psutil.cpu_percent(interval=None)  # warmup
        while self._alive:
            try:
                cpu_pct   = psutil.cpu_percent(interval=1)
                cpu_cores = psutil.cpu_percent(percpu=True)
                freq_obj  = psutil.cpu_freq()
                cpu_freq  = (freq_obj.current / 1000) if freq_obj else 0.0

                # GPU
                gp = gt = gvu = gvt = 0.0
                if self.gpu_ok:
                    try:
                        g = _gputil.getGPUs()[0]
                        gp  = g.load * 100
                        gt  = float(g.temperature)
                        gvu = g.memoryUsed
                        gvt = g.memoryTotal
                    except Exception:
                        pass

                # RAM
                ram = psutil.virtual_memory()

                # Disk I/O
                now = time.time()
                dt  = max(now - self._dt, 0.001)
                dio = psutil.disk_io_counters()
                dr = dw = 0.0
                if self._dp and dio:
                    dr = max(dio.read_bytes  - self._dp.read_bytes,  0) / dt
                    dw = max(dio.write_bytes - self._dp.write_bytes, 0) / dt
                self._dp = dio
                self._dt = now

                disks = []
                for p in psutil.disk_partitions():
                    if not p.fstype:
                        continue
                    try:
                        u = psutil.disk_usage(p.mountpoint)
                        disks.append({
                            "mp":    p.mountpoint,
                            "total": u.total,
                            "used":  u.used,
                            "free":  u.free,
                            "pct":   u.percent,
                        })
                    except Exception:
                        pass

                # Network
                net = psutil.net_io_counters()
                nd = nu = 0.0
                if self._np:
                    nd = max(net.bytes_recv - self._np.bytes_recv, 0)
                    nu = max(net.bytes_sent - self._np.bytes_sent, 0)
                self._np = net

                # Processes
                procs = []
                for p in psutil.process_iter(["pid", "name", "cpu_percent", "memory_info"]):
                    try:
                        i = p.info
                        if i["memory_info"]:
                            procs.append({
                                "name": i["name"] or "",
                                "pid":  i["pid"],
                                "cpu":  i["cpu_percent"] or 0.0,
                                "ram":  i["memory_info"].rss,
                            })
                    except Exception:
                        pass
                procs.sort(key=lambda x: x["cpu"], reverse=True)

                with self._lk:
                    self.cpu_pct   = cpu_pct
                    self.cpu_freq  = cpu_freq
                    self.cpu_cores = cpu_cores
                    self.cpu_h.append(cpu_pct)

                    self.gpu_pct  = gp
                    self.gpu_temp = gt if gp or gt else None
                    self.gpu_vu   = gvu
                    self.gpu_vt   = gvt
                    self.gpu_h.append(gp)

                    self.ram_pct   = ram.percent
                    self.ram_used  = ram.used
                    self.ram_total = ram.total
                    self.ram_h.append(ram.percent)

                    self.disks  = disks
                    self.disk_r = dr
                    self.disk_w = dw

                    self.net_d = nd
                    self.net_u = nu
                    self.net_dh.append(nd)
                    self.net_uh.append(nu)

                    self.procs = procs[:10]

            except Exception:
                time.sleep(2)

    # ── ping loop ──
    def _ping(self):
        while self._alive:
            try:
                r = subprocess.run(
                    ["ping", "-n", "8", "8.8.8.8"],
                    capture_output=True, text=True, timeout=20,
                    creationflags=_NO_WIN,
                )
                ms, pl = ping_parse(r.stdout)
                with self._lk:
                    self.ping_ms  = ms
                    self.pkt_loss = pl
            except Exception:
                pass
            time.sleep(30)

    # ── VPN check loop (fast: every 3s) ──
    def _vpn_check(self):
        while self._alive:
            with self._lk:
                self.vpn_checking = True
                prev = {k: dict(v) for k, v in self.vpn.items()}
            try:
                proc_names = {p.name().lower()
                              for p in psutil.process_iter(["name"])
                              if p.info.get("name")}
                iface_stats = psutil.net_if_stats()
                vpn_new: Dict = {}

                def _since(name: str, now_active: bool) -> Optional[float]:
                    p = prev.get(name, {})
                    if now_active and not p.get("active"):
                        return time.time()
                    if not now_active:
                        return None
                    return p.get("since")

                def _keep(name: str, key: str, default=None):
                    return prev.get(name, {}).get(key, default)

                # ── WARP ──
                # warp-svc.exe runs as background service even when disconnected
                # — only warp-cli status is authoritative
                warp_up, warp_how = False, ""
                try:
                    r = subprocess.run(
                        ["warp-cli", "status"],
                        capture_output=True, text=True, timeout=4,
                        creationflags=_NO_WIN,
                    )
                    out = r.stdout.strip()
                    if "Connected" in out and "Disconnected" not in out:
                        warp_up, warp_how = True, "warp-cli"
                except Exception:
                    # warp-cli unavailable — fall back to interface check
                    for iname, st in iface_stats.items():
                        if ("cloudflare" in iname.lower() or "warp" in iname.lower()) and st.isup:
                            warp_up, warp_how = True, "interface"
                            break
                vpn_new["WARP"] = {
                    "active":    warp_up,
                    "method":    warp_how,
                    "since":     _since("WARP", warp_up),
                    "ping":      _keep("WARP", "ping"),
                    "ping_busy": _keep("WARP", "ping_busy", False),
                }

                # ── Zapret / GoodbyeDPI ──
                zap_up, zap_how = False, ""
                if any(n in proc_names for n in
                       ("winws.exe", "zapret.exe", "goodbyedpi.exe", "windivert.exe")):
                    zap_up, zap_how = True, "process"
                if not zap_up:
                    for svc in ("zapret", "GoodbyeDPI", "winws"):
                        try:
                            r = subprocess.run(
                                ["sc", "query", svc],
                                capture_output=True, text=True, timeout=3,
                                creationflags=_NO_WIN,
                            )
                            if "RUNNING" in r.stdout:
                                zap_up, zap_how = True, f"service:{svc}"
                                break
                        except Exception:
                            pass
                vpn_new["Zapret"] = {
                    "active": zap_up,
                    "method": zap_how,
                    "since":  _since("Zapret", zap_up),
                    "ping":   None,
                    "ping_busy": False,
                }

                with self._lk:
                    self.vpn = vpn_new
                    self.vpn_checked_at = time.time()
                    self.vpn_checking   = False
            except Exception:
                with self._lk:
                    self.vpn_checking = False
            time.sleep(3)

    # ── VPN ping loop (separate, every 8s) ──
    def _vpn_ping(self):
        _targets = {"WARP": "1.1.1.1"}
        while self._alive:
            time.sleep(8)
            try:
                with self._lk:
                    snapshot = {k: dict(v) for k, v in self.vpn.items()}
                for name, info in snapshot.items():
                    if not info.get("active"):
                        continue
                    target = _targets.get(name)
                    if not target:
                        continue
                    with self._lk:
                        if name in self.vpn:
                            self.vpn[name]["ping_busy"] = True
                    try:
                        r = subprocess.run(
                            ["ping", "-n", "3", target],
                            capture_output=True, text=True, timeout=10,
                            creationflags=_NO_WIN,
                        )
                        ms, _ = ping_parse(r.stdout)
                        with self._lk:
                            if name in self.vpn:
                                self.vpn[name]["ping"]      = int(ms) if ms else None
                                self.vpn[name]["ping_busy"] = False
                    except Exception:
                        with self._lk:
                            if name in self.vpn:
                                self.vpn[name]["ping_busy"] = False
            except Exception:
                pass

    # ── speedtest ──
    def run_speedtest(self):
        if self.st_busy:
            return

        def _run():
            with self._lk:
                self.st_busy = True
                self.st_ts   = "running…"
            try:
                import speedtest as _st
                s = _st.Speedtest(secure=True)
                s.get_best_server()
                d = s.download() / 1e6
                u = s.upload()   / 1e6
                with self._lk:
                    self.st_d  = d
                    self.st_u  = u
                    self.st_ts = datetime.now().strftime("%H:%M")
            except Exception as e:
                with self._lk:
                    self.st_ts = f"error"
            finally:
                with self._lk:
                    self.st_busy = False

        threading.Thread(target=_run, daemon=True).start()

    def stop(self):
        self._alive = False


# ── Renderer ───────────────────────────────────────────────────────────────────
class Renderer:
    def __init__(self, c: Collector):
        self.c = c

    def _cpu(self) -> Panel:
        c = self.c
        t = Text()
        t.append(f" {c.cpu_name[:36]}\n\n", style=f"bold {T['text']}")
        t.append(" Load   ", style=T["dim"])
        t.append(bar(c.cpu_pct, 19, T["cpu"]))
        t.append(f"  {c.cpu_pct:5.1f}%\n", style=f"bold {T['cpu']}")
        t.append(" Clock  ", style=T["dim"])
        t.append(f"{c.cpu_freq:.2f} GHz\n", style=T["text"])
        t.append(" Cores  ", style=T["dim"])
        t.append(f"{psutil.cpu_count(logical=False)} phys / {psutil.cpu_count()} logical\n",
                 style=T["dim"])
        t.append(" Temp   ", style=T["dim"])
        if c.cpu_temp is not None:
            t.append(f"{c.cpu_temp:.0f}°C\n", style=tc(c.cpu_temp))
        else:
            t.append("—  (запусти OHM или LHM)\n", style=T["dim"])
        t.append(f"\n {spark(c.cpu_h, T['cpu'])}")
        return Panel(t, title=f"[bold {T['cpu']}]  CPU[/]",
                     border_style=T["border"], box=box.ROUNDED)

    def _gpu(self) -> Panel:
        c = self.c
        t = Text()
        if not c.gpu_ok:
            t.append("\n  Not detected\n", style=T["dim"])
            t.append("  Install GPUtil for NVIDIA support\n\n", style=T["dim"])
        else:
            t.append(f" {c.gpu_name[:36]}\n\n", style=f"bold {T['text']}")
            t.append(" Load   ", style=T["dim"])
            t.append(bar(c.gpu_pct, 19, T["gpu"]))
            t.append(f"  {c.gpu_pct:5.1f}%\n", style=f"bold {T['gpu']}")
            if c.gpu_vt:
                vp = c.gpu_vu / c.gpu_vt * 100
                t.append(" VRAM   ", style=T["dim"])
                t.append(bar(vp, 19, T["gpu"]))
                t.append(f"  {vp:5.1f}%\n", style=T["gpu"])
                t.append(f"        {c.gpu_vu:.0f}/{c.gpu_vt:.0f} MB\n", style=T["dim"])
            t.append(" Temp   ", style=T["dim"])
            if c.gpu_temp is not None:
                t.append(f"{c.gpu_temp:.0f}°C\n", style=tc(c.gpu_temp))
            else:
                t.append("—\n", style=T["dim"])
            t.append(f"\n {spark(c.gpu_h, T['gpu'])}")
        return Panel(t, title=f"[bold {T['gpu']}]  GPU[/]",
                     border_style=T["border"], box=box.ROUNDED)

    def _ram(self) -> Panel:
        c = self.c
        ug = c.ram_used  / 1_073_741_824
        tg = c.ram_total / 1_073_741_824
        sw = psutil.swap_memory()
        t = Text()
        t.append(f"\n {ug:.1f} / {tg:.1f} GB\n\n", style=f"bold {T['text']}")
        t.append(" Used   ", style=T["dim"])
        t.append(bar(c.ram_pct, 19, T["ram"]))
        t.append(f"  {c.ram_pct:5.1f}%\n", style=f"bold {T['ram']}")
        if sw.total:
            sp = sw.percent
            sg = sw.used / 1_073_741_824
            stg = sw.total / 1_073_741_824
            t.append(" Swap   ", style=T["dim"])
            t.append(bar(sp, 19, T["dim"]))
            t.append(f"  {sg:.1f}/{stg:.1f} GB\n", style=T["dim"])
        t.append(f"\n {spark(c.ram_h, T['ram'])}")
        return Panel(t, title=f"[bold {T['ram']}]  RAM[/]",
                     border_style=T["border"], box=box.ROUNDED)

    def _disk(self) -> Panel:
        c = self.c
        t = Text()
        for d in c.disks:
            mp  = d["mp"].rstrip("\\")
            tg  = d["total"] / 1_073_741_824
            ug  = d["used"]  / 1_073_741_824
            fg  = d["free"]  / 1_073_741_824
            pct = d["pct"]
            t.append(f" {mp:<6}", style=f"bold {T['disk']}")
            t.append(bar(pct, 22, T["disk"]))
            t.append(f" {pct:5.1f}%  ", style=f"bold {T['disk']}")
            t.append(f"{ug:.0f}/{tg:.0f} GB   free {fg:.1f} GB\n", style=T["dim"])
        if c.disk_r or c.disk_w:
            t.append(
                f"\n  Disk I/O   ↓ {fmt_spd(c.disk_r)}   ↑ {fmt_spd(c.disk_w)}",
                style=T["dim"],
            )
        return Panel(t, title=f"[bold {T['disk']}]  STORAGE[/]",
                     border_style=T["border"], box=box.ROUNDED)

    def _procs(self) -> Panel:
        c = self.c
        tbl = Table(box=None, padding=(0, 1), show_header=True,
                    header_style=f"bold {T['dim']}")
        tbl.add_column("#",    width=3,  style=T["dim"])
        tbl.add_column("Process", width=24)
        tbl.add_column("CPU%", width=7,  justify="right")
        tbl.add_column("RAM",  width=9,  justify="right")

        for i, p in enumerate(c.procs, 1):
            cc = T["err"] if p["cpu"] > 50 else T["warn"] if p["cpu"] > 15 else T["ok"]
            tbl.add_row(
                str(i),
                Text(p["name"][:24], style=T["text"]),
                Text(f"{p['cpu']:5.1f}%",   style=cc),
                Text(fmt_b(p["ram"]),        style=T["dim"]),
            )

        return Panel(tbl, title=f"[bold {T['accent']}]  TOP PROCESSES[/]",
                     border_style=T["border"], box=box.ROUNDED)

    def _net(self) -> Panel:
        c = self.c
        t = Text()
        t.append(f"\n ↓  {fmt_spd(c.net_d):<14}", style=f"bold {T['nd']}")
        t.append(f"\n ↑  {fmt_spd(c.net_u):<14}\n", style=f"bold {T['nu']}")
        t.append("\n Ping   ", style=T["dim"])
        if c.ping_ms is not None:
            pc = T["err"] if c.ping_ms > 150 else T["warn"] if c.ping_ms > 80 else T["ok"]
            t.append(f"{c.ping_ms:.0f} ms\n", style=pc)
        else:
            t.append("…\n", style=T["dim"])
        t.append(" Loss   ", style=T["dim"])
        lc = T["err"] if c.pkt_loss > 5 else T["warn"] if c.pkt_loss > 0 else T["ok"]
        t.append(f"{c.pkt_loss:.0f}%\n\n", style=lc)

        if c.st_busy:
            t.append(f" ⏳ Speedtest running…\n", style=T["dim"])
        elif c.st_d is not None:
            t.append(f" SpeedTest {c.st_ts}\n", style=T["dim"])
            t.append(f" ↓ {c.st_d:.1f}  ↑ {c.st_u:.1f} Mbps\n", style=T["text"])
        elif c.st_ts == "error":
            t.append(f" Speedtest: ошибка\n", style=T["err"])
            t.append(f" [S] повторить\n", style=T["dim"])
        else:
            t.append(f" [S] запустить speedtest\n", style=T["dim"])

        t.append(f"\n ↓ {spark(c.net_dh, T['nd'])}\n")
        t.append(f" ↑ {spark(c.net_uh, T['nu'])}")
        return Panel(t, title=f"[bold {T['nd']}]  NETWORK[/]",
                     border_style=T["border"], box=box.ROUNDED)

    def _vpn(self) -> Panel:
        c = self.c
        t = Text()

        # Status bar: last check / checking indicator
        t.append("\n")
        if c.vpn_checking:
            pulse = "◌" if int(time.time() * 2) % 2 == 0 else "○"
            t.append(f" {pulse} checking…\n\n", style=T["dim"])
        elif c.vpn_checked_at > 0:
            ago = int(time.time() - c.vpn_checked_at)
            t.append(f" ↻ updated {ago}s ago\n\n", style=T["dim"])
        else:
            t.append(" Initializing…\n\n", style=T["dim"])

        if not c.vpn:
            t.append("  Scanning…\n", style=T["dim"])
        else:
            for name, info in c.vpn.items():
                on  = info.get("active", False)
                sc  = T["von"] if on else T["voff"]
                dot = "●" if on else "○"
                st  = "ACTIVE" if on else "OFF"

                t.append(f"  {dot} ", style=sc)
                t.append(f"{name:<12}", style=f"bold {T['text']}" if on else T["dim"])
                t.append(f" {st:<6}", style=sc)

                if on:
                    # Ping
                    if info.get("ping_busy"):
                        t.append("  …ms  ", style=T["dim"])
                    elif info.get("ping") is not None:
                        pm = info["ping"]
                        pc = T["err"] if pm > 150 else T["warn"] if pm > 80 else T["ok"]
                        t.append(f"  {pm}ms  ", style=pc)
                    else:
                        t.append("         ", style=T["dim"])

                    # Uptime
                    since = info.get("since")
                    if since:
                        el = int(time.time() - since)
                        if el < 60:
                            up = f"{el}s"
                        elif el < 3600:
                            up = f"{el//60}m{el%60:02d}s"
                        else:
                            up = f"{el//3600}h{(el%3600)//60:02d}m"
                        t.append(f"up {up}", style=T["dim"])

                    # Detection method
                    method = info.get("method", "")
                    if method:
                        t.append(f"\n              [{method}]", style=T["dim"])

                t.append("\n")

        return Panel(t, title=f"[bold {T['accent']}]  VPN[/]",
                     border_style=T["border"], box=box.ROUNDED)

    def _cores(self) -> Panel:
        c = self.c
        cores = c.cpu_cores
        t = Text()
        if cores:
            n = len(cores)
            per_row = 8 if n > 8 else n
            t.append("\n")
            for i, pct in enumerate(cores):
                if i > 0 and i % per_row == 0:
                    t.append("\n")
                col = T["err"] if pct > 85 else T["warn"] if pct > 60 else T["cpu"]
                t.append(f" {i:02d}", style=T["dim"])
                t.append(bar(pct, 7, T["cpu"]))
                t.append(f"{pct:4.0f}%", style=col)
        return Panel(t, title=f"[bold {T['cpu']}]  CPU CORES[/]",
                     border_style=T["border"], box=box.ROUNDED)

    def _power(self) -> Panel:
        c = self.c
        t = Text()
        has_data = c.cpu_power is not None or c.gpu_power is not None

        if not has_data:
            t.append("\n  Нет данных о мощности.\n", style=T["dim"])
            t.append("  Запусти OpenHardwareMonitor или LibreHardwareMonitor.\n", style=T["dim"])
            t.append("  Для GPU NVIDIA также работает nvidia-smi (драйверы NVIDIA).\n", style=T["dim"])
            return Panel(t, title=f"[bold {T['pwr']}]  POWER CONSUMPTION[/]",
                         border_style=T["border"], box=box.ROUNDED)

        MAX_W = 600.0

        def _row(label: str, watts: float, color: str, bold: bool = False):
            pct = min(watts / MAX_W * 100, 100)
            wc = T["err"] if watts > MAX_W * 0.83 else T["warn"] if watts > MAX_W * 0.5 else color
            ls = f"bold {T['text']}" if bold else T["text"]
            t.append(f"  {label:<10}", style=ls)
            t.append(bar(pct, 38, color))
            t.append(f"  {watts:>5.0f} W\n", style=f"bold {wc}" if bold else wc)

        if c.cpu_power is not None:
            _row("CPU", c.cpu_power, T["cpu"])
        if c.gpu_power is not None:
            _row("GPU", c.gpu_power, T["gpu"])
        _row("Система", c.pwr_sys_base, T["dim"])

        # divider
        t.append(f"  {'':10}", style=T["dim"])
        t.append("─" * 38, style=T["dim"])
        t.append("\n")

        # total
        _row("ИТОГО", c.pwr_total, T["pwr"], bold=True)

        # avg / peak
        t.append("\n")
        t.append("  Среднее ", style=T["dim"])
        t.append(f"{c.pwr_avg:>5.0f} W", style=T["pwr"])
        t.append("     Пик ", style=T["dim"])
        pk_c = T["err"] if c.pwr_peak > MAX_W * 0.83 else T["warn"] if c.pwr_peak > MAX_W * 0.5 else T["ok"]
        t.append(f"{c.pwr_peak:>5.0f} W", style=f"bold {pk_c}")

        # sparkline of total power history
        if len(c.pwr_h) > 1:
            t.append(f"\n\n  {spark(c.pwr_h, T['pwr'], w=PWR_HIST)}\n")

        return Panel(t, title=f"[bold {T['pwr']}]  POWER CONSUMPTION[/]",
                     border_style=T["border"], box=box.ROUNDED)

    def _hdr(self) -> Text:
        now = datetime.now().strftime("%Y-%m-%d  %H:%M:%S")
        t = Text(justify="center")
        t.append("  SYSTEM MONITOR v2  ", style=f"bold {T['accent']}")
        t.append(f"  {now}  ", style=T["dim"])
        t.append(" Q", style=f"bold {T['cpu']}")
        t.append(" quit", style=T["dim"])
        t.append("   S", style=f"bold {T['gpu']}")
        t.append(" speedtest  ", style=T["dim"])
        return t

    def layout(self) -> Layout:
        lo = Layout()
        lo.split_column(
            Layout(name="hdr",     size=3),
            Layout(name="row1",    size=13),
            Layout(name="row2",    size=5),
            Layout(name="row_pwr", size=11),
            Layout(name="row3",    size=13),
            Layout(name="row4"),
        )
        lo["hdr"].update(
            Panel(self._hdr(), style=T["border"], box=box.SIMPLE_HEAVY)
        )
        lo["row1"].split_row(
            Layout(self._cpu(), name="cpu"),
            Layout(self._gpu(), name="gpu"),
            Layout(self._ram(), name="ram"),
        )
        lo["row2"].update(self._disk())
        lo["row_pwr"].update(self._power())
        lo["row3"].split_row(
            Layout(self._procs(), name="procs", ratio=3),
            Layout(self._net(),   name="net",   ratio=2),
            Layout(self._vpn(),   name="vpn",   ratio=2),
        )
        lo["row4"].update(self._cores())
        return lo


# ── Entry ──────────────────────────────────────────────────────────────────────
def main():
    col = Collector()
    ren = Renderer(col)
    con = Console()

    try:
        import msvcrt
        has_msvcrt = True
    except ImportError:
        has_msvcrt = False

    running = [True]

    def key_loop():
        while running[0]:
            if has_msvcrt and msvcrt.kbhit():
                k = msvcrt.getch().decode("utf-8", errors="ignore").lower()
                if k == "q":
                    running[0] = False
                elif k == "s":
                    col.run_speedtest()
            time.sleep(0.05)

    threading.Thread(target=key_loop, daemon=True).start()

    try:
        with Live(ren.layout(), refresh_per_second=2, screen=True, console=con) as live:
            while running[0]:
                live.update(ren.layout())
                time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        col.stop()
        con.print(f"\n[{T['dim']}]Stopped.[/]\n")


if __name__ == "__main__":
    main()
