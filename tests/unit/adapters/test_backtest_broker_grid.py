"""Tests for SimulatedBrokerAdapter grid mode (limit orders, SL, multi-trade)."""

from datetime import UTC, datetime

import pytest

from aurex_trade.adapters.backtest.broker import SimulatedBrokerAdapter
from aurex_trade.domain.enums import OrderSide, OrderType
from aurex_trade.domain.models import BarData, Order


def _bar(
    close: float,
    low: float | None = None,
    high: float | None = None,
    ts: datetime | None = None,
    open_: float | None = None,
) -> BarData:
    """Helper to create a BarData with sensible defaults.

    ``open_`` controls the bar open independently of close — needed to exercise
    (or avoid) gap-through fills on stop entries and stop-loss exits.
    """
    return BarData(
        symbol="XAU_USD",
        open=open_ if open_ is not None else close,
        high=high if high is not None else close + 1.0,
        low=low if low is not None else close - 1.0,
        close=close,
        volume=100,
        timestamp=ts or datetime(2025, 1, 15, 12, 0, tzinfo=UTC),
    )


class TestLimitOrderPlacement:
    """Limit orders should create pending state, not fill immediately."""

    def test_limit_order_creates_pending(self) -> None:
        broker = SimulatedBrokerAdapter(initial_capital=10000.0, grid_mode=True)
        broker.set_current_bar(_bar(close=4570.0))

        order = Order(
            symbol="XAU_USD",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=10.0,
            limit_price=4560.0,
            stop_loss=4550.0,
        )
        trade = broker.place_order(order)

        # Should return trade with broker_trade_id for tracking
        assert trade.broker_trade_id != ""
        assert trade.price == 4560.0  # Limit price, not fill price

        # Should have a pending order
        pending = broker.get_pending_orders("XAU_USD")
        assert len(pending) == 1
        assert pending[0].limit_price == 4560.0
        assert pending[0].side == OrderSide.BUY

        # Should NOT have an open trade
        open_trades = broker.get_open_trades("XAU_USD")
        assert len(open_trades) == 0

    def test_market_order_creates_open_trade(self) -> None:
        broker = SimulatedBrokerAdapter(
            initial_capital=10000.0, spread=0.0, slippage=0.0, grid_mode=True
        )
        broker.set_current_bar(_bar(close=4570.0))

        order = Order(
            symbol="XAU_USD",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=10.0,
            stop_loss=4560.0,
        )
        trade = broker.place_order(order)

        open_trades = broker.get_open_trades("XAU_USD")
        assert len(open_trades) == 1
        assert open_trades[0].broker_trade_id == trade.broker_trade_id
        assert open_trades[0].open_price == 4570.0


class TestProcessBarLimitFills:
    """Limit orders should fill when bar touches limit price."""

    def test_buy_limit_fills_when_bar_low_touches_price(self) -> None:
        broker = SimulatedBrokerAdapter(initial_capital=10000.0, grid_mode=True)
        broker.set_current_bar(_bar(close=4570.0))

        order = Order(
            symbol="XAU_USD",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=10.0,
            limit_price=4560.0,
            stop_loss=4550.0,
        )
        broker.place_order(order)

        # Bar with low=4559 touches the limit price
        fill_bar = _bar(close=4565.0, low=4559.0, high=4572.0)
        broker.set_current_bar(fill_bar)
        newly_filled, _ = broker.process_bar(fill_bar)

        assert len(newly_filled) == 1
        # Buy limit crosses the spread: fills above the limit by half-spread + slip.
        assert 4560.0075 <= newly_filled[0].open_price <= 4560.0125
        assert newly_filled[0].side == OrderSide.BUY

        # Pending cleared, open trade created
        assert len(broker.get_pending_orders("XAU_USD")) == 0
        assert len(broker.get_open_trades("XAU_USD")) == 1

    def test_sell_limit_fills_when_bar_high_touches_price(self) -> None:
        broker = SimulatedBrokerAdapter(initial_capital=10000.0, grid_mode=True)
        broker.set_current_bar(_bar(close=4570.0))

        order = Order(
            symbol="XAU_USD",
            side=OrderSide.SELL,
            order_type=OrderType.LIMIT,
            quantity=10.0,
            limit_price=4580.0,
            stop_loss=4590.0,
        )
        broker.place_order(order)

        fill_bar = _bar(close=4575.0, low=4568.0, high=4581.0)
        broker.set_current_bar(fill_bar)
        newly_filled, _ = broker.process_bar(fill_bar)

        assert len(newly_filled) == 1
        # Sell limit crosses the spread: fills below the limit by half-spread + slip.
        assert 4579.9875 <= newly_filled[0].open_price <= 4579.9925
        assert newly_filled[0].side == OrderSide.SELL

    def test_limit_does_not_fill_when_bar_misses(self) -> None:
        broker = SimulatedBrokerAdapter(initial_capital=10000.0, grid_mode=True)
        broker.set_current_bar(_bar(close=4570.0))

        order = Order(
            symbol="XAU_USD",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=10.0,
            limit_price=4560.0,
        )
        broker.place_order(order)

        # Bar low is above limit price
        miss_bar = _bar(close=4572.0, low=4561.0, high=4580.0)
        broker.set_current_bar(miss_bar)
        newly_filled, _ = broker.process_bar(miss_bar)

        assert len(newly_filled) == 0
        assert len(broker.get_pending_orders("XAU_USD")) == 1


