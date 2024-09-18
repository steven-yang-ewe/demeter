import math
from decimal import Decimal
from enum import Enum
from typing import Tuple

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
    simple_moving_average, AtTimeTrigger, PeriodTrigger
)
from demeter.metrics import performance_metrics
from demeter.uniswap import UniLpMarket, UniV3Pool
from datetime import date, timedelta, datetime

from strategy_ploter import plot_position_return_decomposition
from chaos_lab_utils import standard_deviation_over_last


class Management(str, Enum):
    active = "active"
    passive = "passive"


class PortfolioRisk(Enum):
    veryAggressive = 0.5
    aggressive = 1
    conservative = 2


class Sentiment(str, Enum):
    positive = "positive"
    negative = "negative"
    neutral = "neutral"


class TimeHorizon(Enum):
    weekly = 7
    monthly = 30


management = Management.active
risk = PortfolioRisk.conservative
sentiment = Sentiment.neutral
horizon = TimeHorizon.weekly
deviate = Decimal(0.1)


class ChaosLabStrategy(Strategy):

    # interval_prices: pd.Series

    @staticmethod
    def calculate_tick_bounds(price: float, std: float, p_risk: PortfolioRisk, senti: Sentiment) -> (Decimal, Decimal):
        limit = std * p_risk.value

        if not isinstance(limit, Decimal):
            limit = Decimal(limit)
        if not isinstance(price, Decimal):
            price = Decimal(price)

        # limit = price * limit

        if senti == Sentiment.positive:
            return price, price + limit
        elif senti == Sentiment.negative:
            return price - limit, price
        else:
            return price - limit, price + limit
        pass

    def initialize(self):
        # self.add_column(market_key, "sma_1_day", simple_moving_average(self.data[market_key].price, timedelta(hours=1)))
        # self.add_column(
        #     market_key,
        #     "volatility",
        #     realized_volatility(self.data[market_key].price, timedelta(hours=1), timedelta(days=1)),
        # )
        # Calculate the rolling standard deviation with a window of 1440 minutes (24 hours)
        self.add_column(market_key, "std_24_hr", standard_deviation_over_last(self.data[market_key].price, 1440))

        self.add_column(market_key, "std_1_hr", standard_deviation_over_last(self.data[market_key].price, 60))
        print(self.data[market_key].keys())

        new_trigger = AtTimeTrigger(time=datetime(2024, 1, 1, 0, 0, 0), do=self.first_lp)
        self.triggers.append(new_trigger)
        if management == Management.active:
            self.triggers.append(PeriodTrigger(time_delta=timedelta(hours=1), do=self.on_hour_active))

        # self.interval_prices = pd.Series([])
        pass

    def first_lp(self, row_data: RowData):

        lp_market: UniLpMarket = self.broker.markets[market_key]
        lp_row_data: Series = row_data.market_status[market_key]

        # current_std = self.interval_prices.std()
        current_std = lp_row_data.std_1_hr
        if math.isnan(current_std):
            return
        # limit = risk.value * float(row_data.prices[eth.name]) * lp_row_data.volatility
        # TODO rebalance funds before deposit into pool to maximize fund usage
        if sentiment == Sentiment.neutral:
            lp_market.even_rebalance(row_data.prices[eth.name])
        (lower, upper) = self.calculate_tick_bounds(row_data.prices[eth.name], current_std, risk, sentiment)
        print(f"price: {str(row_data.prices[eth.name])}, usdc price: {str(
            row_data.prices[usdc.name])}, std: {str(current_std)}, range: {str(lower)} ~ {str(upper)}")

        lp_market.add_liquidity(lower, upper)
        # lp_market.add_liquidity(lp_row_data.sma_1_day - limit, lp_row_data.sma_1_day + limit)

        print(f"market_status ({type(lp_row_data).__name__}): {str(lp_row_data)}")
        print(f"row_data: {str(row_data)}")

        pass
    def on_bar(self, row_data: RowData):
        """
        Called after triggers on each iteration, at this time, market are not updated yet(Take uniswap market for example, fee of this minute are not added to positions).

        :param row_data: data in this iteration, include current timestamp, price, all columns data, and indicators(such as simple moving average)
        :type row_data: RowData
        """
        pass

    def on_hour_active(self, row_data: RowData):
        """
        Called after triggers on each iteration, at this time, market are not updated yet(Take uniswap market for example, fee of this minute are not added to positions).

        :param row_data: data in this iteration, include current timestamp, price, all columns data, and indicators(such as simple moving average)
        :type row_data: RowData
        """
        # if management == Management.active:
        """
        Dynamic Ranges
            This strategy begins with a position range set around the current price, following user preferences, similar to the passive strategy. However, in response to significant price fluctuations
            based on asset volatility, the position range is recalibrated around the new price. We define
            a significant price movement as a scenario where the standard deviation of the price over
            the last hour surpasses thrice the standard deviation of the price over the previous 24 hours.
            Following such a recalibration, the standard deviation that determines the concentration of
            liquidity is recalculated.
        """

        fprice = row_data.prices[eth.name]
        if isinstance(fprice, Decimal):
            price = fprice
            fprice = float(fprice)
        else:
            price = Decimal(fprice)

        lp_market: UniLpMarket = self.broker.markets[market_key]
        if len(lp_market.positions) > 0:

            position_info = None
            for posInfo in lp_market.positions:

                pos = lp_market.positions[posInfo]
                if pos.liquidity <= 0:
                    continue
                if pos.lower_price > price + price * deviate or pos.upper_price < price - price * deviate:
                    position_info = posInfo
                break

            if position_info is not None:

                # price_std = self.interval_prices.std()
                lp_row_data: Series = row_data.market_status[market_key]
                price_std = lp_row_data.std_1_hr
                std24hr = lp_row_data.std_24_hr

                if price_std > std24hr * 3:

                    # rebalance
                    lp_market.remove_all_liquidity()
                    # lp_market.remove_liquidity(position_info, collect=False, remove_dry_pool=True)
                    if sentiment == Sentiment.neutral:
                        lp_market.even_rebalance(price)
                    # lp_row_data = row_data.market_status[market_key]
                    (lower, upper) = self.calculate_tick_bounds(fprice, price_std, risk, sentiment)
                    # print("price: " + str(row_data.prices[eth.name]) + ", usdc price: " + str(
                    #     row_data.prices[usdc.name]) + ", volatility: " + str(lp_row_data.volatility) + "range: " + str(
                    #     lower) + "-" + str(upper))

                    lp_market.add_liquidity(lower, upper)
                    print("rebalance at price: " + str(price) + ", new range: " + str(lower) + " ~ " + str(
                        upper) + ", positions: " + str(len(lp_market.positions)))
                    # self.interval_prices = pd.Series([])  # start a new price series, STD need to be recalculated
                else:
                    print(f"out of range but do not rebalance. std: {price_std}, 24 *3 std: {std24hr * 3}")
            # self.interval_prices.loc[len(self.interval_prices)] = fprice
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
        pass


if __name__ == "__main__":
    demeter.Formats.global_num_format = ".4g"  # change out put formats here

    usdc = TokenInfo(name="usdc", decimal=6)  # declare  token0
    eth = TokenInfo(name="eth", decimal=18)  # declare token1
    pool = UniV3Pool(eth, usdc, 0.03, usdc)  # declare pool, Arbitrum One
    market_key = MarketInfo("lp")

    actuator = Actuator()  # declare actuator
    broker = actuator.broker
    market = UniLpMarket(market_key, pool)

    broker.add_market(market)
    broker.set_balance(usdc, 5000)
    broker.set_balance(eth, 0)

    actuator.strategy = ChaosLabStrategy()
    market.data_path = "../real_data"
    market.load_data(
        ChainType.arbitrum.name, "0xc473e2aEE3441BF9240Be85eb122aBB059A3B57c", date(2023, 12, 10), date(2024, 6, 30)
    )
    actuator.set_price(market.get_price_from_data())
    actuator.run()  # run test

    metrics = performance_metrics(
        actuator.account_status_df["net_value"], benchmark=actuator.account_status_df["price"]["ETH"]
    )
    print(metrics)
    # plot_position_return_decomposition(actuator.account_status_df, actuator.token_prices[eth.name], market_key)
