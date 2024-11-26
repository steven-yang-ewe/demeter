import copy

import decimal
import math
import multiprocessing
from decimal import Decimal
from pathlib import Path
from typing import List, Tuple, Dict

import pandas as pd
from pandas import Series

import demeter
from demeter import (
    Strategy,
    RowData,
    Actuator,
    TokenInfo,
    MarketInfo,
    ChainType,
    AtTimeTrigger,
    PeriodTrigger
)
from demeter.result.metrics.calculator import max_draw_down
from demeter.result import performance_metrics
from demeter.uniswap import UniLpMarket, UniV3Pool
from datetime import date, timedelta, datetime

from strategy_ploter import plot_position_return_decomposition
from remix_dao_utils import RemixDaoUtils, RemixDAOParams
from export_file import export_file, ExportData, export_apr_results
from chaos_lab_utils import standard_deviation_over_last, average_true_range
from rm_types import RescaleFrequency, TestParams, GlobalParams, RangeStrategy

conservative_fluctuation = Decimal(0.03)
ZERO = Decimal(0)


class RemixDaoChaosStrategy(Strategy):

    def __init__(self, _utils: RemixDaoUtils, _params: TestParams, _gp: GlobalParams):
        super().__init__()
        self.utils = _utils
        self.params = _params
        self.was_in_range = False
        self.last_rescale_tick = 0
        self.total_base_fee = ZERO
        self.total_quote_fee = ZERO
        self.export_actions = []
        self.lock_until_time = None
        self.last_price = ZERO
        self.balance_data = {}
        self.tick_spreads = pd.Series([])
        self.total_fee = ZERO
        self.final_total_net_value = ZERO
        self.final_lp_net_value = ZERO
        self.total_base_swap_fee = ZERO
        self.total_quote_swap_fee = ZERO
        self.gp = _gp

    def initialize(self):

        new_trigger = AtTimeTrigger(time=self.params.cal_start_datetime, do=self.first_lp)
        self.triggers.append(new_trigger)

        market_data = self.data[self.utils.market_key]

        # print(market_data.keys()) ==>
        # Index(['netAmount0', 'netAmount1', 'closeTick', 'openTick', 'lowestTick',
        #        'highestTick', 'inAmount0', 'inAmount1', 'currentLiquidity', 'open',
        #        'price', 'low', 'high', 'volume0', 'volume1'],
        #       dtype='object')

        if self.params.range_strategy == RangeStrategy.std:
            self.add_column(self.utils.market_key, "std_1_hr",
                            standard_deviation_over_last(market_data.closeTick, self.params.indicator_length_min))

        if self.params.range_strategy == RangeStrategy.atr:
            self.add_column(self.utils.market_key, "atr_1_hr",
                            average_true_range(market_data.lowestTick, market_data.highestTick, market_data.closeTick,
                                               self.params.indicator_length_min))

        if self.params.rescale_frequency == RescaleFrequency.hourly:
            self.triggers.append(PeriodTrigger(time_delta=timedelta(hours=1), do=self.rescale_work))
        else:
            self.triggers.append(PeriodTrigger(time_delta=timedelta(days=1), do=self.rescale_work))

        dt = datetime(self.params.data_end_date.year, self.params.data_end_date.month, self.params.data_end_date.day,
                      23, 59, 0)
        end_trigger = AtTimeTrigger(time=dt, do=self.calculate_final_result)
        self.triggers.append(end_trigger)

        pass

    # def rebalance(self, current_price: Decimal):
    #
    #     lp_market: UniLpMarket = self.broker.markets[self.utils.market_key]
    #
    #     if self.params.range_strategy == RangeStrategy.remix_dao and not self.utils.params.tick_spread_lower == self.utils.params.tick_spread_upper:
    #         upper = self.utils.params.tick_spread_upper
    #         lower = self.utils.params.tick_spread_lower
    #
    #         #current_price = row_data.prices[eth.name]
    #         total = Decimal(upper + lower)
    #
    #         upper_percent = Decimal(upper) / total
    #         lower_percent = Decimal(1) - upper_percent
    #
    #         orig_base = lp_market.broker.get_token_balance(eth)
    #         orig_quote = lp_market.broker.get_token_balance(usdc)
    #
    #         base_trade, quote_trade = self.utils.calculate_trade(orig_base, orig_quote, current_price, upper_percent,
    #                                                              lower_percent)
    #         if base_trade > 0:
    #             quote_fee, base_spent, quote_got = lp_market.sell(base_trade)
    #             # print(
    #             #     f"sell, quote_fee: {quote_fee}, base_spent: {base_spent}, quote_got: {quote_got}, base: {orig_base}, quote: {orig_quote}, f_base: {lp_market.broker.get_token_balance(eth)}, f_quote: {lp_market.broker.get_token_balance(usdc)}")
    #             # raise RuntimeError("just break")
    #         if quote_trade > 0:
    #             base_needed = quote_trade / current_price
    #             base_fee, quote_spent, base_got = lp_market.buy(base_needed)
    #         #     print(
    #         #         f"buy, base_fee: {base_fee}, quote_spent: {quote_spent}, base_got: {base_got}, base: {base_trade}, quote: {orig_quote}, f_base: {lp_market.broker.get_token_balance(eth)}, f_quote: {lp_market.broker.get_token_balance(usdc)}")
    #         #     raise RuntimeError("just break")
    #         #
    #         # raise RuntimeError("just break no swap")
    #     else:
    #         lp_market.even_rebalance()

    def even_rebalance(self, lp_market: UniLpMarket, base: Decimal | None = None, quote: Decimal | None = None,
                       price: Decimal | None = None) -> tuple[Decimal, Decimal, Decimal | None, Decimal | None]:
        """
        return: final base, final quote, fee in base token, fee in quote token
        """
        if price is None:
            price = lp_market.market_status.data.price

        if quote is None:
            amount_quote = lp_market.broker.get_token_balance(lp_market.quote_token)
        else:
            amount_quote = quote
        if base is None:
            amount_base = lp_market.broker.get_token_balance(lp_market.base_token)
        else:
            amount_base = base

        delta_base = (amount_quote / price - amount_base) / (Decimal(2) + lp_market.pool_info.fee_rate)
        if delta_base >= 0:
            base_fee, quote_spent, base_got = lp_market.buy(delta_base)
            # print(f"buy, base_fee: {base_fee}, quote_spent: {quote_spent}, base_got: {base_got}, base: {amount_base}, quote: {amount_quote}")
            f_base, f_quote, b_fee, q_fee = amount_base + base_got, amount_quote - quote_spent, base_fee, None
            if f_base <= ZERO or f_quote <= ZERO:
                print(
                    f"BAD buy, base_fee: {base_fee}, quote_spent: {quote_spent}, base_got: {base_got}, base: {amount_base}, quote: {amount_quote}, f_base: {f_base}, f_quote: {f_quote}")
            self.balance_data = {"amount_base": amount_base, "amount_quote": amount_quote, "base_fee": base_fee,
                                 "quote_spent": quote_spent, "base_got": base_got, "f_base": f_base, "f_quote": f_quote,
                                 "b_fee": b_fee, "q_fee": q_fee, "price": price}
            return f_base, f_quote, b_fee, q_fee

        delta_quote = (amount_base - amount_quote / price) / (Decimal(2) - lp_market.pool_info.fee_rate)
        if delta_quote >= 0:
            quote_fee, base_spent, quote_got = lp_market.sell(delta_quote)
            # print(f"sell, quote_fee: {quote_fee}, base_spent: {base_spent}, quote_got: {quote_got}, base: {amount_base}, quote: {amount_quote}")
            f_base, f_quote, b_fee, q_fee = amount_base - base_spent, amount_quote + quote_got, None, quote_fee
            if f_base <= ZERO or f_quote <= ZERO:
                print(
                    f"BAD sell, quote_fee: {quote_fee}, base_spent: {base_spent}, quote_got: {quote_got}, base: {amount_base}, quote: {amount_quote}, f_base: {f_base}, f_quote: {f_quote}")
            self.balance_data = {"amount_base": amount_base, "amount_quote": amount_quote, "quote_fee": quote_fee,
                                 "base_spent": base_spent, "quote_got": quote_got, "f_base": f_base,
                                 "f_quote": f_quote, "b_fee": b_fee, "q_fee": q_fee, "price": price}
            return f_base, f_quote, b_fee, q_fee

        self.balance_data = {"msg": "no rebalance"}
        return base, quote, None, None

    def round_to_tick_space(self, lower: int, upper: int) -> tuple[int, int]:
        tick_space = self.utils.params.tick_spacing
        return self.utils.ceiling_tick(lower, tick_space), self.utils.floor_tick(upper, tick_space)

    def calculate_strategy_tick_range(self, row_data: RowData, current_tick: int, lower_tick: int, upper_tick: int,
                                      multiplier: float) -> tuple[int, int]:

        lp_row_data: Series = self.utils.get_lp_row_data(row_data)
        tick_dif = int(lp_row_data.std_1_hr * multiplier) * self.utils.params.tick_spacing
        tick_dif = max(tick_dif, self.utils.params.tick_spacing)
        if current_tick >= upper_tick:
            return self.round_to_tick_space(upper_tick - tick_dif, upper_tick)
        else:
            return self.round_to_tick_space(lower_tick, lower_tick + tick_dif)
        pass

    def calculate_strategy_tick_range_atr(self, row_data: RowData, current_tick: int, lower_tick: int, upper_tick: int,
                                          multiplier: float) -> tuple[int, int]:

        lp_row_data: Series = self.utils.get_lp_row_data(row_data)
        tick_dif = int(lp_row_data.atr_1_hr * multiplier) * self.utils.params.tick_spacing
        tick_dif = max(tick_dif, self.utils.params.tick_spacing)
        if current_tick >= upper_tick:
            return self.round_to_tick_space(upper_tick - tick_dif, upper_tick)
        else:
            return self.round_to_tick_space(lower_tick, lower_tick + tick_dif)
        pass

    def calculate_tick_bounds(self, row_data: RowData, is_first_lp: bool = False) -> (int, int):
        lp_row_data = self.utils.get_lp_row_data(row_data)
        spread_lower = self.utils.params.tick_spread_lower
        spread_upper = self.utils.params.tick_spread_upper
        if is_first_lp:
            spread_lower = spread_upper = self.utils.params.init_tick_spread  # max(self.utils.params.tick_spread_lower, self.utils.params.tick_spread_upper)

        low = lp_row_data.openTick - spread_lower * self.utils.params.tick_spacing
        upper = lp_row_data.openTick + spread_upper * self.utils.params.tick_spacing
        return self.round_to_tick_space(low, upper)

    def calculate_tick_bounds_std(self, row_data: RowData, multiplier: float = 2) -> (int, int):
        lp_row_data = self.utils.get_lp_row_data(row_data)
        tick_dif = int(lp_row_data.std_1_hr * multiplier) * self.utils.params.tick_spacing
        tick_dif = max(tick_dif, self.utils.params.tick_spacing)
        low = lp_row_data.closeTick - tick_dif
        upper = lp_row_data.closeTick + tick_dif
        return self.round_to_tick_space(low, upper)

    def calculate_tick_bounds_atr(self, row_data: RowData, multiplier: float) -> (int, int):
        lp_row_data = self.utils.get_lp_row_data(row_data)
        tick_dif = int(lp_row_data.atr_1_hr * multiplier) * self.utils.params.tick_spacing
        tick_dif = max(tick_dif, self.utils.params.tick_spacing)
        low = lp_row_data.closeTick - tick_dif
        upper = lp_row_data.closeTick + tick_dif
        return self.round_to_tick_space(low, upper)

    def get_balance_base_quote_amounts(self) -> (Decimal, Decimal):
        base = self.broker.get_token_balance(self.gp.base_token)
        quote = self.broker.get_token_balance(self.gp.quote_token)
        # if self.broker.quote_token == usdc:
        #     quote = usdc_balance
        #     base = eth_balance
        # else:
        #     base = usdc_balance
        #     quote = eth_balance

        return base, quote

    def rescale_work(self, row_data: RowData):

        lp_market: UniLpMarket = self.broker.markets[self.utils.market_key]

        if len(lp_market.positions) == 0:
            return

        current_price = row_data.prices[self.gp.base_token.name]
        try:

            if not self.params.aggressive:
                in_lock = self.lock_until_time is not None and row_data.timestamp <= self.lock_until_time
                if in_lock:
                    return
                elif self.lock_until_time is None:
                    # check price fluctuate
                    fluc: Decimal = (current_price - self.last_price) / self.last_price
                    if fluc.copy_abs() > conservative_fluctuation:
                        self.lock_until_time = row_data.timestamp + timedelta(hours=24)
                        # print(
                        #     f"time: {row_data.timestamp.strftime("%Y-%m-%d %H:%M:%S")}, rescale locked until {self.lock_until_time.strftime("%Y-%m-%d %H:%M:%S")} ")
                        return
                    pass
                else:  # just got out of lock ignore price fluctuate, just check if rescale is needed
                    self.lock_until_time = None
                    pass

            # lp_row_data = row_data.market_status[market_key]

            allow_rescale, new_tick_upper, new_tick_lower = self.utils.verify_and_get_new_rescale_tick_boundary(
                row_data, self.was_in_range, self.last_rescale_tick)

            # Check if rescaling is allowed
            if not allow_rescale:
                # print("current condition not allow rescale: " + row_data.timestamp.strftime("%Y-%m-%d %H:%M:%S"))
                return

            tick_spacing, current_tick, _, _ = self.utils.get_tick_info(row_data)
            old_position_info = self.utils.current_position_info

            match self.params.range_strategy:
                case RangeStrategy.remix_dao:
                    # do nothing if not swap

                    if self.params.to_swap:

                        spread = self.utils.params.tick_spread_upper
                        if current_tick < old_position_info[0]:
                            spread = self.utils.params.tick_spread_lower
                        # upper_spread = self.utils.params.tick_spread_upper * tick_spacing
                        # lower_spread = self.utils.params.tick_spread_lower * tick_spacing
                        spread *= tick_spacing
                        low = current_tick - spread
                        high = current_tick + spread
                        new_tick_lower, new_tick_upper = self.round_to_tick_space(low, high)
                    pass
                case RangeStrategy.std:

                    if self.params.to_swap:
                        new_tick_lower, new_tick_upper = self.calculate_tick_bounds_std(row_data,
                                                                                        self.params.indicator_mult)
                    else:
                        new_tick_lower, new_tick_upper = self.calculate_strategy_tick_range(row_data, current_tick,
                                                                                            new_tick_lower,
                                                                                            new_tick_upper,
                                                                                            self.params.indicator_mult)
                case RangeStrategy.atr:
                    if self.params.to_swap:
                        new_tick_lower, new_tick_upper = self.calculate_tick_bounds_atr(row_data,
                                                                                        self.params.indicator_mult)
                    else:
                        new_tick_lower, new_tick_upper = self.calculate_strategy_tick_range_atr(row_data, current_tick,
                                                                                                new_tick_lower,
                                                                                                new_tick_upper,
                                                                                                self.params.indicator_mult)
                    pass

            # Get the current tick info
            # tick_spacing, current_tick, current_tick_lower, current_tick_upper = utils.get_tick_info(row_data)

            # fee0, fee1 = lp_market.collect_fee(self.utils.current_position_info, collect_to_user=False)

            if (new_tick_lower == self.utils.current_position_info[0] and new_tick_upper ==
                self.utils.current_position_info[1]) or new_tick_lower >= new_tick_upper:
                # print(f"same tick, do not rescale: {new_tick_lower}, {new_tick_upper}")
                return

            base_fee, quote_fee = lp_market.collect_fee(self.utils.current_position_info, collect_to_user=True)
            if self.gp.swap_fee:
                pass
            try:
                base, quote = lp_market.remove_liquidity(self.utils.current_position_info, collect=True)
                base_removed, quote_removed = base, quote
            except Exception as e:
                print(f"failed to remove liquidity: {self.utils.current_position_info}")
                raise e

            self.total_base_fee += base_fee
            self.total_quote_fee += quote_fee

            rebalance_base_fee, rebalance_quote_fee = ZERO, ZERO
            try:

                if self.params.to_swap:

                    if self.params.compound:
                        lp_market.even_rebalance()
                    else:
                        bal_base, bal_quote = self.get_balance_base_quote_amounts()
                        bal_base -= self.total_base_fee
                        bal_quote -= self.total_quote_fee

                        base, quote, rebalance_base_fee, rebalance_quote_fee = self.even_rebalance(lp_market, bal_base,
                                                                                                   bal_quote)

                        if rebalance_base_fee is not None:
                            self.total_base_swap_fee += rebalance_base_fee
                        if rebalance_quote_fee is not None:
                            self.total_quote_swap_fee += rebalance_quote_fee

                        base, quote = self.get_balance_base_quote_amounts()

                        base -= self.total_base_fee
                        quote -= self.total_quote_fee

                if self.params.compound:
                    self.utils.current_position_info, base_used, quote_used, _ = lp_market.add_liquidity_by_tick(
                        new_tick_lower, new_tick_upper)
                else:
                    self.utils.current_position_info, base_used, quote_used, _ = lp_market.add_liquidity_by_tick(
                        new_tick_lower, new_tick_upper, base, quote)
            except Exception as e:
                print(f"failed to add liquidity, upper: {new_tick_upper}, lower: {new_tick_lower}")
                raise e

            _, current_tick, _, _ = self.utils.get_tick_info(row_data)

            if base_used == ZERO and quote_used == ZERO:
                print(
                    f"\nno position place: {self.utils.current_position_info}, old_position_info: {old_position_info}, "
                    f"current_tick: {current_tick}, new_tick_lower: {new_tick_lower}, new_tick_upper: {new_tick_upper}, "
                    f"positions: {lp_market.positions}, base: {base}, quote: {quote}, "
                    f"rebalance_base_fee: {rebalance_base_fee}, rebalance_quote_fee: {rebalance_quote_fee}, balance_data: {self.balance_data}")

            ed = ExportData()
            ed.time = row_data.timestamp
            ed.price = current_price
            ed.tick = current_tick

            ed.tick_lower, ed.tick_upper = old_position_info[0], old_position_info[1]

            pos = lp_market.positions[self.utils.current_position_info]
            ed.price_lower, ed.price_upper = pos.lower_price, pos.upper_price

            ed.new_tick_lower, ed.new_tick_upper = self.utils.current_position_info[0], \
                self.utils.current_position_info[1]

            ed.base_fee, ed.quote_fee = base_fee, quote_fee
            ed.base_removed, ed.quote_removed = base_removed, quote_removed
            ed.base_added, ed.quote_added = base_used, quote_used
            ed.was_in_range = self.was_in_range
            ed.total_base_fee, ed.total_quote_fee = self.total_base_fee, self.total_quote_fee

            ed.lp_net_value = lp_market.get_market_balance().net_value
            ed.quote_balance = lp_market.broker.get_token_balance(self.gp.quote_token)
            ed.base_balance = lp_market.broker.get_token_balance(self.gp.base_token)
            ed.total_net_value = ed.lp_net_value + ed.quote_balance + (ed.base_balance * ed.price)
            lp_row_data = self.utils.get_lp_row_data(row_data)
            if self.params.range_strategy == RangeStrategy.std:
                ed.indicator_value = lp_row_data.std_1_hr
            elif self.params.range_strategy == RangeStrategy.atr:
                ed.indicator_value = lp_row_data.atr_1_hr
            else:
                ed.indicator_value = None

            self.export_actions.append(ed)

            pos_info = self.utils.current_position_info
            tick_spread = pos_info[1] - pos_info[0]
            self.tick_spreads.loc[len(self.tick_spreads)] = tick_spread

            # print(
            #     f"rescaled at {row_data.timestamp.strftime("%Y-%m-%d %H:%M:%S")}, removed: {base} / {quote}, fee: {base_fee} / {quote_fee}, used: {base_used} / {quote_used}, "
            #     f"tick: {current_tick}, old_position_info: {old_position_info}, position_info: {str(self.utils.current_position_info)}, was_in_range: {self.was_in_range}, price: {current_price}")

            self.last_rescale_tick = current_tick
            self.was_in_range = False

        finally:
            self.last_price = current_price
        pass

    def first_lp(self, row_data: RowData):

        lp_market: UniLpMarket = self.broker.markets[self.utils.market_key]
        # lp_row_data = row_data.market_status[self.utils.market_key]

        if len(lp_market.positions) > 0:
            raise RuntimeError("shouldn't have any position")

        if not self.params.initial_swap:

            tick_spacing, current_tick, _, _ = self.utils.get_tick_info(row_data)

            if self.gp.token0 == self.gp.quote_token:
                current_tick_lower = current_tick + tick_spacing
            else:
                current_tick_lower = current_tick - tick_spacing

            lower, upper = self.utils.calculate_non_one_tick_spacing_rescale_tick_boundary(tick_spacing, current_tick,
                                                                                           current_tick_lower)
        else:

            lp_market.even_rebalance()
            # need to know how to place initial position
            match self.params.range_strategy:
                case RangeStrategy.remix_dao:
                    (lower, upper) = self.calculate_tick_bounds(row_data, True)
                case RangeStrategy.std:
                    (lower, upper) = self.calculate_tick_bounds_std(row_data, self.params.indicator_mult)
                case RangeStrategy.atr:
                    (lower, upper) = self.calculate_tick_bounds_atr(row_data, self.params.indicator_mult)
                # case RangeStrategy.atr1_5:
                #     (lower, upper) = self.calculate_tick_bounds_atr(row_data, 1.5)

        self.utils.current_position_info, _, _, _ = lp_market.add_liquidity_by_tick(lower, upper)
        # print(
        #     f"\nadding first liquidity, price: {str(row_data.prices[_base_token.name])}, range: {str(lower)} ~ {str(upper)}, position_info: {str(self.utils.current_position_info)}")

        self.was_in_range = True
        self.last_price = row_data.prices[self.gp.base_token.name]

        # lp_market.add_liquidity(lp_row_data.sma_1_day - limit, lp_row_data.sma_1_day + limit)

        # print(f"market_status ({type(lp_row_data).__name__}): {str(lp_row_data)}")
        pass

    def calculate_final_result(self, row_data: RowData):

        lp_market: UniLpMarket = self.broker.markets[self.utils.market_key]
        _, current_tick, _, _ = self.utils.get_tick_info(row_data)
        current_price = row_data.prices[self.gp.base_token.name]
        ed = ExportData()
        ed.time = row_data.timestamp
        ed.price = current_price
        ed.tick = current_tick

        position_info = self.utils.current_position_info
        ed.tick_lower, ed.tick_upper = position_info[0], position_info[1]

        pos = lp_market.positions[position_info]
        ed.price_lower, ed.price_upper = pos.lower_price, pos.upper_price

        ed.new_tick_lower, ed.new_tick_upper = position_info[0], position_info[1]

        base_fee, quote_fee = lp_market.collect_fee(self.utils.current_position_info, collect_to_user=True)

        self.total_base_fee += base_fee
        self.total_quote_fee += quote_fee

        ed.base_fee, ed.quote_fee = base_fee, quote_fee
        ed.base_removed, ed.quote_removed = None, None
        ed.base_added, ed.quote_added = None, None
        ed.was_in_range = self.was_in_range
        ed.total_base_fee, ed.total_quote_fee = self.total_base_fee, self.total_quote_fee

        ed.lp_net_value = lp_market.get_market_balance().net_value
        ed.quote_balance = lp_market.broker.get_token_balance(self.gp.quote_token)
        ed.base_balance = lp_market.broker.get_token_balance(self.gp.base_token)
        ed.total_net_value = ed.lp_net_value + ed.quote_balance + (ed.base_balance * ed.price)
        lp_row_data = self.utils.get_lp_row_data(row_data)
        if self.params.range_strategy == RangeStrategy.std:
            ed.indicator_value = lp_row_data.std_1_hr
        elif self.params.range_strategy == RangeStrategy.atr:
            ed.indicator_value = lp_row_data.atr_1_hr
        else:
            ed.indicator_value = None

        self.export_actions.append(ed)

        self.total_fee = self.total_quote_fee + self.total_base_fee * current_price
        self.final_total_net_value = ed.total_net_value
        self.final_lp_net_value = ed.lp_net_value

        pass

    def on_bar(self, row_data: RowData):
        """
        Called after triggers on each iteration, at this time, market are not updated yet(Take uniswap market for example, fee of this minute are not added to positions).

        :param row_data: data in this iteration, include current timestamp, price, all columns data, and indicators(such as simple moving average)
        :type row_data: RowData
        """

        if self.utils.current_position_info is None:
            return

        lp_row_data = row_data.market_status[self.utils.market_key]
        # current_tick = lp_row_data.closeTick

        self.was_in_range = self.was_in_range or (
            # check if the tick range ever overlaps the LP range
                self.utils.current_position_info[0] <= lp_row_data.highestTick and
                self.utils.current_position_info[1] >= lp_row_data.lowestTick)

        pass

    def after_bar(self, row_data: RowData):
        """
        called after market are updated on each iteration

        :param row_data: data in this iteration, include current timestamp, price, all columns data, and indicators(such as simple moving average)
        :type row_data: RowData
        """

        # pos_info = self.utils.current_position_info
        # if pos_info is not None:
        #     tick_spread = pos_info[1] - pos_info[0]
        #     self.tick_spreads.loc[len(self.tick_spreads)] = tick_spread

        pass

    def finalize(self):
        """
        this will run after all the data processed. You can access broker.account_status, broker.market.status to do some calculation

        """
        # lp_market: UniLpMarket = self.broker.markets[market_key]
        # fee0, fee1 = lp_market.collect_fee(self.utils.current_position_info, collect_to_user=False)
        # self.total_base_fee += fee0
        # self.total_quote_fee += fee1

        # lp_market.broker.add_to_balance(token0, self.total_base_fee)
        # lp_market.broker.add_to_balance(token1, self.total_quote_fee)

        # print(f"total fee0: {self.total_base_fee}, fee1: {self.total_quote_fee}")

        # account_status: List[AccountStatus] = self.account_status
        # print(f"account_status: {str(account_status)}")

        # if you need a dataframe. you can call account_status_df
        # do not call account_status_df in on_bar because it will slow the backtesting.
        # account_status_df: pd.DataFrame = self.account_status_df
        # print(f"account_status_df: {str(account_status_df)}")

        # actions, this record all the actions such as add/remove liquidity, buy, sell,
        # As each action has different parameter, its type is List[BaseAction],
        # and it can't be converted into a dataframe.
        # actions: List[BaseAction] = self.actions
        # print(f"actions: {str(actions)}")

        # export_file(f"{self.params.folder}/result_{self.gp.init_quote}_{self.params.report_name}.csv", self.export_actions)
        export_file(f"{self.params.folder}/result_{self.params.report_name}.csv", self.export_actions)
        pass


