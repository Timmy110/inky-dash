"""`status`: device health, provider health, config errors (DESIGN.md §6.4)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, ClassVar, Literal

from inkydash import __version__, sysinfo
from inkydash.render.theme import BLACK, GREEN, RED, WHITE, YELLOW, new_canvas
from inkydash.render.widgets import Canvas
from inkydash.screens.base import Action, Frame, RenderContext, ScreenOptions


class StatusOptions(ScreenOptions):
    type: Literal["status"] = "status"


def _rescan(ctx: RenderContext, opts: StatusOptions) -> bool:
    for g in getattr(ctx.providers, "_galleries", {}).values():
        g.force_rescan()
    return True


class Status:
    name: ClassVar[str] = "status"
    Options: ClassVar[type[ScreenOptions]] = StatusOptions
    actions: ClassVar[dict[str, Action]] = {"rescan": Action("rescan", _rescan, "rescan")}

    def render(self, ctx: RenderContext, opts: StatusOptions) -> Frame:
        img = new_canvas(ctx.size)
        c = Canvas(img, ctx.theme)
        W, H = ctx.size
        errors: list[str] = list(ctx.view.get("errors") or [])

        # header
        c.rect((0, 0, W, 44), fill=BLACK)
        c.text((14, 22), "inky-dash", "bold", 24, WHITE, anchor="lm")
        c.text((W - 14, 22), f"v{__version__}", "ui", 16, WHITE, anchor="rm")

        rows: list[tuple[str, str, int]] = []
        ip = sysinfo.ip_address()
        rows.append((ctx.t("status.host"), f"{sysinfo.hostname()}  {ip or '–'}", BLACK))
        ssid, q = sysinfo.wifi()
        if ssid or q is not None:
            rows.append((ctx.t("status.wifi"), f"{ssid or '?'}  {q if q is not None else '?'}%",
                         RED if q is not None and q < 30 else BLACK))
        up = sysinfo.uptime_s()
        if up is not None:
            rows.append((ctx.t("status.uptime"), sysinfo.human_duration(up), BLACK))
        synced = sysinfo.clock_synced()
        if synced is not None:
            rows.append((ctx.t("status.clock"), ctx.t("yes") if synced else ctx.t("status.not_synced"),
                         BLACK if synced else RED))
        df = sysinfo.disk_free(ctx.store.root if ctx.store else ".")
        if df:
            free, total = df
            rows.append((ctx.t("status.disk"), f"{sysinfo.human_bytes(free)} / {sysinfo.human_bytes(total)}",
                         RED if free < 200 * 2**20 else BLACK))
        temp = sysinfo.cpu_temp_c()
        if temp is not None:
            rows.append((ctx.t("status.cpu"), f"{temp:.0f} °C", RED if temp > 75 else BLACK))
        mem = sysinfo.mem_available_mb()
        if mem is not None:
            rows.append((ctx.t("status.memory"), f"{mem} MB {ctx.t('status.free')}", BLACK))
        for line in ctx.view.get("schedule") or []:
            rows.append(line)

        landscape = ctx.landscape
        col_w = W // 2 - 20 if landscape else W - 28
        y = 58
        for label, value, color in rows:
            c.text((14, y), label, "ui", 15, BLACK)
            c.text_fit((14 + 110, y), value, "bold", col_w - 110, 15, 11, color)
            y += 24

        # providers
        px, py = (W // 2 + 6, 58) if landscape else (14, y + 12)
        c.text((px, py), ctx.t("status.providers"), "bold", 17, BLACK)
        py += 26
        usage = ctx.providers.usage
        names = sorted(set(usage.last_ok) | set(usage.last_error) | {
            p for d in usage.data.values() for p in d})
        if not names:
            c.text((px, py), ctx.t("status.no_calls"), "ui", 15, BLACK)
            py += 22
        for name in names[:9]:
            err = usage.last_error.get(name)
            ok = usage.last_ok.get(name)
            dot = RED if err else GREEN if ok else YELLOW
            c.d.ellipse((px, py + 3, px + 12, py + 15), fill=dot, outline=BLACK)
            detail = f"{usage.today(name)} {ctx.t('status.today')}"
            if ok:
                detail += f" · {ctx.fmt.time(ok)}"
            c.text((px + 20, py), name, "bold", 15, BLACK)
            c.text_fit((px + 130, py), detail, "ui", (W - 14 if landscape else W - 14) - px - 130, 15, 11, BLACK)
            py += 22

        # errors
        if errors:
            top = max(y, py) + 10
            box = (10, top, W - 10, H - 30)
            if box[3] - box[1] > 40:
                c.rect(box, fill=WHITE, outline=RED, width=3)
                c.text((22, top + 10), ctx.t("status.errors"), "bold", 17, RED)
                yy = top + 36
                for err in errors:
                    yy = c.paragraph((22, yy), err, "ui", 14, W - 44, 3)
                    if yy > box[3] - 18:
                        break

        footer = c.footer(f"{ctx.t('updated')} {ctx.fmt.time(ctx.now)}")
        return Frame(img, volatile=[footer])


def describe_schedule(ctx_now: datetime, decision: Any, nxt: datetime | None, fmt: Any) -> list[tuple[str, str, int]]:
    rows = [(fmt.t("status.showing"), f"{decision.screen} ({decision.rule or 'default'})", BLACK)]
    if nxt:
        rows.append((fmt.t("status.next"), fmt.time(nxt.astimezone(UTC)), BLACK))
    return rows