class TestProcessBarStopFills:
    """STOP entry orders fill on a breakout move (mirror of limit direction)."""

    def test_stop_order_creates_pending(self) -> None:
        broker = SimulatedBrokerAdapter(initial_capital=10000.0, grid_mode=True)
        broker.set_current_bar(_bar(close=4100.0))

        order = Order(
            symbol="XAU_USD",
            side=OrderSide.BUY,
            order_type=OrderType.STOP,
            quantity=20.0,
            limit_price=4115.90,  # trigger above the market
            stop_loss=4099.90,
        )
        trade = broker.place_order(order)

        assert trade.broker_trade_id != ""
        assert len(broker.get_pending_orders("XAU_USD")) == 1
        assert len(broker.get_open_trades("XAU_USD")) == 0

    def test_buy_stop_fills_when_bar_high_reaches_trigger(self) -> None:
        broker = SimulatedBrokerAdapter(initial_capital=10000.0, grid_mode=True)
        broker.set_current_bar(_bar(close=4100.0))

        order = Order(
            symbol="XAU_USD",
            side=OrderSide.BUY,
            order_type=OrderType.STOP,
            quantity=20.0,
            limit_price=4115.90,
            stop_loss=4099.90,
        )
        broker.place_order(order)

        # Price breaks up through the trigger intrabar (open below it — no gap).
        fill_bar = _bar(close=4116.0, low=4112.0, high=4117.0, open_=4113.0)
        broker.set_current_bar(fill_bar)
        newly_filled, _ = broker.process_bar(fill_bar)

        assert len(newly_filled) == 1
        # Buy stop crosses the spread above the trigger.
        assert 4115.9075 <= newly_filled[0].open_price <= 4115.9125
        assert newly_filled[0].side == OrderSide.BUY

    def test_sell_stop_fills_when_bar_low_reaches_trigger(self) -> None:
        broker = SimulatedBrokerAdapter(initial_capital=10000.0, grid_mode=True)
        broker.set_current_bar(_bar(close=4100.0))

        order = Order(
            symbol="XAU_USD",
            side=OrderSide.SELL,
            order_type=OrderType.STOP,
            quantity=20.0,
            limit_price=4085.00,  # trigger below the market
            stop_loss=4101.00,
        )
        broker.place_order(order)

        # Open above the trigger (no gap); price breaks down through it intrabar.
        fill_bar = _bar(close=4084.0, low=4083.0, high=4090.0, open_=4088.0)
        broker.set_current_bar(fill_bar)
        newly_filled, _ = broker.process_bar(fill_bar)

        assert len(newly_filled) == 1
        # Sell stop crosses the spread below the trigger.
        assert 4084.9875 <= newly_filled[0].open_price <= 4084.9925
        assert newly_filled[0].side == OrderSide.SELL

    def test_buy_stop_does_not_fill_when_price_stays_below(self) -> None:
        broker = SimulatedBrokerAdapter(initial_capital=10000.0, grid_mode=True)
        broker.set_current_bar(_bar(close=4100.0))

        order = Order(
            symbol="XAU_USD",
            side=OrderSide.BUY,
            order_type=OrderType.STOP,
            quantity=20.0,
            limit_price=4115.90,
        )
        broker.place_order(order)

        # High never reaches the trigger — a limit would have filled here, a stop must not.
        miss_bar = _bar(close=4110.0, low=4108.0, high=4114.0)
        broker.set_current_bar(miss_bar)
        newly_filled, _ = broker.process_bar(miss_bar)

        assert len(newly_filled) == 0
        assert len(broker.get_pending_orders("XAU_USD")) == 1


