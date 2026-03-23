import clr
clr.AddReference("cAlgo.API")
from cAlgo.API import *
import math

class MomentumBot():
    """
    Momentum Breakout Bot v12 — NASDAQ M5
    Strategia: Compressione → Breakout con WAE + CHOP + Macro Efficiency
    Risultati backtest: €+382 su 4 anni (2022-2025), 48% WR cross-year
    """

    # ══════════════════════════════════════════════════════
    # INIZIALIZZAZIONE
    # ══════════════════════════════════════════════════════
    def on_start(self):
        api.Positions.Closed += self.OnPositionClosed
        api.Positions.Opened += self.OnPositionOpened
        self.symbol = api.Symbol
        self.account = api.Account

        # ── Parametri strategia ──
        self.RISK_PCT = 0.5         # Rischio per trade (%)
        self.SL_ATR_MULT = 0.8     # SL = ATR × 0.8
        self.RR = 1.1              # TP = SL × 1.1
        self.SLIPPAGE_MAX = 4      # Max slippage ammesso (pip)
        self.STALL_TIMEOUT = 600   # Timeout se profit < 0.1R (secondi)
        self.HARD_TIMEOUT = 1200   # Timeout assoluto (secondi)
        self.MAX_DAILY_STOPS = 3   # Stop giornalieri prima di blocco

        # ── Buffer prezzi ──
        self.prices_15s = []
        self.last_sample_ts = api.Server.Time
        self.ready = False
        self.PRICE_SAMPLE_BOOTSTRAP = 2
        self.PRICE_SAMPLE_NORMAL = 15
        self.MIN_PRICES = 55

        # ── Controlli operativi ──
        self.order_lock = False
        self.last_entry_time = None
        self.pause_bars_remaining = 0
        self.planned_entry_price = 0
        self.planned_sl_pips = 0
        self.motivo = None
        self.last_stop_reset_day = api.Server.Time.Day

        # ── Loss zone ──
        self.last_loss_price_zone = None
        self.loss_zone_timestamp = None
        self.ZONE_TOLERANCE_PIPS = 20
        self.ZONE_EXPIRY_BARS = 20

        api.Print("AdvancedStrategyBot v12 avviato")

    # ══════════════════════════════════════════════════════
    # ON BAR — Calcoli e decisioni ogni 5 minuti
    # ══════════════════════════════════════════════════════
    def on_bar(self):
        try:
            if self.pause_bars_remaining > 0:
                self.pause_bars_remaining -= 1
                if self.pause_bars_remaining > 0:
                    return

            # Pulizia loss zone scaduta
            if self.loss_zone_timestamp is not None:
                bars_since = (api.Server.Time - self.loss_zone_timestamp).TotalMinutes / 5
                if bars_since > self.ZONE_EXPIRY_BARS:
                    self.last_loss_price_zone = None
                    self.loss_zone_timestamp = None

            if self.has_position():
                return

            bars = api.Bars
            if bars.Count < 60:
                return

            count = bars.Count
            idx = count - 60
            close = [bars.ClosePrices[i] for i in range(idx, count)]
            open_ = [bars.OpenPrices[i] for i in range(idx, count)]
            high = [bars.HighPrices[i] for i in range(idx, count)]
            low = [bars.LowPrices[i] for i in range(idx, count)]
            atr = self.calcolo_atr(high, low, close, 10)

            if atr[-1] is None:
                return

            self.check_entry(self.symbol.Bid, self.symbol.Ask, open_, high, low, close, atr)
        except Exception as e:
            api.Print(f"ERR ON_BAR: {e}")

    # ══════════════════════════════════════════════════════
    # ON TICK — Gestione posizione aperta
    # ══════════════════════════════════════════════════════
    def on_tick(self):
        try:
            now = api.Server.Time

            # Campionamento prezzi
            interval = self.PRICE_SAMPLE_BOOTSTRAP if len(self.prices_15s) < self.MIN_PRICES else self.PRICE_SAMPLE_NORMAL
            if (now - self.last_sample_ts).TotalSeconds >= interval:
                self.prices_15s.append(self.symbol.Bid)
                self.last_sample_ts = now
                if len(self.prices_15s) >= 2000:
                    self.prices_15s = self.prices_15s[-1500:]
                if not self.ready and len(self.prices_15s) >= self.MIN_PRICES:
                    self.ready = True
                    api.Print("✅ BUFFER PIENO - READY")

            if not self.ready or api.Bars.Count < 60:
                return

            pos = self.get_position()
            if pos:
                bars = api.Bars
                count = bars.Count
                idx = count - 45
                close = [bars.ClosePrices[i] for i in range(idx, count)]
                open_ = [bars.OpenPrices[i] for i in range(idx, count)]
                high = [bars.HighPrices[i] for i in range(idx, count)]
                low = [bars.LowPrices[i] for i in range(idx, count)]
                atr = self.calcolo_atr(high, low, close, 10)
                self.manage_position(pos, open_, close, atr)
        except Exception as e:
            api.Print(f"ERR ON_TICK: {e}")

    # ══════════════════════════════════════════════════════
    # CHECK ENTRY — Logica di ingresso
    # ══════════════════════════════════════════════════════
    def check_entry(self, bid, ask, open_, high, low, close, atr):
        now = api.Server.Time

        # Reset giornaliero
        if now.Day != self.last_stop_reset_day:
            self.last_stop_reset_day = now.Day
            self.last_loss_price_zone = None
            self.loss_zone_timestamp = None

        # ── Guardie ──
        if not self.trading_time_allowed():
            return
        if self.check_daily_stop_limit():
            return
        if self._is_in_loss_zone(ask):
            return
        if self.order_lock:
            if not self.has_position():
                self.order_lock = False
            else:
                return

        # ── Money management mensile ──
        monthly_status = self.get_monthly_status()
        if monthly_status >= 1:
            return
        risk_multiplier = 0.5 if monthly_status == 0.5 else 1.0

        # ── Filtro volatilità estrema ──
        vol_pct = self.get_volatility_percentile(atr, 20)
        if vol_pct > 90:
            return

        # ── Segnale ──
        is_valid, motivo, score = self.check_momentum(close, high, low, open_, atr)
        if not is_valid:
            return

        # ── Calcolo SL/TP ──
        spread_pips = (ask - bid) / self.symbol.PipSize
        atr_cur = self.atr_mean(atr, 5)
        sl_pips = (atr_cur * self.SL_ATR_MULT) / self.symbol.PipSize
        tp_pips = sl_pips * self.RR

        volume = self.calc_volume(sl_pips, risk_multiplier)
        if volume < self.symbol.VolumeInUnitsMin or spread_pips > sl_pips * 0.20:
            return

        self.planned_sl_pips = sl_pips
        self.motivo = motivo

        # ── Log indicatori ──
        hurst_acc = self.get_hurst_acceleration(close)
        adx, _, _ = self.calc_ADX(high, low, close, 5)
        bb_b, _, bb_w_pct = self.get_bollinger_squeeze(close, 10, 2, 10)
        trend_force, explosion, dead_zone = self.get_wae(close, 10, 20, 10, 2)
        chop = self.get_choppiness(high, low, close, 10)
        force_ratio = abs(trend_force) / dead_zone if dead_zone > 0 else 0
        macro_eff = self.get_macro_efficiency(close, 25)
        api.Print(f"📊 BBw:{bb_w_pct:.2f}, BBb:{bb_b:.2f}, Trend force{trend_force:.3f}, "
                  f"dead zone: {dead_zone}, explosion: {explosion}, force ratio:{force_ratio}, "
                  f"V:{vol_pct:.0f} Chop:{chop:.2f} Ha:{hurst_acc:.3f} ADX:{adx:.0f} "
                  f"macro_eff:{macro_eff} Dir:{motivo} Score:{score}")

        # ── Esecuzione ──
        if "LONG" in motivo:
            self.planned_entry_price = ask
            api.ExecuteMarketOrder(TradeType.Buy, self.symbol, volume,
                                  "AdvancedBot", sl_pips, tp_pips)
        elif "SHORT" in motivo:
            self.planned_entry_price = bid
            api.ExecuteMarketOrder(TradeType.Sell, self.symbol, volume,
                                  "AdvancedBot", sl_pips, tp_pips)
        self.order_lock = True
        self.last_entry_time = now

    # ══════════════════════════════════════════════════════
    # CHECK MOMENTUM — Cuore della strategia
    # ══════════════════════════════════════════════════════
    def check_momentum(self, close, high, low, open_, atr):
        bb_b, bb_w, bb_w_pct = self.get_bollinger_squeeze(close, 10, 2, 10)
        hurst = self.get_fractal_efficiency(close, 5)
        adx, plus_di, minus_di = self.calc_ADX(high, low, close, 5)
        conviction = self.get_candle_conviction(open_, close, high, low)
        trend_force, explosion, dead_zone = self.get_wae(close, 10, 20, 10, 2)
        chop = self.get_choppiness(high, low, close, 10)

        # ── Pre-filtri ──
        if chop < 30 or chop > 45:
            return False, "", 0
        if conviction < 0.50:
            return False, "", 0

        force_ratio = abs(trend_force) / dead_zone if dead_zone > 0 else 0
        if force_ratio < 50 or force_ratio > 150:
            return False, "", 0

        macro_eff = self.get_macro_efficiency(close, 25)
        if macro_eff < 0.15:
            return False, "", 0

        # ── Scoring ──
        score_long = 0
        score_short = 0

        # 1. WAE direzione
        if trend_force > dead_zone:
            score_long += 1
        if trend_force < -dead_zone:
            score_short += 1

        # 2. Bande compresse (energia accumulata)
        if bb_w_pct < 50:
            score_long += 1
            score_short += 1

        # 3. Breakout dalle bande
        if bb_b > 90:
            score_long += 1
        if bb_b < 10:
            score_short += 1

        # 4. Direzione confermata
        if adx > 20:
            if plus_di > minus_di:
                score_long += 1
            if minus_di > plus_di:
                score_short += 1

        # 5. Movimento efficiente
        if hurst > 0.50:
            score_long += 1
            score_short += 1

        if score_long >= 4:
            return True, "MOMENTUM-LONG", score_long
        if score_short >= 4:
            return True, "MOMENTUM-SHORT", score_short

        return False, "", 0

    # ══════════════════════════════════════════════════════
    # MANAGE POSITION — Gestione trade aperto
    # ══════════════════════════════════════════════════════
    def manage_position(self, pos, open_, close, atr):
        if pos is None or pos.VolumeInUnits <= 0:
            return

        durata = (api.Server.Time - pos.EntryTime).TotalSeconds
        entry = pos.EntryPrice
        sl = pos.StopLoss
        if sl is None:
            return

        rischio = abs(entry - sl)
        if rischio <= 0:
            return

        if pos.TradeType == TradeType.Buy:
            profit_R = (self.symbol.Bid - entry) / rischio
        else:
            profit_R = (entry - self.symbol.Ask) / rischio

        # Stallo: nessun progresso dopo 10 min
        if durata > self.STALL_TIMEOUT and profit_R < 0.1:
            api.ClosePosition(pos)
            return

        # Hard timeout: 20 min
        if durata > self.HARD_TIMEOUT:
            api.ClosePosition(pos)
            return

    # ══════════════════════════════════════════════════════
    # POSIZIONE APERTA — Gestione slippage
    # ══════════════════════════════════════════════════════
    def OnPositionOpened(self, args):
        position = args.Position
        if position.SymbolName != self.symbol.Name or position.Label != "AdvancedBot":
            return

        self.order_lock = True
        self.last_entry_time = api.Server.Time

        entry_real = position.EntryPrice
        entry_planned = self.planned_entry_price
        sl_planned = self.planned_sl_pips

        # Calcolo slippage
        if position.TradeType == TradeType.Buy:
            slippage_pips = (entry_real - entry_planned) / self.symbol.PipSize
        else:
            slippage_pips = (entry_planned - entry_real) / self.symbol.PipSize

        # Slippage eccessivo → annulla
        if abs(slippage_pips) > self.SLIPPAGE_MAX:
            api.ClosePosition(position)
            api.Print(f"⛔ ANNULLATO: slippage {slippage_pips:.1f}p > {self.SLIPPAGE_MAX}p")
            self.pause_bars_remaining = 2
            self.order_lock = False
            return

        # Slippage moderato → compensa
        sl_compensation = max(0, abs(slippage_pips) * 0.5)
        adjusted_sl = sl_planned + sl_compensation

        if position.TradeType == TradeType.Buy:
            new_sl = entry_real - (adjusted_sl * self.symbol.PipSize)
            new_tp = entry_real + (sl_planned * self.RR * self.symbol.PipSize)
        else:
            new_sl = entry_real + (adjusted_sl * self.symbol.PipSize)
            new_tp = entry_real - (sl_planned * self.RR * self.symbol.PipSize)

        try:
            r1 = position.ModifyStopLossPrice(new_sl)
            r2 = position.ModifyTakeProfitPrice(new_tp)
            sl_ok = r1 is not None and (not hasattr(r1, 'IsSuccessful') or r1.IsSuccessful)
            tp_ok = r2 is not None and (not hasattr(r2, 'IsSuccessful') or r2.IsSuccessful)

            if not sl_ok or not tp_ok:
                api.ClosePosition(position)
                self.pause_bars_remaining = 3
                self.order_lock = False
                return
        except Exception as e:
            api.Print(f"⚠️ Errore SL/TP: {e}")

        if sl_compensation > 0:
            api.Print(f"⚠️ Slippage {slippage_pips:.1f}p → SL +{sl_compensation:.1f}p")

        api.Print(f"✅ ORDINE PIAZZATO: {position.Id}")

    # ══════════════════════════════════════════════════════
    # POSIZIONE CHIUSA — Log e gestione post-trade
    # ══════════════════════════════════════════════════════
    def OnPositionClosed(self, args):
        position = args.Position
        reason = args.Reason

        if position.SymbolName != self.symbol.Name or position.Label != "AdvancedBot":
            return

        self.order_lock = False
        self.last_entry_time = None

        # Recupero dati finanziari
        closed_trade = None
        for i in range(api.History.Count - 1, -1, -1):
            trade = api.History[i]
            if trade.PositionId == position.Id:
                closed_trade = trade
                break

        if not closed_trade:
            api.Print(f"⚠️ Trade {position.Id} non trovato in History")
            return

        pnl = closed_trade.NetProfit
        close_price = closed_trade.ClosingPrice

        # R-Multiple
        r_mult = 0.0
        if position.StopLoss is not None:
            risk = abs(position.EntryPrice - position.StopLoss)
            if risk > 0:
                if position.TradeType == TradeType.Buy:
                    r_mult = (close_price - position.EntryPrice) / risk
                else:
                    r_mult = (position.EntryPrice - close_price) / risk

        # Log
        esito = str(reason).upper()
        icona = "✅" if pnl > 0 else "❌"
        api.Print("=== POSIZIONE CHIUSA ===")
        api.Print(f"Esito  : {esito} {icona}")
        api.Print(f"PnL    : € {pnl:.2f} | R-Mult: {r_mult:.2f}R")
        api.Print(f"Entry  : {position.EntryPrice:.5f} | Close: {close_price:.5f}")
        api.Print("========================")

        # Log indicatori alla chiusura
        bars = api.Bars
        count = bars.Count
        idx = count - 60
        close_arr = [bars.ClosePrices[i] for i in range(idx, count)]
        open_arr = [bars.OpenPrices[i] for i in range(idx, count)]
        high_arr = [bars.HighPrices[i] for i in range(idx, count)]
        low_arr = [bars.LowPrices[i] for i in range(idx, count)]
        atr_arr = self.calcolo_atr(high_arr, low_arr, close_arr, 10)

        h_c = self.get_fractal_efficiency(close_arr, 5)
        ha_c = self.get_hurst_acceleration(close_arr)
        adx_c, _, _ = self.calc_ADX(high_arr, low_arr, close_arr, 5)
        bb_b_c, _, _ = self.get_bollinger_squeeze(close_arr, 10, 2, 10)
        conv_c = self.get_candle_conviction(open_arr, close_arr, high_arr, low_arr)
        str_c = self.get_consecutive_direction(close_arr)
        vol_c = self.get_volatility_percentile(atr_arr, 20)
        dur = (api.Server.Time - position.EntryTime).TotalSeconds

        api.Print(f"📊 CLOSE: H:{h_c:.2f} Ha:{ha_c:+.3f} ADX:{adx_c:.0f} "
                  f"BBb:{bb_b_c:.1f} V:{vol_c:.0f} Conv:{conv_c:.2f} Str:{str_c} Dur:{dur:.0f}s")

        # Gestione loss
        if pnl < 0:
            self.last_loss_price_zone = position.EntryPrice
            self.loss_zone_timestamp = api.Server.Time
            if self.pause_bars_remaining == 0:
                self.pause_bars_remaining = 2
                api.Print(f"⏸️ PAUSA: 2 candele dopo loss")
        else:
            self.last_loss_price_zone = None
            self.loss_zone_timestamp = None

    # ══════════════════════════════════════════════════════
    # INDICATORI
    # ══════════════════════════════════════════════════════
    def get_wae(self, close, fast=10, slow=20, bb_period=10, bb_mult=2, sensitivity=150):
        """Waddah Attar Explosion — Forza del breakout"""
        if len(close) < slow + 2:
            return 0, 0, 0

        ema_f = self.ema(close, fast)
        ema_s = self.ema(close, slow)

        macd_now = ema_f[-1] - ema_s[-1]
        macd_prev = ema_f[-2] - ema_s[-2]
        trend_force = (macd_now - macd_prev) * sensitivity

        sma = sum(close[-bb_period:]) / bb_period
        variance = sum((c - sma) ** 2 for c in close[-bb_period:]) / bb_period
        std = variance ** 0.5
        explosion = (sma + std * bb_mult) - (sma - std * bb_mult)

        ranges = [abs(close[-i] - close[-i - 1]) for i in range(1, min(21, len(close)))]
        dead_zone = sum(ranges) / len(ranges) * 0.2

        return trend_force, explosion, dead_zone

    def get_choppiness(self, high, low, close, period=10):
        """Choppiness Index — Trend (basso) vs Range (alto)"""
        if len(close) < period + 1:
            return 50

        atr_sum = 0
        for i in range(1, period + 1):
            tr = max(high[-i] - low[-i],
                     abs(high[-i] - close[-i - 1]),
                     abs(low[-i] - close[-i - 1]))
            atr_sum += tr

        highest = max(high[-period:])
        lowest = min(low[-period:])
        hl_range = highest - lowest

        if hl_range == 0:
            return 50

        return 100 * math.log10(atr_sum / hl_range) / math.log10(period)

    def get_macro_efficiency(self, close, period=25):
        """Efficienza macro — Filtra mercati caotici (2022)"""
        if len(close) < period + 1:
            return 0.5
        net_move = abs(close[-1] - close[-period])
        path = sum(abs(close[-i] - close[-i - 1]) for i in range(1, period + 1))
        if path == 0:
            return 0.0
        return net_move / path

    def get_fractal_efficiency(self, close, period=10):
        """Efficienza del movimento: dritto (1.0) vs zigzag (0.0)"""
        if len(close) < period + 1:
            return 0.0
        change = abs(close[-1] - close[-period])
        path = sum(abs(close[-i] - close[-i - 1]) for i in range(1, period + 1))
        if path == 0:
            return 0.0
        return change / path

    def get_hurst_acceleration(self, close, period=5):
        """Accelerazione dell'efficienza direzionale"""
        if len(close) < period + 3:
            return 0.0
        h_now = self.get_fractal_efficiency(close, period)
        h_prev = self.get_fractal_efficiency(close[:-3], period)
        return h_now - h_prev

    def get_bollinger_squeeze(self, close, period=20, std_dev=2, pct_lookback=20):
        """Bollinger %B + Bandwidth + BBw Percentile"""
        sma = sum(close[-period:]) / period
        variance = sum((c - sma) ** 2 for c in close[-period:]) / period
        std = math.sqrt(variance)
        upper = sma + std * std_dev
        lower = sma - std * std_dev

        percent_b = ((close[-1] - lower) / (upper - lower)) * 100 if upper != lower else 50
        bandwidth = ((upper - lower) / sma) * 100

        bbw_pct = 50
        if len(close) >= period + pct_lookback:
            bw_history = []
            for i in range(pct_lookback):
                offset = pct_lookback - 1 - i
                chunk = close[-(period + offset):len(close) - offset] if offset > 0 else close[-period:]
                s = sum(chunk) / period
                v = sum((c - s) ** 2 for c in chunk) / period
                st = math.sqrt(v)
                bw_history.append(((s + st * std_dev) - (s - st * std_dev)) / s * 100 if s != 0 else 0)
            bbw_pct = sum(1 for bw in bw_history if bw <= bandwidth) / pct_lookback * 100

        return percent_b, bandwidth, bbw_pct

    def get_candle_conviction(self, open_, close, high, low):
        """Rapporto body/range della candela chiusa: 0.0 (indecisa) → 1.0 (decisa)"""
        body = abs(close[-2] - open_[-2])
        rng = high[-2] - low[-2]
        if rng == 0:
            return 0.0
        return body / rng

    def get_consecutive_direction(self, close):
        """Candele consecutive: +N verdi, -N rosse"""
        count = 0
        if close[-1] > close[-2]:
            for i in range(1, min(8, len(close))):
                if close[-i] > close[-i - 1]:
                    count += 1
                else:
                    break
            return count
        else:
            for i in range(1, min(8, len(close))):
                if close[-i] < close[-i - 1]:
                    count += 1
                else:
                    break
            return -count

    def calc_ADX(self, high, low, close, period=14):
        """ADX con smoothing Wilder"""
        if len(close) < period * 2 + 1:
            return 0.0, 0.0, 0.0

        tr_list, plus_dm, minus_dm = [], [], []
        for i in range(1, len(close)):
            tr_list.append(max(high[i] - low[i],
                               abs(high[i] - close[i - 1]),
                               abs(low[i] - close[i - 1])))
            up = high[i] - high[i - 1]
            down = low[i - 1] - low[i]
            plus_dm.append(up if up > down and up > 0 else 0.0)
            minus_dm.append(down if down > up and down > 0 else 0.0)

        if len(tr_list) < period:
            return 0.0, 0.0, 0.0

        atr_w = sum(tr_list[:period])
        pdm_w = sum(plus_dm[:period])
        mdm_w = sum(minus_dm[:period])
        dx_list = []
        pdi, mdi = 0.0, 0.0

        for i in range(period, len(tr_list)):
            atr_w = atr_w - atr_w / period + tr_list[i]
            pdm_w = pdm_w - pdm_w / period + plus_dm[i]
            mdm_w = mdm_w - mdm_w / period + minus_dm[i]
            if atr_w == 0:
                continue
            pdi = pdm_w / atr_w * 100
            mdi = mdm_w / atr_w * 100
            di_sum = pdi + mdi
            dx_list.append(0.0 if di_sum == 0 else abs(pdi - mdi) / di_sum * 100)

        if len(dx_list) < period:
            return 0.0, pdi, mdi

        adx = sum(dx_list[:period]) / period
        for dx in dx_list[period:]:
            adx = (adx * (period - 1) + dx) / period

        return adx, pdi, mdi

    def ema(self, values, period):
        """Exponential Moving Average"""
        if len(values) < period:
            return [values[-1]] * len(values) if values else []
        alpha = 2.0 / (period + 1.0)
        result = [sum(values[:period]) / period]
        for v in values[period:]:
            result.append((v - result[-1]) * alpha + result[-1])
        return [0.0] * (period - 1) + result

    def calcolo_atr(self, high, low, close, period=10):
        """Average True Range"""
        if len(high) < period + 1:
            return [None] * len(high)
        tr = []
        for i in range(1, len(high)):
            tr.append(max(high[i] - low[i],
                          abs(high[i] - close[i - 1]),
                          abs(low[i] - close[i - 1])))
        atr = [None] * period
        for i in range(period, len(tr)):
            atr.append(sum(tr[i - period:i]) / period)
        return atr

    def get_volatility_percentile(self, atr, period=20):
        """Percentile dell'ATR attuale vs recente"""
        recent = [a for a in atr[-period:] if a is not None]
        if len(recent) < period:
            return 50
        current = recent[-1]
        rank = sum(1 for v in recent if v <= current)
        return (rank / period) * 100

    # ══════════════════════════════════════════════════════
    # HELPERS
    # ══════════════════════════════════════════════════════
    def has_position(self):
        return any(p.SymbolName == self.symbol.Name and p.Label == "AdvancedBot"
                   and p.VolumeInUnits > 0 for p in api.Positions)

    def get_position(self):
        for p in api.Positions:
            if p.SymbolName == self.symbol.Name and p.Label == "AdvancedBot":
                return p
        return None

    def atr_mean(self, atr, period=4):
        clean = [v for v in atr if v is not None]
        if len(clean) < period:
            return clean[-1] if clean else 0
        return sum(clean[-period:]) / period

    def trading_time_allowed(self):
        h = api.Server.Time.Hour
        m = api.Server.Time.Minute
        return (h == 14 and m >= 35) or (15 <= h <= 17)

    def _is_in_loss_zone(self, price):
        if self.last_loss_price_zone is None:
            return False
        distance = abs(price - self.last_loss_price_zone) / self.symbol.PipSize
        return distance < self.ZONE_TOLERANCE_PIPS

    def calc_volume(self, sl_pips, multiplier=1.0):
        try:
            capital = min(self.account.Balance, self.account.Equity)
            risk = capital * (self.RISK_PCT * multiplier / 100)
            pip_val = self.symbol.PipValue / self.symbol.LotSize
            raw = risk / (sl_pips * pip_val)
            volume = max(raw, self.symbol.VolumeInUnitsMin)
            step = self.symbol.VolumeInUnitsStep
            volume = math.floor(volume / step) * step
            return max(int(volume), int(self.symbol.VolumeInUnitsMin))
        except Exception as e:
            api.Print(f"[calc_volume ERR] {e}")
            return 0

    def check_daily_stop_limit(self):
        """Blocca dopo N stop giornalieri"""
        today = api.Server.Time.Date
        stops = sum(1 for h in api.History
                    if h.SymbolName == self.symbol.Name
                    and h.Label == "AdvancedBot"
                    and h.ClosingTime.Date == today
                    and h.NetProfit < 0)
        active = sum(1 for p in api.Positions
                     if p.Label == "AdvancedBot"
                     and p.SymbolName == self.symbol.Name)
        return (stops + active) >= self.MAX_DAILY_STOPS

    def get_monthly_status(self, target=5.0, soft_dd=2.0, hard_dd=4.0):
        """0=normale, 0.5=rischio dimezzato, 1=profit lock, 2=hard stop"""
        now = api.Server.Time
        trades = sorted(
            [h for h in api.History
             if h.Label == "AdvancedBot"
             and h.ClosingTime.Month == now.Month
             and h.ClosingTime.Year == now.Year],
            key=lambda x: x.ClosingTime)

        total_pnl = sum(t.NetProfit for t in trades)
        start_bal = self.account.Balance - total_pnl
        if start_bal <= 0:
            return 0

        pct = (total_pnl / start_bal) * 100

        # Hard stop
        if pct <= -hard_dd:
            return 2

        # Profit lock
        target_money = start_bal * (target / 100)
        hit_target = False
        running = 0.0
        tolerance = start_bal * 0.002
        for t in trades:
            running += t.NetProfit
            if running >= target_money:
                hit_target = True
            if hit_target and t.NetProfit < -tolerance:
                return 1

        # Soft drawdown con reset su 2 TP consecutivi
        if pct <= -soft_dd:
            if (len(trades) >= 2
                    and trades[-1].NetProfit > 0
                    and trades[-2].NetProfit > 0):
                return 0
            return 0.5

        return 0
