"""Tests for BacktestRunner grid mode — limit orders, callbacks, close_all."""

from collections import deque
from datetime import UTC, datetime, timedelta

from aurex_trade.adapters.backtest.broker import SimulatedBrokerAdapter
from aurex_trade.adapters.backtest.market_data import HistoricalMarketDataAdapter
from aurex_trade.adapters.memory.repository import InMemoryRepository
from aurex_trade.backtest.config import BacktestConfig
from aurex_trade.backtest.runner import BacktestRunner
from aurex_trade.domain.enums import SignalType
from aurex_trade.domain.models import BarData, Signal
from aurex_trade.domain.risk.engine import RiskEngine

BAR_COUNT = 50  # HistoricalMarketDataAdapter cursor starts at this


def _make_bars(
    prices: list[tuple[float, float, float]],
    symbol: str = "XAU_USD",
) -> list[BarData]:
    """Create bars from (close, low, high) tuples.

    Pads with flat bars BEFORE the real prices so that:
    - Total bars >= BAR_COUNT (adapter requirement)
    - Cursor starts at BAR_COUNT, meaning real prices start being iterated
    """
    # We need BAR_COUNT + len(prices) bars so the real prices are all iterable steps
    first = prices[0]
    padding_count = BAR_COUNT
    padded = [(first[0], first[1], first[2])] * padding_count + prices

    bars = []
    start = datetime(2025, 6, 1, 10, 0, tzinfo=UTC)
    for i, (close, low, high) in enumerate(padded):
        bars.append(
            BarData(
                timestamp=start + timedelta(minutes=i),
                open=close,
                high=high,
                low=low,
                close=close,
                volume=100.0,
                symbol=symbol,
            )
        )
    return bars


class FakeGridStrategy:
    """Minimal grid strategy for testing runner orchestration.

    Places a BUY LIMIT at anchor - spacing, SELL LIMIT at anchor + spacing.
    On fill, places opposite market order (handled by runner).
    On session profit, signals FLAT/close_all.
    """

    def __init__(
        self,
        grid_spacing: float = 10.0,
        units: float = 10.0,
        session_profit_target: float = 50.0,
    ) -> None:
        self._spacing = grid_spacing
        self._units = units
        self._profit_target = session_profit_target
        self._anchor: float | None = None
        self._signal_queue: deque[Signal] = deque()
        self._session_unrealized: float = 0.0
        self._session_realized: float = 0.0
        self._fills: list[tuple[str, float]] = []
        self._closures: list[tuple[str, float]] = []
        self._close_all_count: int = 0
        self._session_active: bool = True

    @property
    def name(self) -> str:
        return "fake_grid"

    @property
    def min_bars(self) -> int:
        return 1

    def update_unrealized_pnl(self, unrealized_pnl: float) -> None:
        self._session_unrealized = unrealized_pnl

    def generate(self, bars: list[BarData]) -> Signal | None:
        # Drain queue first
        if self._signal_queue:
            return self._signal_queue.popleft()

        if not self._session_active:
            return None

        # Check session target
        total_pnl = self._session_realized + self._session_unrealized
        if self._anchor is not None and total_pnl >= self._profit_target:
            self._session_active = False
            return Signal(
                symbol=bars[-1].symbol,
                signal_type=SignalType.FLAT,
                strategy_name=self.name,
                strength=1.0,
                metadata={"action": "close_all", "reason": "session_profit_target"},
            )

        # Initialize: place limits
        if self._anchor is None:
            self._anchor = bars[-1].close
            buy_level = self._anchor - self._spacing
            sell_level = self._anchor + self._spacing

            buy_key = f"{buy_level:.2f}_long"
            sell_key = f"{sell_level:.2f}_short"

            self._signal_queue.append(
                Signal(
                    symbol=bars[-1].symbol,
                    signal_type=SignalType.LONG,
                    strategy_name=self.name,
                    strength=1.0,
                    stop_loss=buy_level - self._spacing,
                    metadata={
                        "order_type": "LIMIT",
                        "limit_price": f"{buy_level:.5f}",
                        "grid_level": buy_key,
                        "fixed_units": f"{self._units:.1f}",
                        "opposite_side": "SELL",
                        "opposite_grid_level": f"{buy_level:.2f}_short",
                        "opposite_stop_loss": f"{buy_level + self._spacing:.5f}",
                    },
                )
            )
            self._signal_queue.append(
                Signal(
                    symbol=bars[-1].symbol,
                    signal_type=SignalType.SHORT,
                    strategy_name=self.name,
                    strength=1.0,
                    stop_loss=sell_level + self._spacing,
                    metadata={
                        "order_type": "LIMIT",
                        "limit_price": f"{sell_level:.5f}",
                        "grid_level": sell_key,
                        "fixed_units": f"{self._units:.1f}",
                        "opposite_side": "BUY",
                        "opposite_grid_level": f"{sell_level:.2f}_long",
                        "opposite_stop_loss": f"{sell_level - self._spacing:.5f}",
                    },
                )
            )
            return self._signal_queue.popleft()

        return None

    def report_fill(self, grid_level_key: str, fill_price: float) -> None:
        self._fills.append((grid_level_key, fill_price))

    def report_trade_closed(self, grid_level_key: str, realized_pnl: float) -> None:
        self._closures.append((grid_level_key, realized_pnl))
        self._session_realized += realized_pnl

    def notify_close_all_complete(self) -> None:
        self._close_all_count += 1


