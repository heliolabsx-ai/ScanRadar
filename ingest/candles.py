"""Tick -> 1-minute candle builder with cumulative volume delta and futures OI."""
import datetime as dt
from dataclasses import dataclass, field


@dataclass
class Candle:
    ts: dt.datetime
    open: float; high: float; low: float; close: float
    volume: int = 0
    oi: int = 0
    delta: int = 0


@dataclass
class SymbolState:
    symbol: str
    eq_token: int
    fut_token: int
    last_cum_vol: int = 0      # Kite sends cumulative day volume on equity ticks
    last_price: float = 0.0
    oi: int = 0
    cur: Candle | None = None
    closed: list = field(default_factory=list)

    def on_equity_tick(self, tick: dict, now: dt.datetime):
        price = tick["last_price"]
        cum_vol = tick.get("volume_traded", 0)
        vol = max(0, cum_vol - self.last_cum_vol) if self.last_cum_vol else 0
        self.last_cum_vol = cum_vol
        # tick rule: uptick -> buy volume, downtick -> sell volume
        sign = 1 if price > self.last_price else (-1 if price < self.last_price else 0)
        self.last_price = price

        minute = now.replace(second=0, microsecond=0)
        if self.cur is None or self.cur.ts != minute:
            if self.cur is not None:
                self.closed.append(self.cur)
            self.cur = Candle(ts=minute, open=price, high=price, low=price, close=price, oi=self.oi)
        c = self.cur
        c.high = max(c.high, price); c.low = min(c.low, price); c.close = price
        c.volume += vol; c.delta += sign * vol; c.oi = self.oi

    def on_fut_tick(self, tick: dict):
        if "oi" in tick and tick["oi"]:
            self.oi = tick["oi"]
            if self.cur:
                self.cur.oi = self.oi

    def finalize(self):
        """Market closed: move the forming candle to closed so the last minute is persisted."""
        if self.cur is not None:
            self.closed.append(self.cur)
            self.cur = None

    def pop_closed(self) -> list:
        out, self.closed = self.closed, []
        return out