def run_test(rm_params: RemixDAOParams, params: TestParams, gp: GlobalParams, processed_data: pd.DataFrame | None) -> \
        Dict[str, Decimal]:
    try:
        market_key = MarketInfo("lp")

        actuator = Actuator()  # declare actuator
        broker = actuator.broker
        pool = UniV3Pool(gp.token0, gp.token1, gp.fee, gp.quote_token)  # declare pool, Arbitrum One
        market = UniLpMarket(market_key, pool)

        broker.add_market(market)
        broker.set_balance(gp.quote_token, gp.init_quote)
        broker.set_balance(gp.base_token, 0)

        # rm_params = RemixDAOParams()
        # rm_params.tick_spread_upper = 60
        # rm_params.tick_spread_lower = 60
        # rm_params.tick_upper_boundary_offset = 0
        # rm_params.tick_lower_boundary_offset = 0
        # rm_params.rescale_tick_upper_boundary_offset = 10
        # rm_params.rescale_tick_lower_boundary_offset = 10
        # rm_params.tick_spacing = 10
        # rm_params.rescale_tick_tolerance = 10
        utils = RemixDaoUtils(market, market_key, bull_params=rm_params, bear_params=rm_params)
        strat = RemixDaoChaosStrategy(utils, params, gp)
        actuator.strategy = strat
        market.data_path = "../real-data"
        if processed_data is not None:
            # print("use prepared data")
            pd = copy.deepcopy(processed_data)
            market.add_statistic_column(pd)
            market.data = pd
        else:

            start = datetime.now()
            market.load_data(
                gp.chain_name, gp.contract_address, params.data_start_date, params.data_end_date
            )

            dif = datetime.now() - start
            print(f"load data: {dif.total_seconds()} seconds")

        # start = datetime.now()
        actuator.set_price(market.get_price_from_data())
        actuator.run(False)  # run test
        # dif = datetime.now() - start
        # print(f"run: {dif.total_seconds()} seconds")

        price_name = gp.base_token.name.upper()
        metrics: dict[str, Decimal] = dict(map(lambda item: (item[0].name, item[1]), performance_metrics(
            actuator.account_status_df["net_value"], benchmark=actuator.account_status_df["price"][price_name]
        ).items()))
        # print(metrics)
        metrics["action_count"] = Decimal(len(strat.export_actions))

        spread_mean = strat.tick_spreads.mean()
        spread_median = strat.tick_spreads.median()
        if math.isnan(spread_mean):
            spread_mean = Decimal(-1)
        metrics["spread_mean"] = Decimal(int(spread_mean))
        metrics["spread_median"] = spread_median

        metrics["lp_net_value"] = strat.final_lp_net_value
        metrics["total_net_value"] = strat.final_total_net_value
        metrics["total_fee"] = strat.total_fee
        metrics["fee_to_total_net_value"] = strat.total_fee / strat.final_total_net_value
        metrics["total_base_swap_fee"] = strat.total_base_swap_fee
        metrics["total_quote_swap_fee"] = strat.total_quote_swap_fee

        bench_price = actuator.account_status_df["price"][price_name].apply(lambda x: float(x))
        metrics["benchmark_max_draw_down"] = max_draw_down(bench_price)

        return metrics
    except Exception as e:
        print(f"error for {params.range_strategy.value}, {str(rm_params)}")
        raise e
    # plot_position_return_decomposition(actuator.account_status_df, actuator.token_prices[_base_token.name], market_key)


