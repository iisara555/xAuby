# xauby.ui package initialization

from xauby.ui.widgets import (
    print_row, pad_line, format_panel_header, format_panel_divider,
    get_zone_colored, format_pnl, format_pnl_compact, to_compact,
    make_zone_badge, compute_trade_stats, get_quote_asset
)

from xauby.ui.chart import (
    get_chart_lines, get_chart_lines_raw, get_zone_distribution,
    get_bar_chart_lines, get_capital_curve_lines
)

from xauby.ui.system import (
    get_ram_usage, get_cpu_times, calculate_cpu_usage, get_db_size, format_uptime
)

from xauby.ui.dashboard import (
    get_checklist_lines, get_performance_lines,
    get_layout_profile, resolve_dashboard_state,
)

from xauby.ui.menu import (
    print_line, print_menu_row, draw_hermes_status_bar, draw_hermes_banner
)
