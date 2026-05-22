"""Unit tests for broker-side closure detection in the trading engine."""

from unittest.mock import MagicMock

from aurex_trade.adapters.memory.repository import InMemoryRepository
from aurex_trade.adapters.paper.broker import PaperBrokerAdapter
from aurex_trade.domain.enums import OrderSide
from aurex_trade.domain.models import ClosedTradeInfo, OpenBrokerTrade
from aurex_trade.domain.risk.engine import RiskEngine
from aurex_trade.domain.strategy.ciby_grid_hedging import CibyGridHedgingStrategy
from aurex_trade.engine.trading_engine import TradingEngine

_TEST_USER_ID = "test-user"


def _build_grid_engine(
    seed: int = 42,
) -> tuple[TradingEngine, PaperBrokerAdapter, CibyGridHedgingStrategy]:
    """Build a TradingEngine with grid strategy for closure detection tests."""
    broker = PaperBrokerAdapter(base_price=2050.0, seed=seed)
    repository = InMemoryRepository()
    strategy = CibyGridHedgingStrategy(
        grid_spacing=10.0,
        stop_distance=30.0,
        reward_ratio=2.0,
        num_levels_above=3,
        num_levels_below=3,
    )
    risk_engine = RiskEngine(
        max_position_size=10,
        max_daily_loss=5000.0,
        max_trades_per_day=20,
    )
    engine = TradingEngine(
        strategy=strategy,
        risk_engine=risk_engine,
        broker=broker,
        market_data=broker,
        repository=repository,
        symbol="XAU_USD",
        interval_seconds=0,
        bar_count=10,
        user_id=_TEST_USER_ID,
    )
    return engine, broker, strategy


class TestCheckClosures:
    """Tests for _check_closures detecting broker-side trade closures."""

    def test_no_map_skips_check(self) -> None:
        """When no trades are mapped, _check_closures does nothing."""
        engine, _broker, _ = _build_grid_engine()
        # Should not raise or call broker
        engine._check_closures()
        assert engine._grid_trade_map == {}

    def test_detects_closed_trade_and_releases_level(self) -> None:
        """When a mapped trade is no longer open, level is released."""
        engine, broker, strategy = _build_grid_engine()

        # Manually set up a mapped trade
        engine._grid_trade_map[2060.0] = "100"

        # Simulate that the strategy has this level triggered
        strategy._filled_levels[2060.0] = OrderSide.BUY  # type: ignore[assignment]

        # Mock broker to return empty open trades (trade 100 closed)
        broker.get_open_trades = MagicMock(return_value=[])  # type: ignore[method-assign]
        broker.get_closed_trade_details = MagicMock(  # type: ignore[method-assign]
            return_value=ClosedTradeInfo(
                broker_trade_id="100",
                close_price=2090.0,
                realized_pnl=150.0,
                close_reason="TAKE_PROFIT",
            )
        )

        engine._check_closures()

        # Level should be released from the map
        assert 2060.0 not in engine._grid_trade_map
        # Level should be released from strategy
        assert 2060.0 not in strategy._filled_levels
        # Close marker should be recorded
        markers = engine.get_trade_markers()
        close_markers = [m for m in markers if m["side"] == "close_tp"]
        assert len(close_markers) == 1
        assert close_markers[0]["price"] == 2090.0
        assert close_markers[0]["broker_trade_id"] == "100"

    def test_stop_loss_closure_creates_sl_marker(self) -> None:
        """Stop loss closure creates a close_sl marker."""
        engine, broker, strategy = _build_grid_engine()

        engine._grid_trade_map[2040.0] = "200"
        strategy._filled_levels[2040.0] = OrderSide.SELL  # type: ignore[assignment]

        broker.get_open_trades = MagicMock(return_value=[])  # type: ignore[method-assign]
        broker.get_closed_trade_details = MagicMock(  # type: ignore[method-assign]
            return_value=ClosedTradeInfo(
                broker_trade_id="200",
                close_price=2070.0,
                realized_pnl=-90.0,
                close_reason="STOP_LOSS",
            )
        )

        engine._check_closures()

        markers = engine.get_trade_markers()
        close_markers = [m for m in markers if m["side"] == "close_sl"]
        assert len(close_markers) == 1
        assert close_markers[0]["price"] == 2070.0
        assert close_markers[0]["broker_trade_id"] == "200"

    def test_still_open_trade_not_released(self) -> None:
        """Trades still open on broker are not released."""
        engine, broker, strategy = _build_grid_engine()

        engine._grid_trade_map[2060.0] = "100"
        strategy._filled_levels[2060.0] = OrderSide.BUY  # type: ignore[assignment]

        # Trade is still open
        broker.get_open_trades = MagicMock(  # type: ignore[method-assign]
            return_value=[
                OpenBrokerTrade(
                    broker_trade_id="100",
                    symbol="XAU_USD",
                    side=OrderSide.BUY,
                    quantity=5.0,
                    open_price=2060.0,
                )
            ]
        )

        engine._check_closures()

        # Should still be in the map
        assert 2060.0 in engine._grid_trade_map
        assert 2060.0 in strategy._filled_levels

    def test_no_details_still_releases_level(self) -> None:
        """Even if broker can't provide close details, level is still released."""
        engine, broker, strategy = _build_grid_engine()

        engine._grid_trade_map[2060.0] = "100"
        strategy._filled_levels[2060.0] = OrderSide.BUY  # type: ignore[assignment]

        broker.get_open_trades = MagicMock(return_value=[])  # type: ignore[method-assign]
        broker.get_closed_trade_details = MagicMock(return_value=None)  # type: ignore[method-assign]

        engine._check_closures()

        assert 2060.0 not in engine._grid_trade_map
        assert 2060.0 not in strategy._filled_levels
        # Still creates a marker (with fallback values)
        markers = engine.get_trade_markers()
        close_markers = [m for m in markers if "close" in m["side"]]
        assert len(close_markers) == 1