def process_for_date(csd: datetime, dsd: date, ded: date):
    demeter.Formats.global_num_format = ".4g"  # change out put formats here
    usdc = TokenInfo(name="usdc", decimal=6)
    eth = TokenInfo(name="eth", decimal=18)
    btc = TokenInfo(name="btc", decimal=8)

    base_token, quote_token, init_quote = eth, usdc, Decimal(1000000)  #  USDC

    # base_token, quote_token, init_quote = btc, eth, Decimal(100)  # ETH
    # base_token, quote_token, init_quote = eth, btc, Decimal(1)  # BTC

    # token0, token1 = btc, eth
    # contract_address, fee, chain_name = "0x4585FE77225b41b697C938B018E2Ac67Ac5a20c0", 0.05, ChainType.ethereum.name  # wbtc/weth  2021-05-13
    # contract_address, fee, chain_name = "0x2f5e87C9312fa29aed5c179E456625D79015299c", 0.05, ChainType.arbitrum.name # wbtc/weth
    # contract_address, fee, chain_name = "0xCBCdF9626bC03E24f779434178A73a0B4bad62eD", 0.3, ChainType.ethereum.name # wbtc/weth

    # token0, token1 = usdc, eth
    # contract_address, fee, chain_name = "0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640", 0.05, ChainType.ethereum.name  # weth/usdc May-05-2021
    token0, token1 = eth, usdc
    contract_address, fee, chain_name = "0xC6962004f452bE9203591991D15f6b388e09E8D0", 0.05, ChainType.arbitrum.name  # weth/usdc

    # pool = UniV3Pool(btc, eth, _fee, _quote_token)
    _tick_spacing = int(fee * 200)  # 10  # should simply be fee * 200
    _aggressive = True
    _compound = False
    _folder_prefix = f"rm-mark-fill-{quote_token.name.lower()}"

    gp = GlobalParams(token0=token0, token1=token1, fee=fee, init_quote=init_quote,
                      quote_token=quote_token, base_token=base_token,
                      chain_name=chain_name, contract_address=contract_address, swap_fee=False)

    # [init, lower, upper], [lower/upper], [lower, upper]
    # _remix_spreads = [[90, 120, 30], [60, 120, 40]]  #[[40], [90], [120], [150], [180], [210]]  # [30, 60, 90, 120, 150, 180, 210, 240] [40, 90]
    # _remix_spreads = [[60, 120, 120],
    #                   [30, 120, 120],
    #                   [30, 150, 150],
    #                   [30, 180, 180],
    #                   [30, 210, 210],
    #                   [90, 120, 40],
    #                   [90, 120, 30],
    #                   [60, 120, 40],
    #                   [60, 120, 30],
    #                   [30, 120, 60],
    #                   [30, 120, 40],
    #                   [30, 120, 30],
    #                   [30, 90, 40],
    #                   [30, 40, 90],
    #                   [60, 90, 90],
    #                   [30, 90, 90],
    #                   [60, 40, 40],
    #                   [30, 40, 40],
    #                   ]

    # _remix_spreads = [[25, 120, 120], [75, 120, 120]]

    # _remix_spreads = [[0, 120, 120], [0, 60, 60], [25, 120, 120], [75, 60, 60], [75, 120, 120], [25, 60, 60],
    #                     [120, 120, 30], [120, 180, 60], [0, 120, 180], [25, 120, 180]]
    # _remix_spreads = [[0, 120, 120], [25, 120, 120]]

    # BTC/ETH base case
    # _remix_spreads = [[0, 40, 40], [0, 80, 80], [0, 120, 120],
    #                   [40, 40, 40], [80, 80, 80], [120, 120, 120]]

    # _remix_spreads = [[0, 120, 30], [0, 180, 30]]
    _remix_spreads = [[120, 30, 210], [120, 30, 240], [120, 30, 270], [120, 30, 300]]

    # _remix_spreads = [[75, 60, 60], [25, 120, 120], [120, 120, 120], [120, 60, 60], [120, 180, 30],
    #                   [120, 180, 60],
    #                   [120, 180, 90],
    #                   [120, 120, 30],
    #                   [120, 120, 60],
    #                   [120, 120, 90],
    #                   [120, 240, 480]]

    # _remix_spreads = [
    #     [25, 120, 30],
    #     [25, 120, 210],
    #     [25, 120, 240],
    #     [25, 120, 270],
    #     [25, 120, 300],
    #     [25, 180, 30],
    #     [25, 180, 180],
    #     [25, 180, 210],
    #     [25, 180, 240],
    #     [25, 180, 270],
    #     [25, 180, 300],
    #     [75, 120, 30],
    #     [75, 180, 30],
    #     [120, 120, 210],
    #     [120, 120, 240],
    #     [120, 120, 270],
    #     [120, 120, 300],
    #     [120, 180, 210],
    #     [120, 180, 240],
    #     [120, 180, 270],
    #     [120, 180, 300],
    #     [120, 270, 270],
    #     [120, 300, 300]
    # ]

    # _remix_spreads = [[120, 180, 30], [120, 180, 120], [120, 300, 150]]

    # _remix_spreads = [[25, 120, 60], [25, 180, 90], [75, 120, 60], [75, 180, 90]]

    # _remix_spreads = [[60, 90, 90],
    #                   [30, 90, 90],
    #                   [60, 40, 40],
    #                   [30, 40, 40],
    #                   ]
    _rescale_frequencies = [RescaleFrequency.hourly]  # RescaleFrequency.hourly,

    _param_with_offset = RemixDAOParams(  # offset + range
        tick_spread_upper=60,
        tick_spread_lower=60,
        tick_upper_boundary_offset=0,
        tick_lower_boundary_offset=0,
        rescale_tick_upper_boundary_offset=10,
        rescale_tick_lower_boundary_offset=10,
        # rescale_tick_tolerance=10,
        init_tick_spread=120,
        tick_spacing=_tick_spacing)
    _param_no_offset = RemixDAOParams(  # offset + range
        tick_spread_upper=60,
        tick_spread_lower=60,
        tick_upper_boundary_offset=0,
        tick_lower_boundary_offset=0,
        rescale_tick_upper_boundary_offset=0,
        rescale_tick_lower_boundary_offset=0,
        # rescale_tick_tolerance=10,
        init_tick_spread=120,
        tick_spacing=_tick_spacing)

    remix_params = []

    _cmp = ""
    if _compound:
        _cmp = "_cmp"
    # csd = _cal_start_date
    # dsd = _data_start_date
    # ded = _data_end_date

    folder = f"result/{_folder_prefix}-{init_quote}-{csd.strftime("%Y%m%d")}-{ded.strftime("%Y%m%d")}"
    Path(folder).mkdir(parents=True, exist_ok=True)
    parameters: List[Tuple[RemixDAOParams, TestParams]] = []

    for rescale_frequency in _rescale_frequencies:
        for spread in _remix_spreads:
            # lower = upper
            no_offset = copy.copy(_param_no_offset)
            with_offset = copy.copy(_param_with_offset)

            init: int | None = None
            if len(spread) == 1:
                lower = upper = spread[0]
            elif len(spread) == 2:
                lower = spread[0]
                upper = spread[1]
            else:
                init = spread[0]
                lower = spread[1]
                upper = spread[2]

            with_offset.tick_spread_lower = lower
            with_offset.tick_spread_upper = upper
            no_offset.tick_spread_lower = lower
            no_offset.tick_spread_upper = upper

            if init is not None:
                with_offset.init_tick_spread = init
                no_offset.init_tick_spread = init

            initial_swap = with_offset.init_tick_spread != 0

            # print(f"initial_swap: {initial_swap}")

            # if spread == 120:
            #     with_offset.init_tick_spread = 25
            #     no_offset.init_tick_spread = 25
            # else:
            #     with_offset.init_tick_spread = 75
            #     no_offset.init_tick_spread = 75

            # if lower == upper and lower == 120:
            #     with_offset.init_tick_spread = 60
            #     no_offset.init_tick_spread = 60
            # else:
            #     with_offset.init_tick_spread = 30
            #     no_offset.init_tick_spread = 30

            parameters.append((with_offset,
                               TestParams(range_strategy=RangeStrategy.remix_dao, indicator_mult=1,
                                          report_name=f"{init_quote}{quote_token.name}_{RangeStrategy.remix_dao.name}_with_offset_{with_offset.init_tick_spread}_{with_offset.tick_spread_lower}_{with_offset.tick_spread_upper}_{rescale_frequency.name}{_cmp}",
                                          indicator_length_hr=1, to_swap=False,
                                          aggressive=_aggressive, compound=_compound,
                                          rescale_frequency=rescale_frequency,
                                          cal_start_datetime=csd, data_start_date=dsd, data_end_date=ded, folder=folder,
                                          initial_swap=initial_swap)
                               ))
            parameters.append((no_offset,
                               TestParams(range_strategy=RangeStrategy.remix_dao, indicator_mult=1,
                                          report_name=f"{init_quote}{quote_token.name}_{RangeStrategy.remix_dao.name}_without_offset_{no_offset.init_tick_spread}_{no_offset.tick_spread_lower}_{no_offset.tick_spread_upper}_{rescale_frequency.name}{_cmp}",
                                          indicator_length_hr=1, to_swap=False,
                                          aggressive=_aggressive, compound=_compound,
                                          rescale_frequency=rescale_frequency,
                                          cal_start_datetime=csd, data_start_date=dsd, data_end_date=ded, folder=folder,
                                          initial_swap=initial_swap)
                               ))
            # parameters.append((no_offset,
            #                    TestParams(range_strategy=RangeStrategy.remix_dao, indicator_mult=1,
            #                               report_name=f"{init_quote}{quote_token.name}_{RangeStrategy.remix_dao.name}_rebalance_{no_offset.init_tick_spread}_{no_offset.tick_spread_lower}_{no_offset.tick_spread_upper}_{rescale_frequency.name}{_cmp}",
            #                               indicator_length_hr=1, to_swap=True,
            #                               aggressive=_aggressive, compound=_compound, rescale_frequency=rescale_frequency,
            #                               cal_start_datetime=csd, data_start_date=dsd, data_end_date=ded, folder=folder,
            #                               initial_swap=initial_swap)
            #                    ))

            # upper = lower * 2
            # no_offset_1 = copy.copy(_param_no_offset)
            # with_offset_1 = copy.copy(_param_with_offset)
            # lower_spread = spread
            # upper_spread = spread * 2
            # with_offset_1.tick_spread_lower = lower_spread
            # with_offset_1.tick_spread_upper = upper_spread
            # no_offset_1.tick_spread_lower = lower_spread
            # no_offset_1.tick_spread_upper = upper_spread

            # parameters.append((with_offset_1,
            #                    TestParams(range_strategy=RangeStrategy.remix_dao, indicator_mult=1,
            #                               report_name=f"{init_quote}{quote_token.name}_{RangeStrategy.remix_dao.name}_with_offset_{with_offset_1.init_tick_spread}_{lower_spread}_{upper_spread}_{rescale_frequency.name}{_cmp}",
            #                               indicator_length_hr=1, to_swap=False,
            #                               aggressive=_aggressive, compound=_compound, rescale_frequency=rescale_frequency,
            #                               cal_start_datetime=csd, data_start_date=dsd, data_end_date=ded, folder=folder,
            #             #                               initial_swap=initial_swap)
            #                    ))
            # parameters.append((no_offset_1,
            #                    TestParams(range_strategy=RangeStrategy.remix_dao, indicator_mult=1,
            #                               report_name=f"{init_quote}{quote_token.name}_{RangeStrategy.remix_dao.name}_without_offset_{no_offset_1.init_tick_spread}_{lower_spread}_{upper_spread}_{rescale_frequency.name}{_cmp}",
            #                               indicator_length_hr=1, to_swap=False,
            #                               aggressive=_aggressive, compound=_compound, rescale_frequency=rescale_frequency,
            #                               cal_start_datetime=csd, data_start_date=dsd, data_end_date=ded, folder=folder,
            #             #                               initial_swap=initial_swap)
            #                    ))
            # parameters.append((no_offset_1,
            #                    TestParams(range_strategy=RangeStrategy.remix_dao, indicator_mult=1,
            #                               report_name=f"{init_quote}{quote_token.name}_{RangeStrategy.remix_dao.name}_rebalance_{no_offset_1.init_tick_spread}_{lower_spread}_{upper_spread}_{rescale_frequency.name}{_cmp}",
            #                               indicator_length_hr=1, to_swap=True,
            #                               aggressive=_aggressive, compound=_compound, rescale_frequency=rescale_frequency,
            #                               cal_start_datetime=csd, data_start_date=dsd, data_end_date=ded, folder=folder,
            #             #                               initial_swap=initial_swap)
            #                    ))

            # lower = upper * 2
            # no_offset_2 = copy.copy(_param_no_offset)
            # with_offset_2 = copy.copy(_param_with_offset)
            # lower_spread = spread * 2
            # upper_spread = spread
            # with_offset_2.tick_spread_lower = lower_spread
            # with_offset_2.tick_spread_upper = upper_spread
            # no_offset_2.tick_spread_lower = lower_spread
            # no_offset_2.tick_spread_upper = upper_spread

            # parameters.append((with_offset_2,
            #                    TestParams(range_strategy=RangeStrategy.remix_dao, indicator_mult=1,
            #                               report_name=f"{init_quote}{quote_token.name}_{RangeStrategy.remix_dao.name}_with_offset_{with_offset_2.init_tick_spread}_{lower_spread}_{upper_spread}_{rescale_frequency.name}{_cmp}",
            #                               indicator_length_hr=1, to_swap=False,
            #                               aggressive=_aggressive, compound=_compound, rescale_frequency=rescale_frequency,
            #                               cal_start_datetime=csd, data_start_date=dsd, data_end_date=ded, folder=folder,
            #             #                               initial_swap=initial_swap)
            #                    ))
            # parameters.append((no_offset_2,
            #                    TestParams(range_strategy=RangeStrategy.remix_dao, indicator_mult=1,
            #                               report_name=f"{init_quote}{quote_token.name}_{RangeStrategy.remix_dao.name}_without_offset_{no_offset_2.init_tick_spread}_{lower_spread}_{upper_spread}_{rescale_frequency.name}{_cmp}",
            #                               indicator_length_hr=1, to_swap=False,
            #                               aggressive=_aggressive, compound=_compound, rescale_frequency=rescale_frequency,
            #                               cal_start_datetime=csd, data_start_date=dsd, data_end_date=ded, folder=folder,
            #             #                               initial_swap=initial_swap)
            #                    ))
            # parameters.append((no_offset_2,
            #                    TestParams(range_strategy=RangeStrategy.remix_dao, indicator_mult=1,
            #                               report_name=f"{init_quote}{quote_token.name}_{RangeStrategy.remix_dao.name}_rebalance_{no_offset_2.init_tick_spread}_{lower_spread}_{upper_spread}_{rescale_frequency.name}{_cmp}",
            #                               indicator_length_hr=1, to_swap=True,
            #                               aggressive=_aggressive, compound=_compound, rescale_frequency=rescale_frequency,
            #                               cal_start_datetime=csd, data_start_date=dsd, data_end_date=ded, folder=folder,
            #             #                               initial_swap=initial_swap)
            #                    ))

    # ATRs
    # rebalance_parameters: List[Tuple[RemixDAOParams, TestParams]] = [
    #
    #     (param_with_offset,
    #      TestParams(RangeStrategy.atr, 3, "3ATR_1hr_rebalance", indicator_length_hr=1, to_swap=True,
    #                 aggressive=_aggressive, compound=_compound)),
    #     # (param_with_offset,
    #     #  TestParams(RangeStrategy.atr, 3, "3ATR_4hr_rebalance", indicator_length_hr=4, to_swap=True,
    #     #             aggressive=_aggressive, compound=_compound)),
    #     (param_with_offset,
    #      TestParams(RangeStrategy.atr, 3, "3ATR_24hr_rebalance", indicator_length_hr=24, to_swap=True,
    #                 aggressive=_aggressive, compound=_compound)),
    #
    #     (param_with_offset,
    #      TestParams(RangeStrategy.std, 2, "2STD_1hr_rebalance", indicator_length_hr=1, to_swap=True,
    #                 aggressive=_aggressive, compound=_compound)),
    #     # (param_with_offset,
    #     #  TestParams(RangeStrategy.std, 2, "2STD_4hr_rebalance", indicator_length_hr=4, to_swap=True,
    #     #             aggressive=_aggressive, compound=_compound)),
    #     (param_with_offset,
    #      TestParams(RangeStrategy.std, 2, "2STD_24hr_rebalance", indicator_length_hr=24, to_swap=True,
    #                 aggressive=_aggressive, compound=_compound)),
    #
    # ]
    #
    # parameters.extend(rebalance_parameters)

    # preload data to speed things up
    print(f"preload data {dsd.strftime("%Y%m%d")} ~ {ded.strftime("%Y%m%d")}")
    market_key = MarketInfo("lp")
    pool = UniV3Pool(token0, token1, fee, quote_token)
    market = UniLpMarket(market_key, pool)
    market.data_path = "../real-data"
    market.load_data(chain_name, contract_address, dsd, ded)

    result = list(map(lambda p: (p[1].report_name, run_test(p[0], p[1], gp, market.data)), parameters))
    export_apr_results(f"{folder}/apr_remix_{init_quote}_results.csv", result)
    pass


