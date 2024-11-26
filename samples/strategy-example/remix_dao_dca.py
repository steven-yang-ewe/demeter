import copy

from dataclasses import dataclass
import math
import multiprocessing
from decimal import Decimal
from enum import Enum
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
from demeter.core.math_helper import max_draw_down_fast
from demeter.result import performance_metrics
from demeter.uniswap import UniLpMarket, UniV3Pool, V3CoreLib, base_unit_price_to_sqrt_price_x96
from datetime import date, timedelta, datetime

from remix_dao_utils import RemixDaoUtils, RemixDAOParams, MonthlyTrigger, performance_metrics_for_dca
from export_file import export_file, ExportData, export_apr_results
from chaos_lab_utils import standard_deviation_over_last, average_true_range
from rm_types import RescaleFrequency, TestParams, GlobalParams, RangeStrategy, PriceActionLog, DcaTiming

conservative_fluctuation = Decimal(0.03)
ZERO = Decimal(0)
ONE = Decimal(1)


class RemixDaoDcaStratStrategy(Strategy):

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
        self.pa_upper: List[PriceActionLog] = []
        self.pa_lower: List[PriceActionLog] = []
        self.dca_usdc_accumulated: Decimal = ZERO
        self.dca_total_added: Decimal = ZERO


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

        if self.gp.dca_usdc_amount > ZERO:
            self.triggers.append(MonthlyTrigger(date_of_month=1, do=self.add_dca_fund))
        
        for flip_date in self.params.flip_param_dates:
            self.triggers.append(AtTimeTrigger(time=flip_date, do=self.flip_param))
        pass

    def add_dca_fund(self, row: RowData):

        if row.timestamp.year == self.params.cal_start_datetime.year and row.timestamp.month == self.params.cal_start_datetime.month:
            return  # skip first month

        if self.gp.dca_add_if_non_empty or self.dca_usdc_accumulated == ZERO:
            self.dca_usdc_accumulated += self.gp.dca_usdc_amount

    def flip_param(self, row_data: RowData):
        if self.utils.bull:
            self.utils.use_bear_params()
        else:
            self.utils.use_bull_params()

    @staticmethod
    def calculate_quantity(price: Decimal, amount_b: Decimal) -> Decimal:
        # Calculate quantity of Token A
        quantity_a = amount_b / price
        return Decimal(quantity_a)

    def swap_quote_to_base_liquidity_ratio(self, lp_market: UniLpMarket, total_quote_amount: Decimal) -> tuple[Decimal, Decimal]:

        position = lp_market.get_position(self.utils.current_position_info)
        price = lp_market.market_status.data.price
        sqrt_price_x96 = base_unit_price_to_sqrt_price_x96(
            price,
            lp_market.pool_info.token0.decimal,
            lp_market.pool_info.token1.decimal,
            lp_market.pool_info.is_token0_quote,
        )
        amount0, amount1 = V3CoreLib.get_token_amounts(lp_market.pool_info, self.utils.current_position_info,
                                                       sqrt_price_x96, position.liquidity)
        base, quote = (amount1, amount0) if lp_market.pool_info.is_token0_quote else (amount0, amount1)
        quote_in_base = self.calculate_quantity(price, quote)
        base_percentage = base / (base + quote_in_base)
        # delta_base = (amount_quote / price - amount_base) / (Decimal(2) + lp_market.pool_info.fee_rate)
        swap_amount = total_quote_amount / ((ONE / base_percentage) + lp_market.pool_info.fee_rate)
        fee, to_amount = lp_market.swap(swap_amount, self.gp.quote_token, self.gp.base_token)
        to_amount = Decimal(to_amount)
        final_quote = total_quote_amount - swap_amount

        # print(f"liquidity amount0: {amount0}, amount1: {amount1}, quote_in_base: {quote_in_base}, price: {price}")
        # print(f"total_quote_amount: {total_quote_amount}, swap_amount: {swap_amount}, fee: {fee}, to_amount: {to_amount}, final_quote: {final_quote}, base_percentage: {base_percentage}")

        return to_amount, final_quote


    def check_and_add_dca(self, row_data: RowData, lp_market: UniLpMarket):

        if self.dca_usdc_accumulated <= ZERO:
            return

        timing = self.gp.dca_add_timing
        _, current_tick, current_tick_lower, current_tick_upper = self.utils.get_tick_info(row_data)
        base_used, quote_used = ZERO, ZERO
        if lp_market.pool_info.is_token0_quote:  # quote is basically USDC

            if current_tick > current_tick_upper:  # liquidity is all in base token

                if timing == DcaTiming.quote_only:
                    return

                # old_bal = self.broker.get_token_balance(self.gp.token0)
                self.broker.add_to_balance(self.gp.token0, self.dca_usdc_accumulated)
                # new_bal = self.broker.get_token_balance(self.gp.token0)
                fee, to_amount = lp_market.swap(from_amount=self.dca_usdc_accumulated, from_token=self.gp.token0,
                               to_token=self.gp.token1)

                # print(f"DCA swap => from_amount: {self.dca_usdc_accumulated}, to_amount: {to_amount}, to_token: {self.gp.token1}, fee: {fee}, old_bal: {old_bal}, new_bal: {new_bal}")
                lower, upper = self.utils.current_position_info[0], self.utils.current_position_info[1]
                created_position, base_used, quote_used, _ = lp_market.add_liquidity_by_tick(lower, upper, base_max_amount=to_amount, quote_max_amount=ZERO)
                # new_bal = self.broker.get_token_balance(self.gp.token0)
                # print(
                #     f"DCA position added => base_used: {base_used}, quote_used: {quote_used}, created_position: {created_position}, balance after add: {new_bal}")

                ## put higher
                # current_tick_lower = current_tick + tick_spacing

            elif current_tick < current_tick_lower:  # liquidity is all in quote token

                if timing == DcaTiming.base_only:
                    return
                # old_bal = self.broker.get_token_balance(self.gp.token0)
                self.broker.add_to_balance(self.gp.token0, self.dca_usdc_accumulated)
                # new_bal = self.broker.get_token_balance(self.gp.token0)

                lower, upper = self.utils.current_position_info[0], self.utils.current_position_info[1]
                created_position, base_used, quote_used, _ = lp_market.add_liquidity_by_tick(lower, upper,
                                                                                             base_max_amount=ZERO,
                                                                                             quote_max_amount=self.dca_usdc_accumulated)
            else:  # in range TODO implementation
                if timing == DcaTiming.always:

                    self.broker.add_to_balance(self.gp.token0, self.dca_usdc_accumulated)
                    dca_base, dca_quote = self.swap_quote_to_base_liquidity_ratio(lp_market, self.dca_usdc_accumulated)
                    lower, upper = self.utils.current_position_info[0], self.utils.current_position_info[1]
                    created_position, base_used, quote_used, _ = lp_market.add_liquidity_by_tick(lower, upper,
                                                                                                 base_max_amount=dca_base,
                                                                                                 quote_max_amount=dca_quote)
                    # print(f"dca_base: {dca_base}, dca_quote: {dca_quote}, base_used: {base_used}, quote_used: {quote_used}")

        else:
            if current_tick < current_tick_lower:  # liquidity is all in base token
                if timing == DcaTiming.quote_only:
                    return

                # old_bal = self.broker.get_token_balance(self.gp.token1)
                self.broker.add_to_balance(self.gp.token1, self.dca_usdc_accumulated)
                # new_bal = self.broker.get_token_balance(self.gp.token1)
                fee, to_amount = lp_market.swap(from_amount=self.dca_usdc_accumulated, from_token=self.gp.token1,
                               to_token=self.gp.token0)

                # print(f"DCA swap => from_amount: {self.dca_usdc_accumulated}, to_amount: {to_amount}, to_token: {self.gp.token0}, fee: {fee}, old_bal: {old_bal}, new_bal: {new_bal}")
                self.dca_total_added += self.dca_usdc_accumulated
                self.dca_usdc_accumulated = ZERO
                lower, upper = self.utils.current_position_info[0], self.utils.current_position_info[1]
                created_position, base_used, quote_used, _ = lp_market.add_liquidity_by_tick(lower, upper,
                                                                                             base_max_amount=to_amount,
                                                                                             quote_max_amount=ZERO)
                # new_bal = self.broker.get_token_balance(self.gp.token1)
                # print(
                #     f"DCA position added => base_used: {base_used}, quote_used: {quote_used}, created_position: {created_position}, balance after add: {new_bal}")
            elif current_tick > current_tick_upper:  # liquidity is all in quote token

                if timing == DcaTiming.base_only:
                    return
                # old_bal = self.broker.get_token_balance(self.gp.token0)
                self.broker.add_to_balance(self.gp.token1, self.dca_usdc_accumulated)
                # new_bal = self.broker.get_token_balance(self.gp.token0)

                lower, upper = self.utils.current_position_info[0], self.utils.current_position_info[1]
                created_position, base_used, quote_used, _ = lp_market.add_liquidity_by_tick(lower, upper,
                                                                                             base_max_amount=ZERO,
                                                                                             quote_max_amount=self.dca_usdc_accumulated)
            else:  # in range TODO implementation
                if timing == DcaTiming.always:
                    self.broker.add_to_balance(self.gp.token1, self.dca_usdc_accumulated)
                    dca_base, dca_quote = self.swap_quote_to_base_liquidity_ratio(lp_market, self.dca_usdc_accumulated)
                    lower, upper = self.utils.current_position_info[0], self.utils.current_position_info[1]
                    created_position, base_used, quote_used, _ = lp_market.add_liquidity_by_tick(lower, upper,
                                                                                                 base_max_amount=dca_base,
                                                                                                 quote_max_amount=dca_quote)
                    # print(
                    #     f"dca_base: {dca_base}, dca_quote: {dca_quote}, base_used: {base_used}, quote_used: {quote_used}")


        if base_used > ZERO or quote_used > ZERO:

            self.dca_total_added += self.dca_usdc_accumulated
            self.dca_usdc_accumulated = ZERO

            position = lp_market.get_position(self.utils.current_position_info)

            sqrt_price_x96 = base_unit_price_to_sqrt_price_x96(
                lp_market.market_status.data.price,
                lp_market.pool_info.token0.decimal,
                lp_market.pool_info.token1.decimal,
                lp_market.pool_info.is_token0_quote,
            )
            amount0, amount1 = V3CoreLib.get_token_amounts(lp_market.pool_info, self.utils.current_position_info, sqrt_price_x96, position.liquidity)
            # print(f"lp_amount0: {amount0}, lp_amount1: {amount1}")

            ed = ExportData()
            ed.time = row_data.timestamp
            ed.price = row_data.prices[self.gp.base_token.name]
            ed.tick = current_tick

            ed.tick_lower, ed.tick_upper = self.utils.current_position_info[0], self.utils.current_position_info[1]

            pos = lp_market.positions[self.utils.current_position_info]
            ed.price_lower, ed.price_upper = pos.lower_price, pos.upper_price

            ed.new_tick_lower, ed.new_tick_upper = ed.tick_lower, ed.tick_upper

            ed.base_fee, ed.quote_fee = ZERO, ZERO
            # ed.base_removed, ed.quote_removed = ZERO, ZERO
            ed.base_removed, ed.quote_removed = (amount0, amount1) if lp_market.token1 == lp_market.quote_token else (amount1, amount0)
            ed.base_added, ed.quote_added = base_used, quote_used
            ed.was_in_range = self.was_in_range
            ed.total_base_fee, ed.total_quote_fee = ZERO, ZERO

            ed.lp_net_value = lp_market.get_market_balance().net_value
            ed.quote_balance = lp_market.broker.get_token_balance(self.gp.quote_token)
            ed.base_balance = lp_market.broker.get_token_balance(self.gp.base_token)
            ed.total_net_value = ed.lp_net_value + ed.quote_balance + (ed.base_balance * ed.price)
            # lp_row_data = self.utils.get_lp_row_data(row_data)
            # if self.params.range_strategy == RangeStrategy.std:
            #     ed.indicator_value = lp_row_data.std_1_hr
            # elif self.params.range_strategy == RangeStrategy.atr:
            #     ed.indicator_value = lp_row_data.atr_1_hr
            # else:
            #     ed.indicator_value = new_tick_upper - new_tick_lower
            ed.indicator_value = None
            ed.param_type = "dca"

            self.export_actions.append(ed)

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

    def calculate_tick_bounds(self, row_data: RowData, is_first_lp: bool = False) -> tuple[int, int]:
        lp_row_data = self.utils.get_lp_row_data(row_data)
        spread_lower = self.utils.params.tick_spread_lower
        spread_upper = self.utils.params.tick_spread_upper
        if is_first_lp:
            spread_lower = spread_upper = self.utils.params.init_tick_spread  # max(self.utils.params.tick_spread_lower, self.utils.params.tick_spread_upper)

        low = lp_row_data.openTick - spread_lower * self.utils.params.tick_spacing
        upper = lp_row_data.openTick + spread_upper * self.utils.params.tick_spacing
        return self.round_to_tick_space(low, upper)

    def calculate_tick_bounds_std(self, row_data: RowData, multiplier: float = 2) -> tuple[int, int]:
        lp_row_data = self.utils.get_lp_row_data(row_data)
        tick_dif = int(lp_row_data.std_1_hr * multiplier) * self.utils.params.tick_spacing
        tick_dif = max(tick_dif, self.utils.params.tick_spacing)
        low = lp_row_data.closeTick - tick_dif
        upper = lp_row_data.closeTick + tick_dif
        return self.round_to_tick_space(low, upper)

    def calculate_tick_bounds_atr(self, row_data: RowData, multiplier: float) -> tuple[int, int]:
        lp_row_data = self.utils.get_lp_row_data(row_data)
        tick_dif = int(lp_row_data.atr_1_hr * multiplier) * self.utils.params.tick_spacing
        tick_dif = max(tick_dif, self.utils.params.tick_spacing)
        low = lp_row_data.closeTick - tick_dif
        upper = lp_row_data.closeTick + tick_dif
        return self.round_to_tick_space(low, upper)

    def get_balance_base_quote_amounts(self) -> tuple[Decimal, Decimal]:
        base = self.broker.get_token_balance(self.gp.base_token)
        quote = self.broker.get_token_balance(self.gp.quote_token)
        # if self.broker.quote_token == usdc:
        #     quote = usdc_balance
        #     base = eth_balance
        # else:
        #     base = usdc_balance
        #     quote = eth_balance

        return base, quote
    
    @staticmethod
    def price_trend_check(current_price: Decimal, lp_price: Decimal, price_actions: List[PriceActionLog]) -> tuple[List[PriceActionLog], bool | None]:
        bull: bool | None = None
        pal = PriceActionLog(current_price, lp_price)
        if len(price_actions) <= 1:
            price_actions.append(pal)
        else: 
            if price_actions[-2].lp_price > price_actions[-1].lp_price > lp_price:
                price_actions.append(pal)
                bull = False
            elif price_actions[-2].lp_price < price_actions[-1].lp_price < lp_price:
                price_actions.append(pal)
                bull = True
            else:
                price_actions = [pal]
        return price_actions, bull

    def rescale_work(self, row_data: RowData):

        lp_market: UniLpMarket = self.broker.markets[self.utils.market_key]

        if len(lp_market.positions) == 0:
            return

        self.check_and_add_dca(row_data, lp_market)

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
            
            # param_changed = False
            # current_param_type = "bull" if self.utils.bull else "bear"
            
            # if not self.params.to_swap and self.params.range_strategy == RangeStrategy.remix_dao:
            #     bull: bool | None = None
            #     if current_tick > new_tick_upper: # range is under price
            #         new_upper_price = pos.upper_price
            #         self.ps_lower, bull = self.price_trend_check(current_price, new_upper_price, self.pa_lower)
            #         if bull is not None and len(self.pa_lower) >= 3:
            #             if self.utils.bull == bull: # same price trend as param
            #                 # trim self.ps_lower to the last price action
            #                 self.ps_lower = [self.pa_lower[-1]]
            #             else: # change param
            #                 param_changed = True
            #                 if bull:
            #                     self.utils.use_bull_params()
            #                 else:
            #                     self.utils.use_bear_params()
            #     else:
            #         new_lower_price = pos.lower_price
            #         self.ps_upper, bull = self.price_trend_check(current_price, new_lower_price, self.pa_upper)
            #         if bull is not None and len(self.ps_upper) >= 3:
            #             if self.utils.bull == bull: # same price trend as param
            #                 # trim self.ps_upper to the last price action
            #                 self.ps_upper = [self.ps_upper[-1]]
            #             else: # change param
            #                 param_changed = True
            #                 if bull:
            #                     self.utils.use_bull_params()
            #                 else:
            #                     self.utils.use_bear_params()
            #     if param_changed:
            #         self.pa_lower = []
            #         self.pa_upper = []
            #     pass
            
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
                ed.indicator_value = new_tick_upper - new_tick_lower
                
            ed.param_type = "bull" if self.utils.bull else "bear"

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
            
        ed.param_type = "bull" if self.utils.bull else "bear"
        self.export_actions.append(ed)

        self.total_fee = self.total_quote_fee + self.total_base_fee * current_price
        self.final_total_net_value = ed.total_net_value
        self.final_lp_net_value = ed.lp_net_value

        print(f"total dca added: {self.dca_total_added}")

        pass

    def on_bar(self, row_data: RowData):
        """
        Called after triggers on each iteration, at this time, market are not updated yet(Take uniswap market for example, fee of this minute are not added to positions).

        :param row_data: data in this iteration, include current timestamp, price, all columns data, and indicators(such as simple moving average)
        :type row_data: RowData
        """

        if self.was_in_range or self.utils.current_position_info is None:
            return

        lp_row_data = row_data.market_status[self.utils.market_key]
        # current_tick = lp_row_data.closeTick

        self.was_in_range = (
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
        
        export_file(f"{self.params.folder}/result_{self.params.report_name}.csv", self.export_actions)
        pass


def run_test(bull_params: RemixDAOParams, bear_params: RemixDAOParams, params: TestParams, gp: GlobalParams, processed_data: pd.DataFrame | None) -> Dict[str, Decimal]:
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
        utils = RemixDaoUtils(market, market_key, bull_params, bear_params, params.start_with_bull_param)
        strat = RemixDaoDcaStratStrategy(utils, params, gp)
        actuator.strategy = strat
        market.data_path = f"../real-data/{gp.contract_address}"
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
        metrics: dict[str, Decimal] = performance_metrics_for_dca(
            actuator.account_status_df["net_value"], float(strat.dca_total_added), benchmark=actuator.account_status_df["price"][price_name],
            total_fee=strat.total_fee,
        )
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
        metrics["benchmark_max_draw_down"] = max_draw_down_fast(bench_price)

        metrics["total_dca"] = strat.dca_total_added

        return metrics
    except Exception as e:
        print(f"error for {params.range_strategy.value}, {str(bull_params)}, {str(bear_params)}")
        raise e
    # plot_position_return_decomposition(actuator.account_status_df, actuator.token_prices[_base_token.name], market_key)


@dataclass
class RescaleParam():
    bull_lower_spread: int
    bull_upper_spread: int
    bear_lower_spread: int
    bear_upper_spread: int
    init_tick_spread: int
    
    def initial_swap(self) -> bool:
        return self.init_tick_spread != 0
    

def process_for_date(csd: datetime, dsd: date, ded: date, id: str, flip_param_dates: list[datetime]):
    demeter.Formats.global_num_format = ".4g"  # change out put formats here
    usdc = TokenInfo(name="usdc", decimal=6)
    eth = TokenInfo(name="eth", decimal=18)
    btc = TokenInfo(name="btc", decimal=8)

    # base_token, quote_token, init_quote = eth, usdc, Decimal(1000000)  #  USDC

    base_token, quote_token, init_quote = eth, usdc, Decimal(30000)  # DCA USDC

    # base_token, quote_token, init_quote = btc, eth, Decimal(100)  # ETH
    # base_token, quote_token, init_quote = eth, btc, Decimal(1)  # BTC

    # token0, token1 = btc, eth
    # contract_address, fee, chain_name = "0x4585FE77225b41b697C938B018E2Ac67Ac5a20c0", 0.05, ChainType.ethereum.name # wbtc/weth  2021-05-13
    # contract_address, fee, chain_name = "0x2f5e87C9312fa29aed5c179E456625D79015299c", 0.05, ChainType.arbitrum.name # wbtc/weth
    # contract_address, fee, chain_name = "0xCBCdF9626bC03E24f779434178A73a0B4bad62eD", 0.3, ChainType.ethereum.name # wbtc/weth

    token0, token1 = usdc, eth
    contract_address, fee, chain_name = "0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640", 0.05, ChainType.ethereum.name  # weth/usdc
    # token0, token1 = eth, usdc
    # contract_address, fee, chain_name = "0xC6962004f452bE9203591991D15f6b388e09E8D0", 0.05, ChainType.arbitrum.name  # weth/usdc

    dca_usdc_amount = 0

    # pool = UniV3Pool(btc, eth, _fee, _quote_token)
    _tick_spacing = int(fee * 200)  # 10  # should simply be fee * 200
    _aggressive = True
    _compound = False
    _folder_prefix = f"always-10000-{id}-{quote_token.name.lower()}"
    _dca_add_if_non_empty = False
    _dca_timing = DcaTiming.always

    gp = GlobalParams(token0=token0, token1=token1, fee=fee, init_quote=init_quote,
                      quote_token=quote_token, base_token=base_token,
                      chain_name=chain_name, contract_address=contract_address, swap_fee=False,
                      dca_usdc_amount=dca_usdc_amount,
                      dca_add_if_non_empty=_dca_add_if_non_empty,
                      dca_add_timing=_dca_timing)

    # [init, lower, upper], [lower/upper], [lower, upper]

    _remix_spreads = [
        # RescaleParam(init_tick_spread=10, bull_lower_spread=10, bull_upper_spread=10, bear_lower_spread=10,
        #              bear_upper_spread=10, ),
        # RescaleParam(init_tick_spread=30, bull_lower_spread=30, bull_upper_spread=30, bear_lower_spread=30,
        #              bear_upper_spread=30, ),
        # RescaleParam(init_tick_spread=40, bull_lower_spread=40, bull_upper_spread=40,
        #              bear_lower_spread=40, bear_upper_spread=40, ),
        # RescaleParam(init_tick_spread=50, bull_lower_spread=50, bull_upper_spread=50,
        #              bear_lower_spread=50, bear_upper_spread=50, ),
        # RescaleParam(init_tick_spread=60, bull_lower_spread=60, bull_upper_spread=60,
        #              bear_lower_spread=60, bear_upper_spread=60, ),
        RescaleParam(init_tick_spread=100, bull_lower_spread=100, bull_upper_spread=100,
                     bear_lower_spread=100, bear_upper_spread=100, ),
        RescaleParam(init_tick_spread=150, bull_lower_spread=150, bull_upper_spread=150,
                     bear_lower_spread=150, bear_upper_spread=150, ),
        RescaleParam(init_tick_spread=200, bull_lower_spread=200, bull_upper_spread=200,
                     bear_lower_spread=200, bear_upper_spread=200, ),
    ]

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

    _cmp = ""
    if _compound:
        _cmp = "_cmp"
    # csd = _cal_start_date
    # dsd = _data_start_date
    # ded = _data_end_date

    folder = f"result/{_folder_prefix}-{init_quote}-{csd.strftime("%Y%m%d")}-{ded.strftime("%Y%m%d")}"
    Path(folder).mkdir(parents=True, exist_ok=True)
    parameters: List[Tuple[RemixDAOParams, RemixDAOParams, TestParams]] = []

    for rescale_frequency in _rescale_frequencies:
        for spread in _remix_spreads:
            # lower = upper
            bull_no_offset = copy.copy(_param_no_offset)
            bull_with_offset = copy.copy(_param_with_offset)
            bear_no_offset = copy.copy(_param_no_offset)
            bear_with_offset = copy.copy(_param_with_offset)

            # init: int | None = None
            # if len(spread) == 1:
            #     lower = upper = spread[0]
            # elif len(spread) == 2:
            #     lower = spread[0]
            #     upper = spread[1]
            # else:
            #     init = spread[0]
            #     lower = spread[1]
            #     upper = spread[2]

            bull_with_offset.tick_spread_lower = spread.bull_lower_spread
            bull_with_offset.tick_spread_upper = spread.bull_upper_spread
            bull_no_offset.tick_spread_lower = spread.bull_lower_spread
            bull_no_offset.tick_spread_upper = spread.bull_upper_spread
            bear_with_offset.tick_spread_lower = spread.bear_lower_spread
            bear_with_offset.tick_spread_upper = spread.bear_upper_spread
            bear_no_offset.tick_spread_lower = spread.bear_lower_spread
            bear_no_offset.tick_spread_upper = spread.bear_upper_spread

            # if init is not None:
            #     with_offset.init_tick_spread = init
            #     no_offset.init_tick_spread = init
            bull_with_offset.init_tick_spread = spread.init_tick_spread
            bull_no_offset.init_tick_spread = spread.init_tick_spread
            bear_with_offset.init_tick_spread = spread.init_tick_spread
            bear_no_offset.init_tick_spread = spread.init_tick_spread

            
            start_with_bull_param: bool = True
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

            parameters.append((bull_with_offset, bear_with_offset,
                               TestParams(range_strategy=RangeStrategy.remix_dao, indicator_mult=1,
                                          report_name=f"{init_quote}{quote_token.name}_{RangeStrategy.remix_dao.name}_with_offset_{bull_with_offset.init_tick_spread}_{bull_with_offset.tick_spread_lower}-{bull_with_offset.tick_spread_upper}_{bear_with_offset.tick_spread_lower}-{bear_with_offset.tick_spread_upper}_{rescale_frequency.name}-{gp.dca_add_if_non_empty}",
                                          indicator_length_hr=1, to_swap=False,
                                          aggressive=_aggressive, compound=_compound,
                                          rescale_frequency=rescale_frequency,
                                          cal_start_datetime=csd, data_start_date=dsd, data_end_date=ded, folder=folder,
                                          initial_swap=spread.initial_swap(), flip_param_dates=flip_param_dates,
                                          start_with_bull_param=start_with_bull_param)
                               ))
            parameters.append((bull_no_offset, bear_no_offset,
                               TestParams(range_strategy=RangeStrategy.remix_dao, indicator_mult=1,
                                          report_name=f"{init_quote}{quote_token.name}_{RangeStrategy.remix_dao.name}_no_offset_{bull_no_offset.init_tick_spread}_{bull_no_offset.tick_spread_lower}-{bull_no_offset.tick_spread_upper}_{bear_no_offset.tick_spread_lower}-{bear_no_offset.tick_spread_upper}_{rescale_frequency.name}-{gp.dca_add_if_non_empty}",
                                          indicator_length_hr=1, to_swap=False,
                                          aggressive=_aggressive, compound=_compound,
                                          rescale_frequency=rescale_frequency,
                                          cal_start_datetime=csd, data_start_date=dsd, data_end_date=ded, folder=folder,
                                          initial_swap=spread.initial_swap(), flip_param_dates=flip_param_dates,
                                          start_with_bull_param=start_with_bull_param)
                               ))
            # parameters.append((no_offset,
            #                    TestParams(range_strategy=RangeStrategy.remix_dao, indicator_mult=1,
            #                               report_name=f"{init_quote}{quote_token.name}_{RangeStrategy.remix_dao.name}_rebalance_{no_offset.init_tick_spread}_{no_offset.tick_spread_lower}_{no_offset.tick_spread_upper}_{rescale_frequency.name}{_cmp}",
            #                               indicator_length_hr=1, to_swap=True,
            #                               aggressive=_aggressive, compound=_compound, rescale_frequency=rescale_frequency,
            #                               cal_start_datetime=csd, data_start_date=dsd, data_end_date=ded, folder=folder,
            #                               initial_swap=initial_swap, flip_param_dates=flip_param_dates,
            #                               start_with_bull_param=start_with_bull_param)
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
    market.data_path = f"../real-data/{contract_address}"
    market.load_data(chain_name, contract_address, dsd, ded)

    result = list(map(lambda p: (p[2].report_name, run_test(p[0], p[1], p[2], gp, market.data)), parameters))
    export_apr_results(f"{folder}/apr_remix_{init_quote}_results.csv", result)
    pass


if __name__ == "__main__":

    date_ranges: List[tuple[datetime, date, date, list[datetime]]] = [
        # (_cal_start_date, _data_start_date, _data_end_date)
        # total-market
        #(datetime(2023, 10, 16, 0, 0, 0), date(2023, 10, 6), date(2024, 9, 3), []),
        # bull-market
        #(datetime(2023, 10, 16, 0, 0, 0), date(2023, 10, 6), date(2024, 5, 26), []),
        # bear-market
        #(datetime(2024, 5, 27, 0, 0, 0), date(2024, 5, 1), date(2024, 9, 3), []),
        # 20240311 - 20240903
        #(datetime(2024, 3, 11, 0, 0, 0), date(2024, 3, 11), date(2024, 9, 3), []),
        # BTC/ETH BEAR 20220613 - 20220912
        # (datetime(2022, 6, 13, 0, 0, 0), date(2022, 6, 13), date(2022, 9, 12), []),
        # 20240311 ~ 20240903
        # (datetime(2024, 3, 11, 0, 0, 0), date(2024, 3, 11), date(2024, 9, 3), []),

        # ISAO cases
        #  2021/05/04~2024/09/30
        (datetime(2021, 5, 13, 0, 0, 0), date(2021, 5, 13), date(2024, 11, 11), "dca", []),
        #  2021/05/04~2021/12/31
        (datetime(2021, 5, 13, 0, 0, 0), date(2021, 5, 13), date(2021, 12, 31), "dca", []),
        #  2022/01/01~2022/12/31
        (datetime(2022, 1, 1, 0, 0, 0), date(2022, 1, 1), date(2022, 12, 31), "dca", []),
        #  2023/01/01~2023/12/31
        (datetime(2023, 1, 1, 0, 0, 0), date(2023, 1, 1), date(2023, 12, 31), "dca", []),
        #  2024/01/01~2024/09/30
        # (datetime(2024, 1, 1, 0, 0, 0), date(2024, 1, 1), date(2024, 11, 11), "dca", []),
        
        # switch run
        # SCENARIO A (SMA20/EMA20)
        # (datetime(2023, 10, 16, 0, 0, 0), date(2023, 10, 6), date(2024, 9, 3), "A", [datetime(2023, 11, 20, 0, 0, 0), datetime(2024, 5, 20, 0, 0, 0)]),
        # SCENARIO D (EMA50/CLOSE)
        # (datetime(2023, 10, 16, 0, 0, 0), date(2023, 10, 6), date(2024, 9, 3), "D", [datetime(2023, 6, 26, 0, 0, 0), datetime(2023, 8, 21, 0, 0, 0), datetime(2023, 10, 30, 0, 0, 0), datetime(2024, 8, 5, 0, 0, 0)]),

    ]

    threads = map(lambda dr: multiprocessing.Process(target=process_for_date, args=dr), date_ranges)
    for t in threads:
        t.start()

    for t in threads:
        t.join()