class TestCancelPendingOrder:
    """Cancelling a single pending order by its broker order ID."""

    def test_cancel_removes_only_that_order(self) -> None:
        broker = SimulatedBrokerAdapter(initial_capital=10000.0, grid_mode=True)
        broker.set_current_bar(_bar(close=4100.0))

        o1 = Order(
            symbol="XAU_USD", side=OrderSide.SELL, order_type=OrderType.LIMIT,
            quantity=20.0, limit_price=4115.0,
        )
        o2 = Order(
            symbol="XAU_USD", side=OrderSide.BUY, order_type=OrderType.LIMIT,
            quantity=20.0, limit_price=4085.0,
        )
        t1 = broker.place_order(o1)
        broker.place_order(o2)
        assert len(broker.get_pending_orders("XAU_USD")) == 2

        assert broker.cancel_pending_order(t1.broker_trade_id) is True
        remaining = broker.get_pending_orders("XAU_USD")
        assert len(remaining) == 1
        assert remaining[0].limit_price == 4085.0

    def test_cancel_unknown_order_returns_false(self) -> None:
        broker = SimulatedBrokerAdapter(initial_capital=10000.0, grid_mode=True)
        broker.set_current_bar(_bar(close=4100.0))
        assert broker.cancel_pending_order("does-not-exist") is False


class TestProcessBarStopLoss:
    """Stop-losses should trigger when bar breaches SL price."""

    def test_long_trade_sl_triggers_when_bar_low_breaches(self) -> None:
        broker = SimulatedBrokerAdapter(
            initial_capital=10000.0, spread=0.0, slippage=0.0, grid_mode=True
        )
        broker.set_current_bar(_bar(close=4570.0))

        order = Order(
            symbol="XAU_USD",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=10.0,
            stop_loss=4560.0,
        )
        broker.place_order(order)

        # Bar breaches stop-loss intrabar but opens above it (no gap-through).
        sl_bar = _bar(close=4555.0, low=4553.0, high=4568.0, open_=4565.0)
        broker.set_current_bar(sl_bar)
        _, newly_closed = broker.process_bar(sl_bar)

        assert len(newly_closed) == 1
        assert newly_closed[0].close_reason == "STOP_LOSS"
        # No gap + zero friction => fills exactly at the stop. 10 * (4560 - 4570) = -100
        assert newly_closed[0].realized_pnl == -100.0
        assert newly_closed[0].close_price == 4560.0

        # Trade removed from open
        assert len(broker.get_open_trades("XAU_USD")) == 0

    def test_short_trade_sl_triggers_when_bar_high_breaches(self) -> None:
        broker = SimulatedBrokerAdapter(
            initial_capital=10000.0, spread=0.0, slippage=0.0, grid_mode=True
        )
        broker.set_current_bar(_bar(close=4570.0))

        order = Order(
            symbol="XAU_USD",
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            quantity=10.0,
            stop_loss=4580.0,
        )
        broker.place_order(order)

        # Breaches the stop intrabar but opens below it (no gap-through).
        sl_bar = _bar(close=4582.0, low=4568.0, high=4583.0, open_=4575.0)
        broker.set_current_bar(sl_bar)
        _, newly_closed = broker.process_bar(sl_bar)

        assert len(newly_closed) == 1
        assert newly_closed[0].close_reason == "STOP_LOSS"
        # No gap + zero friction => fills exactly at the stop. 10 * (4570 - 4580) = -100
        assert newly_closed[0].realized_pnl == -100.0

    def test_sl_does_not_trigger_when_bar_misses(self) -> None:
        broker = SimulatedBrokerAdapter(
            initial_capital=10000.0, spread=0.0, slippage=0.0, grid_mode=True
        )
        broker.set_current_bar(_bar(close=4570.0))

        order = Order(
            symbol="XAU_USD",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=10.0,
            stop_loss=4560.0,
        )
        broker.place_order(order)

        safe_bar = _bar(close=4568.0, low=4561.0, high=4575.0)
        broker.set_current_bar(safe_bar)
        _, newly_closed = broker.process_bar(safe_bar)

        assert len(newly_closed) == 0
        assert len(broker.get_open_trades("XAU_USD")) == 1


