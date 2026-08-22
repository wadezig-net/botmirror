import time
import psutil

from utils import get_progress_bar, format_bytes, format_speed, format_duration

# state buat ngitung kecepatan network instan (butuh 2 sample buat dapet delta)
_net_state = {"t": None, "sent": None, "recv": None}


def get_system_stats():
    cpu = psutil.cpu_percent(interval=None)
    ram = psutil.virtual_memory().percent
    disk = psutil.disk_usage("/")

    now = time.monotonic()
    net = psutil.net_io_counters()
    down_speed = up_speed = 0.0
    if _net_state["t"] is not None:
        dt = max(now - _net_state["t"], 0.001)
        down_speed = max(0, (net.bytes_recv - _net_state["recv"]) / dt)
        up_speed = max(0, (net.bytes_sent - _net_state["sent"]) / dt)
    _net_state.update(t=now, sent=net.bytes_sent, recv=net.bytes_recv)

    return {
        "cpu": cpu,
        "ram": ram,
        "free": disk.free,
        "down_speed": down_speed,
        "up_speed": up_speed,
        "net_total_down": net.bytes_recv,
        "net_total_up": net.bytes_sent,
        "uptime": format_duration(time.time() - psutil.boot_time()),
    }


def render_system_block():
    s = get_system_stats()
    return (
        "SYSTEM\n"
        f"🟢 Cpu [{get_progress_bar(s['cpu'])}] {s['cpu']:.1f}%\n"
        f"🟢 Ram [{get_progress_bar(s['ram'])}] {s['ram']:.1f}%\n"
        f"🟢 Free [{get_progress_bar(100 - s['ram'], 10)}] {format_bytes(s['free'])}\n"
        f"⚡ Spd ↓{format_speed(s['down_speed'])} ↑{format_speed(s['up_speed'])}\n"
        f"🌐 Net ↓{format_bytes(s['net_total_down'])} ↑{format_bytes(s['net_total_up'])}\n"
        f"⏱ Upt {s['uptime']}"
    )
