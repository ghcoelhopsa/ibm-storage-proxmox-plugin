#!/usr/bin/python3
"""Builds the performance charts of docs/PERFORMANCE.md (SVG, light and dark theme).

  python3 make_perf_charts.py [iops-log migration-events]

The numbers are the ones measured on the FS7600 lab (see docs/PERFORMANCE.md). With the two
optional files it redraws the live migration chart from the raw fio log; without them it uses
the series stored below.
"""
import collections
import os
import sys

OUT = os.path.dirname(os.path.abspath(__file__))

STYLE = """<style>
  .bg { fill: #fcfcfb } .t1 { fill: #0b0b0b } .t2 { fill: #52514e } .grid { stroke: #e4e3df }
  .axis { stroke: #b9b8b2 } .s1 { fill: #2a78d6 } .s1l { stroke: #2a78d6 } .band { fill: #eb6834; opacity: .12 }
  .s2 { fill: #eb6834 }
  @media (prefers-color-scheme: dark) {
    .bg { fill: #1a1a19 } .t1 { fill: #f0efec } .t2 { fill: #c3c2b7 } .grid { stroke: #34332f }
    .axis { stroke: #5a5953 } .s1 { fill: #3987e5 } .s1l { stroke: #3987e5 } .band { fill: #d95926; opacity: .22 }
    .s2 { fill: #d95926 }
  }
  text { font-family: -apple-system, "Segoe UI", Helvetica, Arial, sans-serif }
</style>"""


def svg(w, h, body, title):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img" '
            f'aria-label="{title}">{STYLE}<rect class="bg" width="{w}" height="{h}" rx="8"/>{body}</svg>\n')


def fmt(v):
    return f"{v:,.0f}" if v >= 100 else (f"{v:.1f}" if v % 1 else f"{v:.0f}")


def hbars(name, title, subtitle, rows, unit):
    """Horizontal bars, one measure, direct labels."""
    W, L, R, top, bh, gap = 860, 300, 110, 78, 26, 14
    H = top + len(rows) * (bh + gap) + 24
    vmax = max(v for _, v in rows) * 1.05
    x = lambda v: L + (W - L - R) * v / vmax
    b = [f'<text class="t1" x="24" y="34" font-size="18" font-weight="600">{title}</text>',
         f'<text class="t2" x="24" y="56" font-size="13">{subtitle}</text>']
    for i, (label, v) in enumerate(rows):
        y = top + i * (bh + gap)
        b.append(f'<text class="t1" x="{L - 12}" y="{y + bh / 2 + 5}" font-size="14" text-anchor="end">{label}</text>')
        b.append(f'<rect class="s1" x="{L}" y="{y}" width="{max(x(v) - L, 3):.1f}" height="{bh}" rx="4"/>')
        b.append(f'<text class="t1" x="{x(v) + 8:.1f}" y="{y + bh / 2 + 5}" font-size="14" font-weight="600">{fmt(v)} {unit}</text>')
    b.append(f'<line class="axis" x1="{L}" x2="{L}" y1="{top - 6}" y2="{H - 18}" stroke-width="1"/>')
    open(os.path.join(OUT, name), "w").write(svg(W, H, "".join(b), title))


def migration(series, windows):
    """IOPS per second with the live migration windows shaded."""
    W, H, L, R, T, B = 860, 330, 64, 24, 78, 44
    t1 = max(t for t, _ in series)
    vmax = 140
    x = lambda t: L + (W - L - R) * t / t1
    y = lambda v: T + (H - T - B) * (1 - v / vmax)
    title = "IOPS inside the VM during four live migrations"
    b = [f'<text class="t1" x="24" y="34" font-size="18" font-weight="600">{title}</text>',
         '<text class="t2" x="24" y="56" font-size="13">fio 4K, 70% read, 4 jobs, queue depth 32 · one point per second · shaded: migration running</text>']
    for v in (0, 35, 70, 105, 140):
        b.append(f'<line class="grid" x1="{L}" x2="{W - R}" y1="{y(v):.1f}" y2="{y(v):.1f}" stroke-width="1"/>')
        b.append(f'<text class="t2" x="{L - 8}" y="{y(v) + 4:.1f}" font-size="12" text-anchor="end">{v if v else 0}{"k" if v else ""}</text>')
    for i, (a, e, label) in enumerate(windows):
        b.append(f'<rect class="band" x="{x(a):.1f}" y="{T}" width="{x(e) - x(a):.1f}" height="{H - T - B}"/>')
        b.append(f'<text class="t2" x="{(x(a) + x(e)) / 2:.1f}" y="{T - 6}" font-size="11" text-anchor="middle">{label}</text>')
    pts = " ".join(f"{x(t):.1f},{y(v):.1f}" for t, v in series)
    b.append(f'<polyline class="s1l" points="{pts}" fill="none" stroke-width="2" stroke-linejoin="round"/>')
    for t in range(0, int(t1) + 1, 30):
        b.append(f'<text class="t2" x="{x(t):.1f}" y="{H - B + 20}" font-size="12" text-anchor="middle">{t} s</text>')
    b.append(f'<line class="axis" x1="{L}" x2="{W - R}" y1="{y(0):.1f}" y2="{y(0):.1f}" stroke-width="1"/>')
    open(os.path.join(OUT, "perf-migration-iops.svg"), "w").write(svg(W, H, "".join(b), title))