if __name__ == "__main__":

    date_ranges: List[tuple[datetime, date, date]] = [
        # (_cal_start_date, _data_start_date, _data_end_date)
        # total-market
        (datetime(2023, 10, 16, 0, 0, 0), date(2023, 10, 6), date(2024, 9, 3)),
        # bull-market
        (datetime(2023, 10, 16, 0, 0, 0), date(2023, 10, 6), date(2024, 5, 26)),
        # bear-market
        (datetime(2024, 5, 27, 0, 0, 0), date(2024, 5, 1), date(2024, 9, 3)),
        # 20240311 - 20240903
        (datetime(2024, 3, 11, 0, 0, 0), date(2024, 3, 11), date(2024, 9, 3)),
        
        # BTC/ETH BEAR 20220613 - 20220912
        # (datetime(2022, 6, 13, 0, 0, 0), date(2022, 6, 13), date(2022, 9, 12)),
        # 20240311 ~ 20240903
        # (datetime(2024, 3, 11, 0, 0, 0), date(2024, 3, 11), date(2024, 9, 3)),

        # ISAO cases
        # ・2021/05/04~2024/09/30
        # (datetime(2021, 5, 13, 0, 0, 0), date(2021, 5, 13), date(2024, 9, 30)),
        # #  2021/05/04~2021/12/31
        # (datetime(2021, 5, 13, 0, 0, 0), date(2021, 5, 13), date(2021, 12, 31)),
        # # ・2022/01/01~2022/12/31
        # (datetime(2022, 1, 1, 0, 0, 0), date(2022, 1, 1), date(2022, 12, 31)),
        # # ・2023/01/01~2023/12/31
        # (datetime(2023, 1, 1, 0, 0, 0), date(2023, 1, 1), date(2023, 12, 31)),
        # # ・2024/01/01~2024/09/30
        # (datetime(2024, 1, 1, 0, 0, 0), date(2024, 1, 1), date(2024, 9, 30)),

    ]

    threads = map(lambda dr: multiprocessing.Process(target=process_for_date, args=dr), date_ranges)
    for t in threads:
        t.start()

    for t in threads:
        t.join()
