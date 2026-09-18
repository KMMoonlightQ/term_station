from __future__ import annotations

import asyncio
from pathlib import Path

from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.geometry import Offset
from textual.widgets import Static, Tab, Tabs

from .daemon import Client
from .dialogs import ConfirmScreen, HelpScreen, NameScreen
from .model import Workspace, WorkspaceStore, WorkspaceTab
from .processes import foreground_commands
from .shortcuts import LAYOUT_KEYS, MODIFIER_KEYS, PREFIX_ACTIONS, PREFIX_ALIASES, PREFIX_KEYS
from .terminal import TerminalView
from .widgets import Dashboard, Panel

class TermStation(App, inherit_bindings=False):
    TITLE = "Term Station"
    CSS_PATH = "station.tcss"
    ENABLE_COMMAND_PALETTE = False
    BINDINGS = [
        Binding("ctrl+b", "prefix", "命令前缀", priority=True),
        Binding("escape", "leave_mode", "结束编辑", priority=True),
        *[Binding(key, f"prefix_command('{key}')", show=False, priority=True) for key in PREFIX_KEYS],
        *[Binding(key, f"layout_key('{key}')", show=False, priority=True) for key in LAYOUT_KEYS],
    ]

    def __init__(self, directory: Path, workspace: Workspace | None = None):
        super().__init__()
        # Native ANSI defaults are resolved by the host terminal, including live
        # palette changes. Do not convert them to Textual's fixed RGB palette.
        self.theme = "ansi-dark"
        self.store = WorkspaceStore(directory)
        self.workspace = workspace or self.store.load()
        self.client = Client(directory)
        self.active_panel = ""
        self.zoomed = ""
        self.layout_mode = False
        self.prefix_active = False
        self.save_timer = None
        self.connected = False
        self.daemon_mouse_supported: bool | None = None
        self.save_error = ""
        self.tab_lock = None
        self.updating_tabs = False

    def compose(self) -> ComposeResult:
        tab_row = Horizontal(id="tab-row")
        tab_row.display = len(self.workspace.tabs) >= 2
        with tab_row:
            yield Tabs(*[Tab(self.tab_label(tab, i), id=f"tab-{tab.id}") for i, tab in enumerate(self.workspace.tabs)],
                       active=f"tab-{self.workspace.active_tab}", id="workspace-tabs")
        with Dashboard():
            for component in self.workspace.current.components:
                yield Panel(component)
        yield Static("", id="command-bar", markup=False)

    @staticmethod
    def tab_label(tab: WorkspaceTab, index: int) -> Text:
        return Text(f"{index + 1} {tab.name}")

    async def on_mount(self) -> None:
        self.save_workspace()
        self.refresh_chrome()
        self.set_interval(1, self.refresh_chrome)
        self.focus_first_panel()
        self.run_worker(self.poll_loop(), group="daemon", exclusive=True)

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if len(self.screen_stack) > 1:
            return False
        if action == "prefix_command":
            return self.prefix_active
        if action == "layout_key":
            return self.layout_mode and bool(self.active_panel)
        if action == "leave_mode":
            return self.prefix_active or self.layout_mode or bool(self.zoomed)
        return True

    async def on_event(self, event: events.Event) -> None:
        if isinstance(event, events.Key) and not event.is_forwarded and event.key in {"ctrl+b", "escape"}:
            self.query_one(Dashboard).finish_drag()
        if isinstance(event, (events.MouseMove, events.MouseDown, events.MouseUp)) and not event.is_forwarded:
            dashboard = self.query_one(Dashboard)
            if len(self.screen_stack) == 1 and not isinstance(self.mouse_captured, TerminalView):
                if dashboard.handle_mouse(event):
                    self.mouse_position = Offset(event.screen_x, event.screen_y)
                    event.stop()
                    event.prevent_default()
                    return
            else:
                dashboard.show_drag_target(None)
        if isinstance(event, events.Key) and not event.is_forwarded and self.prefix_active and len(self.screen_stack) == 1:
            # Resolve prefixes in input order, before events enter widget queues.
            # A terminal may still be awaiting an earlier write to its PTY.
            event.stop()
            event.prevent_default()
            # Kitty's report-all-keys mode includes standalone Shift / Ctrl
            # presses. They prepare the next key, rather than consume it.
            if event.key in MODIFIER_KEYS:
                return
            if event.key == "ctrl+b":
                self.clear_prefix()
                await self.action_send_prefix()
            elif event.key == "escape":
                self.clear_prefix()
            else:
                await self.action_prefix_command(PREFIX_ALIASES.get(event.key, event.key))
            return
        await super().on_event(event)

    def action_prefix(self) -> None:
        if self.prefix_active:
            self.clear_prefix()
            self.run_worker(self.action_send_prefix())
            return
        self.prefix_active = True
        self.refresh_bindings()
        self.refresh_chrome()

    def clear_prefix(self) -> None:
        self.prefix_active = False
        self.refresh_bindings()
        self.refresh_chrome()

    async def action_prefix_command(self, key: str) -> None:
        self.clear_prefix()
        if key in "123456789" and len(key) == 1:
            index = int(key) - 1
            if index < len(self.workspace.tabs):
                self.query_one(Tabs).active = f"tab-{self.workspace.tabs[index].id}"
            return
        if key in PREFIX_ACTIONS:
            await self.run_action(PREFIX_ACTIONS[key])
        else:
            self.notify(f"未识别的前缀按键：{key!r}。请重新按 Ctrl+B 后选择命令。",
                        severity="warning")

    async def action_send_prefix(self) -> None:
        panel = self.current_panel()
        if panel and panel.component.kind == "terminal":
            await panel.query_one(TerminalView).send("\x02")

    def current_panel(self) -> Panel | None:
        return next((p for p in self.query(Panel) if p.component.id == self.active_panel), None)

    def focus_first_panel(self) -> None:
        panels = list(self.query(Panel))
        if panels:
            panels[0].focus_content()
        else:
            self.active_panel = ""
            self.query_one(Dashboard).focus()

    def action_next_panel(self) -> None:
        panels = list(self.query(Panel))
        if not panels:
            return
        current = next((i for i, panel in enumerate(panels) if panel.component.id == self.active_panel), -1)
        panel = panels[(current + 1) % len(panels)]
        if self.zoomed:
            self.zoomed = panel.component.id
            self.apply_zoom()
        panel.focus_content()

    def cycle_tab(self, step: int) -> None:
        index = next(i for i, tab in enumerate(self.workspace.tabs) if tab.id == self.workspace.active_tab)
        self.query_one(Tabs).active = f"tab-{self.workspace.tabs[(index+step) % len(self.workspace.tabs)].id}"

    def action_next_tab(self) -> None:
        self.cycle_tab(1)

    def action_previous_tab(self) -> None:
        self.cycle_tab(-1)

    async def on_tabs_tab_activated(self, event: Tabs.TabActivated) -> None:
        identifier = (event.tab.id or "").removeprefix("tab-")
        if self.updating_tabs or identifier not in {tab.id for tab in self.workspace.tabs}:
            return
        if self.tab_lock is None:
            self.tab_lock = asyncio.Lock()
        async with self.tab_lock:
            if identifier == self.workspace.active_tab:
                return
            self.workspace.active_tab = identifier
            await self.rebuild_dashboard()
            self.save_workspace()

    async def rebuild_dashboard(self) -> None:
        self.zoomed = ""
        dashboard = self.query_one(Dashboard)
        dashboard.show_drag_target(None)
        await dashboard.remove_children()
        await dashboard.mount_all(Panel(c) for c in self.workspace.current.components)
        dashboard.scroll_home(animate=False)
        dashboard.reflow()
        self.focus_first_panel()
        self.refresh_chrome()

    def action_new_tab(self) -> None:
        async def add(name):
            if not name:
                return
            tab = WorkspaceTab(name=name)
            tab.add("terminal", "主终端")
            tab.components[0].w = 12
            self.workspace.tabs.append(tab)
            tabs = self.query_one(Tabs)
            await tabs.add_tab(Tab(self.tab_label(tab, len(self.workspace.tabs)-1), id=f"tab-{tab.id}"))
            tabs.active = f"tab-{tab.id}"
            self.save_workspace()

        self.push_screen(NameScreen("新建 Tab", f"Tab {len(self.workspace.tabs)+1}"), add)

    def action_rename_tab(self) -> None:
        def rename(name):
            if name:
                tab = self.workspace.current
                tab.name = name
                self.query_one(f"#tab-{tab.id}", Tab).label = self.tab_label(tab, self.workspace.tabs.index(tab))
                self.save_workspace()

        self.push_screen(NameScreen("重命名 Tab", self.workspace.current.name), rename)

    async def action_add_component(self) -> None:
        source = self.current_panel()
        defaults = {}
        if source and source.component.kind == "terminal":
            defaults = {"cwd": source.component.cwd, "shell": source.component.shell}
        item = self.workspace.current.add("terminal", **defaults)
        self.layout_mode = False
        self.set_class(False, "editing")
        if self.zoomed:
            self.zoomed = ""
            self.apply_zoom()
        panel = Panel(item)
        await self.query_one(Dashboard).mount(panel)
        panel.focus_content()
        self.refresh_bindings()
        self.save_workspace()

    def action_layout(self) -> None:
        if self.query_one(Dashboard).compact:
            self.notify("请放大终端窗口后编辑网格布局（建议至少 100×32）")
            return
        if self.zoomed:
            self.zoomed = ""
            self.apply_zoom()
        self.layout_mode = not self.layout_mode
        self.set_class(self.layout_mode, "editing")
        panel = self.current_panel()
        if panel:
            panel.focus_content()
        self.refresh_bindings()
        self.refresh_chrome()

    def action_layout_key(self, key: str) -> None:
        panel = self.current_panel()
        if not panel:
            return
        item = panel.component
        dx, dy = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}[key.split("+")[-1]]
        if key.startswith("shift+"):
            self.workspace.current.place(item, item.x, item.y, min(12-item.x, item.w+dx), item.h+dy)
        else:
            self.workspace.current.place(item, item.x+dx, item.y+dy)
        self.query_one(Dashboard).reflow()
        self.save_workspace()

    def action_leave_mode(self) -> None:
        if self.prefix_active:
            self.clear_prefix()
        elif self.layout_mode:
            self.action_layout()
        elif self.zoomed:
            self.action_zoom()

    def apply_zoom(self) -> None:
        for panel in self.query(Panel):
            panel.display = not self.zoomed or self.zoomed == panel.component.id
        dashboard = self.query_one(Dashboard)
        dashboard.scroll_home(animate=False)
        dashboard.reflow()
        self.refresh_chrome()

    def action_zoom(self) -> None:
        if self.active_panel:
            if self.layout_mode:
                self.action_layout()
            self.zoomed = "" if self.zoomed else self.active_panel
            self.apply_zoom()

    async def action_remove_component(self) -> None:
        panel = self.current_panel()
        if panel is None:
            return
        item = panel.component

        try:
            if item.kind == "terminal":
                await self.client.call("kill", id=item.id)
            self.workspace.current.components.remove(item)
            self.zoomed = ""
            await panel.remove()
            self.apply_zoom()
            self.focus_first_panel()
            self.save_workspace()
        except (ConnectionError, ValueError) as error:
            self.notify(str(error), severity="error")

    async def action_remove_tab(self) -> None:
        tab = self.workspace.current
        try:
            for item in tab.components:
                if item.kind == "terminal":
                    await self.client.call("kill", id=item.id)
        except (ConnectionError, ValueError) as error:
            self.notify(str(error), severity="error")
            return
        self.workspace.tabs.remove(tab)
        if not self.workspace.tabs:
            self.workspace.tabs.append(WorkspaceTab())
        self.workspace.active_tab = self.workspace.tabs[0].id
        self.updating_tabs = True
        tabs = self.query_one(Tabs)
        await tabs.clear()
        for i, current in enumerate(self.workspace.tabs):
            await tabs.add_tab(Tab(self.tab_label(current, i), id=f"tab-{current.id}"))
        tabs.active = f"tab-{self.workspace.active_tab}"
        self.updating_tabs = False
        await self.rebuild_dashboard()
        self.save_workspace()

    def action_restart_shell(self) -> None:
        panel = self.current_panel()
        if panel is None or panel.component.kind != "terminal":
            self.notify("先选中一个终端组件")
            return
        item = panel.component

        async def restart(confirmed):
            if confirmed:
                try:
                    terminal = panel.query_one(TerminalView)
                    await self.client.call("kill", id=item.id)
                    terminal.ready = False
                    terminal.frame = {}
                    terminal.last_size = (0, 0)
                    terminal.error = "正在重启 Shell…"
                except (ConnectionError, ValueError) as error:
                    self.notify(str(error), severity="error")

        self.push_screen(ConfirmScreen("重启 Shell？", "将结束当前会话并启动新 Shell。"), restart)

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    async def action_detach(self) -> None:
        self.query_one(Dashboard).finish_drag(save=False)
        if not self.save_workspace():
            self.notify("配置保存失败，修复后再离开。", severity="error")
            return
        self.workers.cancel_group(self, "daemon")
        self.exit()

    def queue_save(self) -> None:
        if self.save_timer:
            self.save_timer.stop()
        self.save_timer = self.set_timer(0.35, self.save_workspace)

    def save_workspace(self) -> bool:
        if any(panel.dragging for panel in self.query(Panel)):
            return False
        try:
            self.store.save(self.workspace)
            self.save_error = ""
            self.refresh_chrome()
            return True
        except OSError as error:
            self.save_error = str(error)
            self.notify(f"配置保存失败：{error}", severity="error", timeout=10)
            self.refresh_chrome()
            return False

    def refresh_chrome(self) -> None:
        if not self.is_mounted:
            return
        self.query_one("#tab-row").display = len(self.workspace.tabs) >= 2
        self.set_class(self.prefix_active, "prefix-active")
        status = "旧后台不支持鼠标转发 · 保存各程序的工作后重启后台" if self.daemon_mouse_supported is False else ""
        self.query_one("#command-bar", Static).update("Ctrl+B › 等待命令" if self.prefix_active else status)
        for panel in self.query(Panel):
            panel.update_state()

    def check_mouse_support(self, frame: dict) -> None:
        # Earlier mouse-capable releases share ping version 1 with old daemons.
        # A zero tracking mode means an ordinary shell; an absent field means
        # this daemon cannot forward mouse input at all.
        supported = "mouse_tracking" in frame
        if supported == self.daemon_mouse_supported:
            return
        self.daemon_mouse_supported = supported
        if not supported:
            self.notify("当前后台不支持鼠标转发。请保存各程序的工作，再退出界面、停止后台并重新启动。"
                        "停止后台会结束所有终端会话；只重启当前 Shell 无法更新后台。",
                        title="需要更新后台", severity="warning", timeout=15)
        self.refresh_chrome()

    async def poll_loop(self) -> None:
        next_title_update = 0.0
        try:
            while True:
                try:
                    if not self.connected:
                        await self.client.connect()
                        await self.client.call("ping")
                        self.connected = True
                        self.daemon_mouse_supported = None
                        for view in self.query(TerminalView):
                            view.ready = False
                            view.frame = {}
                            view.last_size = (0, 0)
                        self.refresh_chrome()
                    views = list(self.query(TerminalView))
                    for view in views:
                        if not view.is_mounted:
                            continue
                        if not view.ready:
                            try:
                                await self.client.call("ensure", id=view.component.id, cwd=view.component.cwd, shell=view.component.shell)
                                view.ready = True
                            except ValueError as error:
                                view.error = str(error)
                                view.refresh()
                                continue
                        dimensions = (max(2, view.content_size.width), max(2, view.content_size.height))
                        if view.visible and view.content_size.width > 0 and view.last_size != dimensions:
                            await self.client.call("resize", id=view.component.id, columns=dimensions[0], rows=dimensions[1])
                            view.last_size = dimensions
                    specs = [{"id": v.component.id, "revision": v.frame.get("revision", -1), "offset": v.history_offset, "last_offset": v.last_offset}
                             for v in views if v.ready and v.is_mounted and v.visible]
                    if specs:
                        response = await self.client.call("snapshot", sessions=specs)
                        if response["frames"]:
                            self.check_mouse_support(response["frames"][0])
                        by_id = {v.component.id: v for v in views}
                        for frame in response["frames"]:
                            view = by_id.get(frame["id"])
                            if view and view.is_mounted:
                                view.apply_frame(frame)
                    if views and asyncio.get_running_loop().time() >= next_title_update:
                        sessions = (await self.client.call("list"))["sessions"]
                        visible_ids = {v.component.id for v in views if v.is_mounted and v.visible}
                        titles = await asyncio.to_thread(foreground_commands, [s for s in sessions if s["id"] in visible_ids])
                        # Tab changes can remove the captured views while ps runs.
                        for view in self.query(TerminalView):
                            if isinstance(view.parent, Panel) and view.component.id in titles:
                                view.command_title = titles[view.component.id]
                                view.parent.update_state()
                        next_title_update = asyncio.get_running_loop().time() + 0.4
                    await asyncio.sleep(0.06)
                except (ConnectionError, OSError) as error:
                    if self.connected:
                        self.notify(f"连接中断，正在重连：{error}", severity="warning")
                    self.connected = False
                    await self.client.close()
                    self.refresh_chrome()
                    await asyncio.sleep(1)
                except ValueError as error:
                    # A session may be removed while a viewport update is in flight.
                    for view in self.query(TerminalView):
                        view.ready = False
                    self.notify(str(error), severity="warning")
                    await asyncio.sleep(0.2)
        finally:
            await self.client.close()
