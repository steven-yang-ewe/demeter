import decimal
import json
import math
from decimal import Decimal
from enum import Enum
from typing import Tuple, List

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
    realized_volatility,
    simple_moving_average, AtTimeTrigger, PeriodTrigger, BaseAction, AccountStatus
)
from demeter.metrics import performance_metrics
from demeter.uniswap import UniLpMarket, UniV3Pool, PositionInfo, Position, tick_to_base_unit_price
from datetime import date, timedelta, datetime

from strategy_ploter import plot_position_return_decomposition
from remix_dao_utils import RemixDaoUtils, RemixDAOParams
from export_file import export_file, ExportData

conservative_fluctuation = Decimal(0.03)


class RemixDaoStrategy(Strategy):
    # _default_lock_time = datetime(2000, 1, 1, 0, 0, 0)
    compound: bool
    aggressive: bool

    was_in_range: bool
    last_rescale_tick: int
    utils: RemixDaoUtils
    total_fee0: Decimal
    total_fee1: Decimal
    export_actions: List[ExportData]
    last_price: Decimal
    lock_until_time: datetime | None

    def __init__(self, _utils: RemixDaoUtils, aggressive: bool = True, compound: bool = False):
        super().__init__()
        self.utils = _utils
        self.was_in_range = False
        self.last_rescale_tick = 0
        self.total_fee0 = decimal.Decimal('0.00000000000000')
        self.total_fee1 = decimal.Decimal('0.00000000000000')
        self.compound = compound
        self.export_actions = []
        # self.lock_until_time = self._default_lock_time
        self.aggressive = aggressive
        self.lock_until_time = None
        self.last_price = decimal.Decimal('0.00000000000000')
        self.printed = False

    def initialize(self):

        new_trigger = AtTimeTrigger(time=datetime(2024, 1, 1, 0, 0, 0), do=self.first_lp)
        self.triggers.append(new_trigger)

        self.triggers.append(PeriodTrigger(time_delta=timedelta(hours=1), do=self.hourly_work))
        pass

    def calculate_tick_bounds(self, row_data: RowData) -> (int, int):
        lp_row_data = self.utils.get_lp_row_data(row_data)
        low = lp_row_data.openTick - self.utils.params.tick_spread_lower
        upper = lp_row_data.openTick + self.utils.params.tick_spread_upper
        return low, upper


    # def printPriceTicks(self, row_data: RowData):
    #
    #     if not self.printed:
    #
    #         lp_row_data = self.utils.get_lp_row_data(row_data)
    #         if lp_row_data.closeTick != lp_row_data.openTick:
    #
    #             lp_market: UniLpMarket = self.broker.markets[market_key]
    #             price = lp_row_data.price
    #             open_price = tick_to_base_unit_price(lp_row_data.openTick, lp_market.token0.decimal, lp_market.token1.decimal, False)
    #             close_price = tick_to_base_unit_price(lp_row_data.closeTick, lp_market.token0.decimal, lp_market.token1.decimal, False)
    #             print(f"price: {str(price)}, open_price: {str(open_price)}, close_price: {str(close_price)}, open_tick: {str(lp_row_data.openTick)}, close_tick: {str(lp_row_data.closeTick)}")
    #             self.printed = True
    #     pass

    def hourly_work(self, row_data: RowData):

        #self.printPriceTicks(row_data)

        lp_market: UniLpMarket = self.broker.markets[market_key]
        if len(lp_market.positions) == 0:
            return
        current_price = row_data.prices[eth.name]
        try:

            if not self.aggressive:
                in_lock = self.lock_until_time is not None and row_data.timestamp <= self.lock_until_time
                if in_lock:
                    return
                elif self.lock_until_time is None:
                    # check price fluctuate
                    fluc: Decimal = (current_price - self.last_price) / self.last_price
                    if fluc.copy_abs() > conservative_fluctuation:
                        self.lock_until_time = row_data.timestamp + timedelta(hours=24)
                        print(
                            f"time: {row_data.timestamp.strftime("%Y-%m-%d %H:%M:%S")}, rescale locked until {self.lock_until_time.strftime("%Y-%m-%d %H:%M:%S")} ")
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

            # Get the current tick info
            # tick_spacing, current_tick, current_tick_lower, current_tick_upper = utils.get_tick_info(row_data)

            # this issue only occurs in on chain action, backtest does not have this issue and cannot determine if issue occurs
            # require(
            #     currentTick <= (_tickBeforeRescale + rescaleTickTolerance[_strategyContract])
            #     & & currentTick >= (_tickBeforeRescale - rescaleTickTolerance[_strategyContract]),
            #     "out of tick tolerance range"
            # );

            # fee0, fee1 = lp_market.collect_fee(self.utils.current_position_info, collect_to_user=False)
            base_fee, quote_fee = lp_market.collect_fee(self.utils.current_position_info, collect_to_user=True)
            base, quote = lp_market.remove_liquidity(self.utils.current_position_info, collect=True)
            old_position_info = self.utils.current_position_info

            self.total_fee0 += base_fee
            self.total_fee1 += quote_fee

            if self.compound:
                self.utils.current_position_info, base_used, quote_used, _ = lp_market.add_liquidity_by_tick(
                    new_tick_lower,
                    new_tick_upper)
            else:
                self.utils.current_position_info, base_used, quote_used, _ = lp_market.add_liquidity_by_tick(
                    new_tick_lower,
                    new_tick_upper, base, quote)
            _, current_tick, _, _ = self.utils.get_tick_info(row_data)

            ed = ExportData()
            ed.time = row_data.timestamp
            ed.price = current_price
            ed.tick = current_tick

            ed.tick_lower, ed.tick_upper = old_position_info[0], old_position_info[1]

            pos = lp_market.positions[utils.current_position_info]
            ed.price_lower, ed.price_upper = pos.lower_price, pos.upper_price

            ed.new_tick_lower, ed.new_tick_upper = utils.current_position_info[0], utils.current_position_info[1]

            ed.base_fee, ed.quote_fee = base_fee, quote_fee
            ed.base_removed, ed.quote_removed = base, quote
            ed.base_added, ed.quote_added = base_used, quote_used
            ed.was_in_range = self.was_in_range

            self.export_actions.append(ed)

            print(
                f"rescaled at {row_data.timestamp.strftime("%Y-%m-%d %H:%M:%S")}, removed: {base} / {quote}, fee: {base_fee} / {quote_fee}, used: {base_used} / {quote_used}, "
                f"tick: {current_tick}, old_position_info: {old_position_info}, position_info: {str(self.utils.current_position_info)}, was_in_range: {self.was_in_range}, price: {current_price}")

            self.last_rescale_tick = current_tick
            self.was_in_range = False

        finally:
            self.last_price = current_price
        pass

    def first_lp(self, row_data: RowData):

        lp_market: UniLpMarket = self.broker.markets[market_key]
        lp_row_data = row_data.market_status[market_key]

        if len(lp_market.positions) > 0:
            raise RuntimeError("shouldn't have any position")

        lp_market.even_rebalance(row_data.prices[eth.name])
        # need to know how to place initial position
        (lower, upper) = self.calculate_tick_bounds(row_data)
        self.utils.current_position_info, _, _, _ = lp_market.add_liquidity_by_tick(lower, upper)
        print(
            f"\nadding first liquidity, price: {str(row_data.prices[eth.name])}, range: {str(lower)} ~ {str(upper)}, "
            f"position_info: {str(self.utils.current_position_info)}")

        self.was_in_range = True
        self.last_price = row_data.prices[eth.name]



        # lp_market.add_liquidity(lp_row_data.sma_1_day - limit, lp_row_data.sma_1_day + limit)

        # print(f"market_status ({type(lp_row_data).__name__}): {str(lp_row_data)}")
        pass

    def on_bar(self, row_data: RowData):
        """
        Called after triggers on each iteration, at this time, market are not updated yet(Take uniswap market for example, fee of this minute are not added to positions).

        :param row_data: data in this iteration, include current timestamp, price, all columns data, and indicators(such as simple moving average)
        :type row_data: RowData
        """

        if self.utils.current_position_info is None:
            return

        lp_row_data = row_data.market_status[market_key]
        # current_tick = lp_row_data.closeTick
        try:
            self.was_in_range = self.was_in_range or (
                # check if the tick range ever overlaps the LP range
                    self.utils.current_position_info[0] <= lp_row_data.highestTick and
                    self.utils.current_position_info[1] >= lp_row_data.lowestTick)
        except ValueError as e:
            print(f"position info: {str(self.utils.current_position_info)}, {e}")
            raise e

        pass

    def after_bar(self, row_data: RowData):
        """
        called after market are updated on each iteration

        :param row_data: data in this iteration, include current timestamp, price, all columns data, and indicators(such as simple moving average)
        :type row_data: RowData
        """

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

        export_file("result/result_remix.csv", self.export_actions)
        pass