class TestMultipleOpenTrades:
    """Broker should track multiple concurrent open trades."""

    def test_multiple_trades_tracked(self) -> None:
        broker = SimulatedBrokerAdapter(
            initial_capital=10000.0, spread=0.0, slippage=0.0, grid_mode=True
        )
        broker.set_current_bar(_bar(close=4570.0))

        # Open 3 trades
        for _ in range(3):
            order = Order(
                symbol="XAU_USD",
                side=OrderSide.BUY,
                order_type=OrderType.MARKET,
                quantity=10.0,
                stop_loss=4560.0,
            )
            broker.place_order(order)

        assert len(broker.get_open_trades("XAU_USD")) == 3

    def test_equity_reflects_all_open_trades(self) -> None:
        broker = SimulatedBrokerAdapter(
            initial_capital=10000.0, spread=0.0, slippage=0.0, grid_mode=True
        )
        broker.set_current_bar(_bar(close=4570.0))

        # BUY 10 @ 4570
        broker.place_order(
            Order(symbol="XAU_USD", side=OrderSide.BUY, quantity=10.0, stop_loss=4560.0)
        )
        # SELL 10 @ 4570
        broker.place_order(
            Order(symbol="XAU_USD", side=OrderSide.SELL, quantity=10.0, stop_loss=4580.0)
        )

        # Price moves up: buy profits, sell loses
        broker.set_current_bar(_bar(close=4575.0))
        # BUY unrealized: 10 * (4575 - 4570) = +50
        # SELL unrealized: 10 * (4570 - 4575) = -50
        # Net: 0
        assert broker.equity == pytest.approx(10000.0, abs=0.01)


class TestCancelOrders:
    """Cancellation of pending orders."""

    def test_cancel_all_orders(self) -> None:
        broker = SimulatedBrokerAdapter(initial_capital=10000.0, grid_mode=True)
        broker.set_current_bar(_bar(close=4570.0))

        for price in [4560.0, 4550.0, 4540.0]:
            broker.place_order(
                Order(
                    symbol="XAU_USD",
                    side=OrderSide.BUY,
                    order_type=OrderType.LIMIT,
                    quantity=10.0,
                    limit_price=price,
                )
            )

        assert len(broker.get_pending_orders("XAU_USD")) == 3
        cancelled = broker.cancel_all_orders("XAU_USD")
        assert cancelled == 3
        assert len(broker.get_pending_orders("XAU_USD")) == 0

    def test_cancel_single_order(self) -> None:
        broker = SimulatedBrokerAdapter(initial_capital=10000.0, grid_mode=True)
        broker.set_current_bar(_bar(close=4570.0))

        order = Order(
            symbol="XAU_USD",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=10.0,
            limit_price=4560.0,
        )
        trade = broker.place_order(order)

        from uuid import UUID

        result = broker.cancel_order(UUID(trade.broker_trade_id))
        assert result is True
        assert len(broker.get_pending_orders("XAU_USD")) == 0


class TestCloseTrade:
    """Explicit trade closure at market price."""

    def test_close_trade_at_market(self) -> None:
        broker = SimulatedBrokerAdapter(
            initial_capital=10000.0, spread=0.0, slippage=0.0, grid_mode=True
        )
        broker.set_current_bar(_bar(close=4570.0))

        trade = broker.place_order(
            Order(symbol="XAU_USD", side=OrderSide.BUY, quantity=10.0, stop_loss=4560.0)
        )

        # Price moved up
        broker.set_current_bar(_bar(close=4580.0))
        broker.close_trade(trade.broker_trade_id)

        closed = broker.get_closed_trade_details(trade.broker_trade_id)
        assert closed is not None
        assert closed.realized_pnl == 100.0  # 10 * (4580 - 4570)
        assert closed.close_reason == "MARKET_CLOSE"
        assert len(broker.get_open_trades("XAU_USD")) == 0