def _build_runner(
    bars: list[BarData],
    strategy: FakeGridStrategy | None = None,
) -> tuple[BacktestRunner, FakeGridStrategy]:
    """Build a runner with grid strategy and given bars."""
    if strategy is None:
        strategy = FakeGridStrategy()

    market_data = HistoricalMarketDataAdapter(bars)
    broker = SimulatedBrokerAdapter(
        initial_capital=10000.0,
        spread=0.5,
        slippage=0.0,
        grid_mode=True,
    )
    risk_engine = RiskEngine(
        max_position_size=1000,
        max_daily_loss=50000.0,
        max_trades_per_day=1000,
        enabled=False,  # Disabled for grid backtests
    )
    config = BacktestConfig(
        symbol="XAU_USD",
        initial_capital=10000.0,
        position_size=10.0,
        bar_count=5,
    )
    repository = InMemoryRepository()

    runner = BacktestRunner(
        strategy=strategy,
        risk_engine=risk_engine,
        market_data=market_data,
        broker=broker,
        repository=repository,
        config=config,
        user_id="test",
    )
    return runner, strategy


class TestGridInitialization:
    """Grid strategy should place limit orders on first bar."""

    def test_limits_placed_on_first_bar(self) -> None:
        # Anchor at 4570, limits at 4560 (buy) and 4580 (sell)
        bars = _make_bars(
            [(4570.0, 4568.0, 4572.0)] * 5  # Flat bars, no fills
        )
        runner, strategy = _build_runner(bars)
        result = runner.run()

        # Strategy should have been called but no fills (price never reached limits)
        assert len(strategy._fills) == 0
        assert result.metrics.trade_count == 0


class TestLimitFillAndOpposite:
    """When a limit fills, runner should place opposite and report fills."""

    def test_buy_limit_fill_triggers_opposite_sell(self) -> None:
        bars = _make_bars([
            (4570.0, 4568.0, 4572.0),  # Bar 0: init, place limits
            (4570.0, 4568.0, 4572.0),  # Bar 1: flat
            (4565.0, 4559.0, 4571.0),  # Bar 2: low=4559 fills BUY LIMIT at 4560
            (4563.0, 4561.0, 4566.0),  # Bar 3: nothing
            (4562.0, 4560.0, 4565.0),  # Bar 4: nothing
            (4564.0, 4561.0, 4567.0),  # Bar 5: nothing
        ])
        runner, strategy = _build_runner(bars)
        runner.run()

        # Should have 2 fills: the limit fill + the opposite market
        assert len(strategy._fills) == 2
        # First fill: buy limit at 4560
        assert strategy._fills[0][0] == "4560.00_long"
        assert strategy._fills[0][1] == 4560.0
        # Second fill: opposite sell (market)
        assert strategy._fills[1][0] == "4560.00_short"