token0: TokenInfo
token1: TokenInfo

if __name__ == "__main__":
    demeter.Formats.global_num_format = ".4g"  # change out put formats here

    usdc = TokenInfo(name="usdc", decimal=6)
    eth = TokenInfo(name="eth", decimal=18)
    token0, token1 = eth, usdc
    pool = UniV3Pool(eth, usdc, 0.03, usdc)  # declare pool, Arbitrum One
    market_key = MarketInfo("lp")

    actuator = Actuator()  # declare actuator
    broker = actuator.broker
    market = UniLpMarket(market_key, pool)

    broker.add_market(market)
    broker.set_balance(usdc, 5000)
    broker.set_balance(eth, 0)

    rm_params = RemixDAOParams(
        tick_spread_upper=60,
        tick_spread_lower=60,
        tick_upper_boundary_offset=0,
        tick_lower_boundary_offset=0,
        rescale_tick_upper_boundary_offset=10,
        rescale_tick_lower_boundary_offset=10,
        tick_spacing=10,
        rescale_tick_tolerance=10)

    utils = RemixDaoUtils(market, market_key, rm_params)
    actuator.strategy = RemixDaoStrategy(utils, False)
    market.data_path = "../real_data"
    market.load_data(
        ChainType.arbitrum.name, "0xc473e2aEE3441BF9240Be85eb122aBB059A3B57c", date(2024, 1, 1), date(2024, 6, 30)
    )
    actuator.set_price(market.get_price_from_data())
    actuator.run()  # run test

    metrics = performance_metrics(
        actuator.account_status_df["net_value"], benchmark=actuator.account_status_df["price"]["ETH"]
    )
    print(metrics)
    plot_position_return_decomposition(actuator.account_status_df, actuator.token_prices[eth.name], market_key)