# per-second total IOPS (thousands), seconds 5-179 of the run; migrations at 33-51, 63-80, 92-110, 123-139 s
SERIES = [81, 98, 93, 103, 110, 119, 115, 120, 106, 112, 114, 115, 106, 111, 119, 119, 120, 120, 120, 120, 122, 121,
          121, 120, 118, 118, 118, 118, 118, 120, 118, 117, 126, 132, 119, 119, 121, 120, 119, 119, 119, 118, 93, 92, 90,
          91, 93, 91, 88, 93, 91, 91, 91, 93, 90, 94, 90, 90, 95, 92, 95, 91, 92, 88, 92, 89, 90, 91, 91, 89, 88, 83, 109,
          112, 119, 122, 122, 123, 123, 117, 108, 122, 124, 125, 124, 123, 114, 123, 121, 117, 118, 123, 125, 129, 124,
          132, 132, 132, 130, 127, 113, 87, 118, 92, 72, 91, 94, 92, 91, 94, 96, 96, 94, 96, 94, 94, 91, 93, 92, 91, 90, 92,
          95, 91, 92, 93, 92, 93, 86, 90, 87, 134, 128, 115, 128, 130, 130, 131, 131, 131, 130, 130, 131, 130, 128, 130,
          131, 130, 131, 131, 131, 130, 127, 129, 126, 125, 126, 126, 127, 127, 126, 127, 127, 127, 126, 116, 117, 116,
          117, 117, 117, 117, 117, 118, 121]
WINDOWS = [(33, 51, "02 → 01 · 69 ms"), (63, 80, "01 → 02 · 103 ms"), (92, 110, "02 → 01 · 105 ms"), (123, 139, "01 → 02 · 110 ms")]

if len(sys.argv) == 3:
    t0 = None
    ev = [l.split() for l in open(sys.argv[2]) if l[:1].isdigit()]
    iops = collections.defaultdict(int)
    for l in open(sys.argv[1]):
        t, v = [int(x) for x in l.split(",")[:2]]
        iops[t // 1000] += v
    # the fio log starts with the run; the events carry epoch seconds
    start = int(ev[0][2].split("=")[1]) - 33
    SERIES = [round(iops[s] / 1000) for s in sorted(iops) if s >= 5]
    WINDOWS = [(int(e[2].split("=")[1]) - start, int(e[3].split("=")[1]) - start,
                f"{e[1].replace('servidor', '').replace('->', ' → ')} · {e[5]} ms") for e in ev]

migration([(5 + i, v) for i, v in enumerate(SERIES)], WINDOWS)

hbars("perf-vm-iops.svg", "IOPS inside one VM on the FlashSystem 7600",
      "fio, 4 jobs, queue depth 32, O_DIRECT · one virtual disk · average latency 1.0 to 1.1 ms",
      [("Random read 4K", 118615), ("Random write 4K", 113456), ("Mixed 8K 70/30 (read + write)", 120241)], "IOPS")

hbars("perf-backup-read.svg", "Data read from the array per backup (VM with 140 GiB of disks)",
      "Backup from the array snapshot to Proxmox Backup Server · incremental reads only what changed",
      [("Full (first backup)", 140), ("Incremental, after live migration", 2.05), ("Incremental, during a load test", 4.05)], "GiB")

hbars("perf-snapshot-ops.svg", "Time of storage operations seen from Proxmox",
      "FlashSystem 7600, firmware 9.1.2 · volume group of the VM with two disks (20 + 120 GiB)",
      [("Create disk (100 GiB)", 2.7), ("Import cloud image (3 GiB)", 5.7), ("Snapshot of the VM (2 disks)", 2.7),
       ("Rollback to snapshot", 2.7), ("Delete snapshot", 1.7), ("Expand disk online", 2.3)], "s")
print("ok")
