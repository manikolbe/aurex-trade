"""End-to-end BacktestRunner test for the Ciby Sliding Grid strategy.

Drives the full strategy → runner → simulated broker path (including STOP and
LIMIT resting orders) over synthetic trending bars, with no network or DB access.
"""

from datetime import UTC, datetime, timedelta

from aurex_trade.adapters.backtest.broker import SimulatedBrokerAdapter
from aurex_trade.adapters.backtest.market_data import HistoricalMarketDataAdapter
from aurex_trade.adapters.memory.repository import InMemoryRepository
from aurex_trade.backtest.config import BacktestConfig
from aurex_trade.backtest.runner import BacktestRunner
from aurex_trade.domain.models import BarData
from aurex_trade.domain.risk.engine import RiskEngine
from aurex_trade.domain.strategy.ciby_sliding_grid import CibySlidingGridStrategy

BAR_COUNT = 50


def _trending_bars(symbol: str = "XAU_USD") -> list[BarData]:
    """Anchor at 4100, then a steady climb to ~4150 and back down to ~4080.

    The up-leg should trigger BUY stops and SELL limits above the anchor; the
    down-leg should trigger SELL stops and BUY limits below it.
    """
    anchor = 4100.0
    padding = [anchor] * BAR_COUNT
    up = [anchor + i for i in range(1, 51)]  # 4101 → 4150
    down = [4150.0 - i for i in range(1, 71)]  # 4149 → 4080
    closes = padding + up + down

    bars: list[BarData] = []
    start = datetime(2025, 6, 1, 10, 0, tzinfo=UTC)
    for i, close in enumerate(closes):
        bars.append(
            BarData(
                timestamp=start + timedelta(minutes=i),
                open=close,
                high=close + 0.5,
                low=close - 0.5,
                close=close,
                volume=100.0,
                symbol=symbol,
            )
        )
    return bars


def _run() -> tuple[object, CibySlidingGridStrategy]:
    bars = _trending_bars()
    strategy = CibySlidingGridStrategy(
        grid_spacing=10.0,
        anchor_gap=15.0,
        buy_sell_offset=0.90,
        anchor_units=10.0,
        grid_units=20.0,
        stop_buffer=1.0,
        session_profit_target=100000.0,  # high so the session doesn't recycle
        session_loss_limit=100000.0,
        daily_loss_limit=100000.0,
    )
    config = BacktestConfig(
        symbol="XAU_USD",
        initial_capital=100_000.0,
        spread_pips=0.2,
        slippage_pips=0.0,
        bar_count=BAR_COUNT,
    )
    risk = RiskEngine(
        max_position_size=1000,
        max_daily_loss=50000.0,
        max_trades_per_day=10000,
        enabled=False,  # Grid strategies manage their own risk
    )
    broker = SimulatedBrokerAdapter(
        initial_capital=config.initial_capital,
        spread=config.spread_pips,
        slippage=config.slippage_pips,
        seed=config.deterministic_seed,
        grid_mode=True,
    )
    runner = BacktestRunner(
        strategy=strategy,
        risk_engine=risk,
        market_data=HistoricalMarketDataAdapter(bars, BAR_COUNT),
        broker=broker,
        repository=InMemoryRepository(),
        config=config,
        user_id="test",
    )
    result = runner.run()
    return result, strategy


class TestSlidingGridEndToEnd:
    def test_runs_and_places_trades(self) -> None:
        result, strategy = _run()
        # The grid should have set an anchor and traded as price moved.
        assert strategy._anchor_price is not None
        assert result.metrics.trade_count > 0

    def test_window_slides_with_trend(self) -> None:
        _result, strategy = _run()
        # By the end of a 50-point climb, levels well above the initial window
        # should have been reached (the window slid up with the trend).
        reached = set(strategy._filled) | set(strategy._stopped) | strategy._retired
        assert any(level >= 4135.0 for level in reached)

    def test_trims_trailing_levels_for_margin(self) -> None:
        _result, strategy = _run()
        # The 50-point climb opens many above-anchor levels; the cap of 2 means
        # trailing ones must have been retired (closed) to free margin.
        assert len(strategy._retired) > 0
        # The anchor is never retired.
        assert strategy._anchor_price is not None
        assert round(strategy._anchor_price, 2) not in strategy._retired

    def test_active_levels_respect_caps(self) -> None:
        _result, strategy = _run()
        # At the end of the run, active levels above/below the anchor must not
        # exceed the caps (2 ahead + 1 behind), excluding the anchor itself.
        anchor = round(strategy._anchor_price or 0.0, 2)
        active = [lv for lv, sides in strategy._filled.items() if sides and lv != anchor]
        above = [lv for lv in active if lv > anchor]
        below = [lv for lv in active if lv < anchor]
        # Whichever direction price ended, neither side exceeds max_levels_ahead.
        assert len(above) <= 2
        assert len(below) <= 2

    def test_completes_without_error(self) -> None:
        result, _strategy = _run()
        # A clean run yields finite final capital.
        assert result.metrics.final_capital > 0