class TestStopLossClosure:
    """Stop-loss should trigger and report to strategy."""

    def test_sl_triggers_and_reports_closure(self) -> None:
        bars = _make_bars([
            (4570.0, 4568.0, 4572.0),  # init, places limits
            (4570.0, 4568.0, 4572.0),  # flat
            (4565.0, 4559.0, 4571.0),  # fills BUY LIMIT at 4560
            (4555.0, 4548.0, 4558.0),  # SL at 4550 triggers on buy side
            (4554.0, 4552.0, 4556.0),  # extra bar so previous bar is processed
        ])
        runner, strategy = _build_runner(bars)
        runner.run()

        # Should have closures reported
        assert len(strategy._closures) >= 1
        # The buy side at 4560 had SL at 4550, loss = 10 * (4550 - 4560) = -100
        buy_closure = next(
            (c for c in strategy._closures if c[0] == "4560.00_long"), None
        )
        assert buy_closure is not None
        assert buy_closure[1] == -100.0


class TestCloseAll:
    """FLAT/close_all should cancel pending, close trades, notify strategy."""

    def test_close_all_on_profit_target(self) -> None:
        # Use a very low profit target so it triggers easily
        strategy = FakeGridStrategy(
            grid_spacing=10.0, units=10.0, session_profit_target=5.0
        )

        bars = _make_bars([
            (4570.0, 4568.0, 4572.0),  # Bar 0: init
            (4565.0, 4559.0, 4571.0),  # Bar 1: fills BUY LIMIT at 4560
            (4575.0, 4562.0, 4576.0),  # Bar 2: unrealized profit on buy side
            (4578.0, 4574.0, 4579.0),  # Bar 3: more profit, should trigger close_all
            (4580.0, 4576.0, 4582.0),  # Bar 4: post-close
        ])
        runner, strategy = _build_runner(bars, strategy=strategy)
        runner.run()

        # Strategy should have been notified of close_all
        assert strategy._close_all_count >= 1


class TestBackwardCompatSimple:
    """Simple strategies should still work identically."""

    def test_simple_strategy_no_grid_mode(self) -> None:
        """A strategy without report_fill should use simple mode."""

        class SimpleStrategy:
            @property
            def name(self) -> str:
                return "simple"

            def generate(self, bars: list[BarData]) -> Signal | None:
                if len(bars) < 2:
                    return None
                if bars[-1].close > bars[-2].close:
                    return Signal(
                        symbol=bars[-1].symbol,
                        signal_type=SignalType.LONG,
                        strategy_name=self.name,
                        strength=1.0,
                        stop_loss=bars[-1].close - 5.0,
                    )
                return None

        bars = _make_bars([
            (100.0, 99.0, 101.0),
            (101.0, 100.0, 102.0),
            (102.0, 101.0, 103.0),
            (101.5, 100.5, 102.5),
            (103.0, 102.0, 104.0),
        ])

        market_data = HistoricalMarketDataAdapter(bars)
        broker = SimulatedBrokerAdapter(
            initial_capital=10000.0, spread=0.0, slippage=0.0
        )
        risk_engine = RiskEngine(
            max_position_size=100,
            max_daily_loss=50000.0,
            max_trades_per_day=100,
            enabled=True,
        )
        config = BacktestConfig(
            symbol="XAU_USD",
            initial_capital=10000.0,
            position_size=10.0,
            bar_count=5,
        )
        repository = InMemoryRepository()

        runner = BacktestRunner(
            strategy=SimpleStrategy(),
            risk_engine=risk_engine,
            market_data=market_data,
            broker=broker,
            repository=repository,
            config=config,
            user_id="test",
        )
        result = runner.run()

        # Should have trade records (even if no round-trips closed)
        assert len(result.trades) > 0
        # Should NOT have grid mode artifacts
        assert runner._is_grid is False
