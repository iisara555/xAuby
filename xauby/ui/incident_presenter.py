"""Incident explorer state and data loading for Textual TUI."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from xauby.observability.incidents import (
    cycle_timeline_filter,
    filter_timeline_events,
    format_filter_status,
    format_timeline_line,
    is_notable_event,
    list_runs,
    load_run_timeline,
    summarize_run,
)
from xauby.observability.replay_validation import ReplayValidationReport, validate_run
from xauby.observability.store import EventStore
from xauby.storage.interface import IDatabaseRepository
from xauby.utils.colors import C_BOLD, C_GREEN, C_MUTED, C_PRIMARY, C_RED, C_RESET, C_YELLOW, make_gemini_gradient
from xauby.utils.common import format_ts_ict
from xauby.ui.widgets import pad_line


@dataclass
class IncidentExplorerState:
    runs: List[Dict[str, Any]] = field(default_factory=list)
    runs_cache_key: Optional[Tuple[Optional[str], bool]] = None
    runs_cache_time: float = 0.0
    selected_idx: int = 0
    timeline_filter: str = "notable"
    scroll: int = 0
    validation_cache: Dict[str, Tuple[float, ReplayValidationReport]] = field(
        default_factory=dict
    )
    validation_pending_run: str = ""

    def selected_run_id(self) -> str:
        if self.runs and 0 <= self.selected_idx < len(self.runs):
            return str(self.runs[self.selected_idx].get("run_id") or "")
        return ""

    @property
    def notable_only(self) -> bool:
        return self.timeline_filter == "notable"

    @notable_only.setter
    def notable_only(self, value: bool) -> None:
        self.timeline_filter = "notable" if value else "all"

    def reset(self) -> None:
        self.runs = []
        self.runs_cache_key = None
        self.selected_idx = 0
        self.scroll = 0
        self.validation_cache.clear()
        self.validation_pending_run = ""
        self.runs_cache_time = 0.0


_store: Optional[EventStore] = None
_store_lock = threading.Lock()
_VALIDATION_CACHE_TTL = 300


def render_validation_lines(report: Optional[ReplayValidationReport], W: int) -> List[str]:
    if report is None:
        return [f"  {C_MUTED}Press v — replay validation + event coverage{C_RESET}"]
    lines: List[str] = []
    cov = report.coverage
    if cov:
        cov_color = C_GREEN if cov.integrity_ok else C_YELLOW
        lines.append(
            f"  {cov_color}COVERAGE{C_RESET}  "
            f"{cov.wired_seen}/{cov.wired_total} types ({cov.coverage_pct:.0f}%)  "
            f"integrity={'OK' if cov.integrity_ok else 'ISSUES'}"
        )
        if cov.integrity_issues:
            lines.append(
                f"  {C_YELLOW}{', '.join(cov.integrity_issues[:3])}{C_RESET}"
            )
        elif cov.wired_missing and W >= 70:
            missing = ", ".join(cov.wired_missing[:4])
            lines.append(f"  {C_MUTED}not seen: {missing}{C_RESET}")
    checked = report.matched + report.mismatched
    if checked == 0:
        lines.append(f"  {C_MUTED}No signals to replay (skipped={report.skipped}){C_RESET}")
        return lines
    badge_color = C_GREEN if report.mismatched == 0 else C_YELLOW
    lines.append(
        f"  {badge_color}{'SIGNALS OK' if report.mismatched == 0 else 'SIGNAL MISMATCH'}{C_RESET}  "
        f"match {report.matched}/{checked} ({report.match_rate_pct:.0f}%)  "
        f"skipped={report.skipped}"
    )
    for m in report.mismatches[:2 if W < 75 else 3]:
        ts = format_ts_ict(m.ts, fmt="%H:%M:%S")
        reason = m.live_reason[: max(12, W - 52)]
        lines.append(
            f"  {C_MUTED}{ts}{C_RESET} #{m.seq} "
            f"{C_YELLOW}{m.live_action}{C_RESET}→{C_RED}{m.replay_action}{C_RESET}  {reason}"
        )
    if len(report.mismatches) > 3:
        lines.append(
            f"  {C_MUTED}+{len(report.mismatches) - 3} mismatches — "
            f"scripts/replay_validate.py {report.run_id}{C_RESET}"
        )
    return lines


def _payload(ev: Dict[str, Any]) -> Dict[str, Any]:
    payload = ev.get("payload")
    return payload if isinstance(payload, dict) else {}


def _severity_for_event(ev: Dict[str, Any]) -> Tuple[str, str]:
    etype = str(ev.get("event_type") or ev.get("event") or "").lower()
    payload = _payload(ev)
    text = " ".join(
        str(x or "")
        for x in (
            payload.get("error"),
            payload.get("reason"),
            payload.get("trigger"),
            ev.get("error"),
            ev.get("reason"),
        )
    ).lower()
    if etype in {"order_failed", "risk_rejected"} or "critical" in text:
        return "CRITICAL", C_RED
    if etype in {"engine_stopped", "stop_loss_triggered"}:
        return "WARN", C_YELLOW
    if etype in {"position_opened", "position_closed", "order_filled"}:
        return "TRADE", C_GREEN
    if etype in {"stop_loss_updated", "regime_changed", "strategy_changed"}:
        return "INFO", C_PRIMARY
    return "OK", C_MUTED


def _event_symbol(ev: Dict[str, Any], fallback: str = "") -> str:
    payload = _payload(ev)
    return str(ev.get("symbol") or payload.get("symbol") or fallback or "").upper()


def _event_error_code(ev: Dict[str, Any]) -> str:
    payload = _payload(ev)
    code = payload.get("code") if payload.get("code") is not None else ev.get("code")
    return str(code or "")


def _event_detail(ev: Dict[str, Any], width: int) -> str:
    payload = _payload(ev)
    etype = str(ev.get("event_type") or ev.get("event") or "?")
    for key in ("error", "reason", "trigger", "action", "side", "status"):
        val = payload.get(key)
        if val is not None:
            detail = f"{etype}: {val}"
            return detail[: max(24, width)]
    return etype[: max(24, width)]


def _recommended_action(events: List[Dict[str, Any]]) -> str:
    for ev in reversed(events):
        etype = str(ev.get("event_type") or ev.get("event") or "").lower()
        payload = _payload(ev)
        code = _event_error_code(ev)
        err = str(payload.get("error") or payload.get("reason") or "").lower()
        if etype == "order_failed":
            if code == "-4023" or "step size" in err:
                return "Fix order quantity filters, then restart engine once."
            if code == "-2018" or "balance is insufficient" in err:
                return "Check available balance, fee dust, and SL/order quantity."
            return "Inspect latest order error and verify exchange order state."
        if etype == "risk_rejected":
            return "Review risk block before expecting a new entry."
        if etype == "stop_loss_triggered":
            return "Confirm position close and check remaining dust."
        if etype == "stop_loss_updated":
            return "Monitor the protected position and verify SL order if live."
        if etype == "position_opened":
            return "Verify exchange-side SL exists for the open position."
        if etype == "position_closed":
            return "Review PnL and confirm DB is idle for this pair."
    return "No immediate action from this run."


def render_incident_summary_lines(
    events: List[Dict[str, Any]],
    summary: Dict[str, Any],
    width: int,
) -> List[str]:
    w = max(38, width)
    if not events:
        return [f"  {C_MUTED}No events loaded for the selected run.{C_RESET}"]

    latest = events[-1]
    notable = [ev for ev in events if is_notable_event(ev)]
    latest_notable = notable[-1] if notable else latest
    severity, color = _severity_for_event(latest_notable)
    types = summary.get("types") or {}
    failed = int(types.get("order_failed") or 0)
    opened = int(types.get("position_opened") or 0)
    closed = int(types.get("position_closed") or 0)
    rejected = int(types.get("risk_rejected") or 0)
    symbol = _event_symbol(latest_notable, str(summary.get("symbol") or ""))
    started = format_ts_ict(summary.get("started_at") or "", fmt="%m-%d %H:%M")
    ended = format_ts_ict(summary.get("ended_at") or "", fmt="%H:%M")

    lines = [
        f"  {color}{severity}{C_RESET}  {C_BOLD}{symbol or 'ALL'}{C_RESET}  "
        f"{C_MUTED}{started}->{ended} · {summary.get('total', 0)} events · "
        f"{summary.get('notable', 0)} notable{C_RESET}",
    ]
    counts = []
    if failed:
        counts.append(f"{C_RED}failed {failed}{C_RESET}")
    if rejected:
        counts.append(f"{C_YELLOW}risk {rejected}{C_RESET}")
    if opened:
        counts.append(f"{C_GREEN}opened {opened}{C_RESET}")
    if closed:
        counts.append(f"{C_GREEN}closed {closed}{C_RESET}")
    if counts:
        lines.append("  " + "  ".join(counts))

    detail_room = max(24, w - 18)
    lines.append(
        f"  {C_BOLD}Latest:{C_RESET} "
        f"{_event_detail(latest_notable, detail_room)}"
    )
    lines.append(
        f"  {C_BOLD}Next:{C_RESET} {_recommended_action(events)[:detail_room]}"
    )

    repeated = [
        (etype, count)
        for etype, count in sorted(types.items(), key=lambda item: item[1], reverse=True)
        if count >= 3 and etype not in {"tick", "heartbeat", "signal_evaluated"}
    ]
    if repeated:
        compact = ", ".join(f"{etype} x{count}" for etype, count in repeated[:3])
        lines.append(f"  {C_MUTED}Repeated: {compact}{C_RESET}")
    return lines


def get_event_store(db: Optional[IDatabaseRepository] = None) -> EventStore:
    global _store
    with _store_lock:
        from xauby.ui.textual_tui.capabilities import is_read_only_mode

        readonly = is_read_only_mode()
        if _store is None or (readonly and not getattr(_store, "_readonly", False)):
            _store = EventStore(db=db, readonly=readonly)
        return _store


def reset_explorer_state() -> None:
    """Full reset (refresh key / explicit reload)."""
    with _default_state_lock:
        _default_state.reset()


def prepare_incident_screen() -> None:
    """Light reset when opening the incidents screen — keep cache, refresh runs."""
    with _default_state_lock:
        st = get_explorer_state()
        st.scroll = 0
        st.runs_cache_time = 0.0


_default_state = IncidentExplorerState()
_default_state_lock = threading.Lock()


def get_explorer_state() -> IncidentExplorerState:
    return _default_state


def _get_cached_validation(
    state: IncidentExplorerState, run_id: str
) -> Optional[ReplayValidationReport]:
    entry = state.validation_cache.get(run_id)
    if entry is None:
        return None
    ts, report = entry
    if time.time() - ts > _VALIDATION_CACHE_TTL:
        state.validation_cache.pop(run_id, None)
        return None
    return report


def _prune_validation_cache(state: IncidentExplorerState, max_entries: int = 100) -> None:
    if len(state.validation_cache) <= max_entries:
        return
    sorted_items = sorted(state.validation_cache.items(), key=lambda x: x[1][0])
    to_evict = len(sorted_items) - max_entries
    for k, _ in sorted_items[:to_evict]:
        del state.validation_cache[k]


def run_validation(state: IncidentExplorerState, db: IDatabaseRepository, run_id: str) -> None:
    if not run_id:
        return
    store = get_event_store()
    report = validate_run(store, db, run_id, max_signals=100)
    state.validation_cache[run_id] = (time.time(), report)
    _prune_validation_cache(state)


def refresh_runs(
    state: IncidentExplorerState,
    *,
    symbol: Optional[str],
    is_multi: bool,
    current_run_id: str = "",
    force: bool = False,
) -> None:
    store = get_event_store()
    cache_key = (symbol, is_multi)
    is_expired = time.time() - state.runs_cache_time > 10.0
    if state.runs and state.runs_cache_key == cache_key and not is_expired and not force:
        return

    selected_run = state.selected_run_id()
    state.runs = list_runs(store, limit=15, symbol=symbol)
    state.runs_cache_key = cache_key
    state.runs_cache_time = time.time()

    found_idx = -1
    target_run = selected_run or current_run_id
    if target_run:
        for i, row in enumerate(state.runs):
            if row.get("run_id") == target_run:
                found_idx = i
                break
    if found_idx != -1:
        state.selected_idx = found_idx
    else:
        state.selected_idx = (
            min(state.selected_idx, len(state.runs) - 1) if state.runs else 0
        )


def format_run_label(
    row: Dict[str, Any],
    *,
    selected: bool,
    is_live_run: bool,
    narrow: bool,
) -> str:
    rid = str(row.get("run_id", "?"))
    marker = "▶" if selected else " "
    started = format_ts_ict(row.get("started_at") or "", fmt="%m-%d %H:%M")
    ended = format_ts_ict(row.get("ended_at") or "", fmt="%H:%M")
    count = int(row.get("event_count") or 0)
    mode = row.get("mode") or "?"
    rid_show = rid if not narrow else rid[:8]
    live_tag = " [LIVE]" if is_live_run else ""
    return f"{marker} {rid_show}{live_tag}  {started}→{ended}  {count} ev  {mode}"


def format_run_label_ansi(
    row: Dict[str, Any],
    *,
    selected: bool,
    is_live_run: bool,
    narrow: bool,
    compact: bool = False,
) -> str:
    """ANSI-colored run row (avoids Rich markup nesting bugs in Textual)."""
    line = format_run_label(
        row, selected=selected, is_live_run=is_live_run, narrow=narrow or compact
    )
    if selected:
        return f"{C_BOLD}{C_YELLOW}{line}{C_RESET}"
    if is_live_run:
        return f"{C_GREEN}{line}{C_RESET}"
    return f"{C_MUTED}{line}{C_RESET}"


@dataclass
class IncidentViewModel:
    state: IncidentExplorerState
    runs: List[Dict[str, Any]]
    selected_run_id: str
    timeline_events: List[Dict[str, Any]]
    timeline_summary: Dict[str, Any]
    validation_lines: List[str]
    scroll: int
    timeline_filter: str
    timeline_total_count: int
    timeline_filtered_count: int
    filter_status_line: str
    pair_label: str
    is_multi: bool
    summary_lines: List[str] = field(default_factory=list)
    header_lines: List[str] = field(default_factory=list)
    help_line: str = ""
    live_run_id: str = ""


def format_incident_header_lines(
    *,
    pair_label: str,
    run_count: int,
    width: int,
    is_multi: bool,
) -> List[str]:
    w = max(38, width)
    lines = [
        pad_line(make_gemini_gradient("✦ INCIDENT EXPLORER ✦"), w),
    ]
    scope = "ALL PAIRS" if is_multi else (pair_label or "—")
    lines.append(
        pad_line(
            f" {C_BOLD}{C_PRIMARY}Scope:{C_RESET} {scope}  "
            f"{C_MUTED}│{C_RESET}  {C_MUTED}{run_count} run(s) in store{C_RESET}",
            w,
        )
    )
    return lines


def build_incident_view_model(
    db: IDatabaseRepository,
    bot_state: Dict[str, Any],
    width: int,
    height: int,
    explorer_state: IncidentExplorerState,
    *,
    focus_symbol: Optional[str] = None,
    is_multi: bool = False,
) -> IncidentViewModel:
    # Seed the singleton with the app's read-only DB before refresh_runs() asks
    # for it. Hosted observer mode must not initialise schema or a JSONL dir.
    get_event_store(db)
    symbol = None if is_multi else (focus_symbol or bot_state.get("symbol"))
    current_run_id = str(bot_state.get("run_id") or "")
    pair_label = (focus_symbol or bot_state.get("symbol") or "").upper().replace("_", "")

    if explorer_state.validation_pending_run:
        run_validation(explorer_state, db, explorer_state.validation_pending_run)
        explorer_state.validation_pending_run = ""

    refresh_runs(
        explorer_state,
        symbol=symbol,
        is_multi=is_multi,
        current_run_id=current_run_id,
    )

    run_id = explorer_state.selected_run_id() or current_run_id
    store = get_event_store()
    events: List[Dict[str, Any]] = []
    summary: Dict[str, Any] = {}
    timeline_total = 0
    timeline_filtered = 0
    filter_mode = explorer_state.timeline_filter or "notable"
    if run_id:
        all_events = load_run_timeline(store, run_id, limit=500, filter_mode="all")
        timeline_total = len(all_events)
        filtered_chrono = filter_timeline_events(all_events, filter_mode)
        timeline_filtered = len(filtered_chrono)
        events = list(reversed(filtered_chrono)) if filtered_chrono else []
        summary = summarize_run(all_events)
    else:
        all_events = []

    timeline_rows = max(4, height - 24)
    max_scroll = max(0, len(events) - timeline_rows)
    explorer_state.scroll = min(explorer_state.scroll, max_scroll)

    report = _get_cached_validation(explorer_state, run_id) if run_id else None
    validation_lines = render_validation_lines(report, max(38, width))
    summary_lines = render_incident_summary_lines(
        all_events,
        summary,
        max(38, width),
    )

    header_lines = format_incident_header_lines(
        pair_label=pair_label,
        run_count=len(explorer_state.runs),
        width=width,
        is_multi=is_multi,
    )
    filter_status = format_filter_status(
        filter_mode,
        shown=timeline_filtered,
        total=timeline_total,
    )
    if run_id:
        header_lines.append(pad_line(f" {filter_status}", max(38, width)))
    help_line = (
        " j/k runs · ↑↓ timeline · [ ] pair · a cycle filter · v validate · r refresh"
        if width >= 60
        else " j/k run · ↑↓ scr · [ ] pair · a filter"
    )

    return IncidentViewModel(
        state=explorer_state,
        runs=explorer_state.runs,
        selected_run_id=run_id,
        live_run_id=current_run_id,
        timeline_events=events,
        timeline_summary=summary,
        validation_lines=validation_lines,
        scroll=explorer_state.scroll,
        timeline_filter=filter_mode,
        timeline_total_count=timeline_total,
        timeline_filtered_count=timeline_filtered,
        filter_status_line=filter_status,
        pair_label=pair_label,
        is_multi=is_multi,
        summary_lines=summary_lines,
        header_lines=header_lines,
        help_line=help_line,
    )


def format_timeline_display_lines(
    events: List[Dict[str, Any]],
    width: int,
    scroll: int,
    max_rows: int,
    *,
    is_mobile: bool,
) -> Tuple[List[str], str]:
    if not events:
        return [], ""
    max_scroll = max(0, len(events) - max_rows)
    scroll = min(scroll, max_scroll)
    visible = events[scroll : scroll + max_rows]
    lines: List[str] = []
    for ev in visible:
        raw = format_timeline_line(ev, width=width - 6, show_tick=not is_mobile)
        lines.append(raw)
    footer = ""
    if len(events) > max_rows:
        footer = (
            f"latest at top · {scroll + 1}-{scroll + len(visible)} of {len(events)} "
            f"(↓ older · ↑ newer)"
        )
    return lines, footer


def handle_explorer_action(state: IncidentExplorerState, action: str) -> bool:
    """Apply explorer key action. Returns True if view should refresh."""
    if action in ("j", "n"):
        if state.runs and state.selected_idx < len(state.runs) - 1:
            state.selected_idx += 1
            state.scroll = 0
            return True
    elif action in ("k", "p"):
        if state.selected_idx > 0:
            state.selected_idx -= 1
            state.scroll = 0
            return True
    elif action == "a":
        state.timeline_filter = cycle_timeline_filter(state.timeline_filter)
        state.scroll = 0
        return True
    elif action == "r":
        state.runs = []
        state.scroll = 0
        state.validation_cache.clear()
        return True
    elif action == "v":
        state.validation_pending_run = state.selected_run_id()
        return True
    elif action in ("+", "=", "up", "pageup"):
        state.scroll = max(0, state.scroll - 3)
        return True
    elif action in ("-", "_", "down", "pagedown"):
        state.scroll += 3
        return True
    return False