class TestEquityAfterClosures:
    """Equity should be correct after all trades close."""

    def test_equity_correct_after_sl_closes_all_trades(self) -> None:
        broker = SimulatedBrokerAdapter(
            initial_capital=10000.0, spread=0.0, slippage=0.0, grid_mode=True
        )
        broker.set_current_bar(_bar(close=4570.0))

        # Open BUY and SELL
        broker.place_order(
            Order(symbol="XAU_USD", side=OrderSide.BUY, quantity=10.0, stop_loss=4560.0)
        )
        broker.place_order(
            Order(symbol="XAU_USD", side=OrderSide.SELL, quantity=10.0, stop_loss=4580.0)
        )

        # Price drops — triggers BUY SL intrabar (opens above the stop, no gap).
        sl_bar = _bar(close=4555.0, low=4553.0, high=4568.0, open_=4565.0)
        broker.set_current_bar(sl_bar)
        broker.process_bar(sl_bar)

        # BUY closed at SL (4560): pnl = 10*(4560-4570) = -100
        # SELL still open, unrealized = 10*(4570-4555) = +150
        assert broker.equity == pytest.approx(10000.0 - 100.0 + 150.0, abs=0.01)

        # Now close SELL via close_trade
        sell_trade = broker._open_trades[0]
        broker.close_trade(sell_trade.broker_trade_id)
        closed = broker.get_closed_trade_details(sell_trade.broker_trade_id)
        assert closed is not None
        # SELL closed at market (4555): pnl = 10*(4570-4555) = +150
        assert closed.realized_pnl == 150.0

        # All trades closed — equity = capital (no unrealized)
        # capital = 10000 - 100 + 150 = 10050
        assert broker.equity == pytest.approx(10050.0, abs=0.01)
        assert len(broker.get_open_trades("XAU_USD")) == 0


class TestGapThroughFills:
    """Stops and stop-entries that gap past their level fill at the worse open."""

    def test_long_stop_loss_gaps_through_to_open(self) -> None:
        broker = SimulatedBrokerAdapter(
            initial_capital=10000.0, spread=0.0, slippage=0.0, grid_mode=True
        )
        broker.set_current_bar(_bar(close=4570.0))
        broker.place_order(
            Order(symbol="XAU_USD", side=OrderSide.BUY, quantity=10.0, stop_loss=4560.0)
        )

        # Bar gaps DOWN: opens 4550, well below the 4560 stop.
        gap_bar = _bar(close=4548.0, low=4545.0, high=4552.0, open_=4550.0)
        broker.set_current_bar(gap_bar)
        _, newly_closed = broker.process_bar(gap_bar)

        assert len(newly_closed) == 1
        # Fills at the open (4550), not the stop (4560): 10 * (4550 - 4570) = -200.
        assert newly_closed[0].close_price == 4550.0
        assert newly_closed[0].realized_pnl == -200.0

    def test_buy_stop_entry_gaps_through_to_open(self) -> None:
        broker = SimulatedBrokerAdapter(
            initial_capital=10000.0, spread=0.0, slippage=0.0, grid_mode=True
        )
        broker.set_current_bar(_bar(close=4100.0))
        broker.place_order(
            Order(
                symbol="XAU_USD", side=OrderSide.BUY, order_type=OrderType.STOP,
                quantity=20.0, limit_price=4115.90,
            )
        )

        # Bar gaps UP through the trigger: opens 4120, above the 4115.90 trigger.
        gap_bar = _bar(close=4122.0, low=4119.0, high=4123.0, open_=4120.0)
        broker.set_current_bar(gap_bar)
        newly_filled, _ = broker.process_bar(gap_bar)

        assert len(newly_filled) == 1
        # Fills at the open (4120), worse than the trigger (4115.90).
        assert newly_filled[0].open_price == 4120.0


class TestBackwardCompatibility:
    """Existing market-order-only behavior should be unchanged."""

    def test_market_order_fills_with_spread_and_slippage(self) -> None:
        broker = SimulatedBrokerAdapter(
            initial_capital=10000.0, spread=1.0, slippage=0.5, seed=42
        )
        broker.set_current_bar(_bar(close=100.0))

        trade = broker.place_order(
            Order(symbol="XAU_USD", side=OrderSide.BUY, quantity=1.0)
        )

        # Should be above mid: 100 + 0.5 (half_spread) + random(0, 0.5)
        assert trade.price > 100.0
        assert trade.price <= 101.0

    def test_process_bar_noop_when_no_pending_or_open(self) -> None:
        broker = SimulatedBrokerAdapter(initial_capital=10000.0)
        bar = _bar(close=100.0)
        broker.set_current_bar(bar)

        newly_filled, newly_closed = broker.process_bar(bar)
        assert newly_filled == []
        assert newly_closed == []
