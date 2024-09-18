from decimal import Decimal
from typing import Tuple

from pandas import Series

from demeter import RowData, MarketInfo
from demeter.uniswap import UniLpMarket, PositionInfo, Position


class StrategyInfo:
    was_in_range: bool = False
    current_position_info: PositionInfo = None
    lp_market: UniLpMarket = None
    market_key: MarketInfo = None

    def __init__(self, lp_market: UniLpMarket, market_key: MarketInfo):
        self.lp_market = lp_market
        self.market_key = market_key

    def get_current_position(self) -> Position:
        return self.lp_market.positions[self.current_position_info]

    def get_current_position_info(self) -> PositionInfo:
        return self.current_position_info

    def set_current_position_info(self, position_info: PositionInfo):
        self.current_position_info = position_info

    def get_lp_row_data(self, row_data: RowData) -> Series:
        return row_data.market_status[self.market_key]


class RemixDAOParams:
    def __init__(self, rescale_tick_lower_boundary_offset=10, rescale_tick_tolerance=10,
                 rescale_tick_upper_boundary_offset=10,
                 tick_lower_boundary_offset=0,
                 tick_spread_lower=60, tick_spread_upper=60, tick_upper_boundary_offset=0, init_tick_spread=75,
                 tick_spacing: int = 10):
        self.rescale_tick_lower_boundary_offset = rescale_tick_lower_boundary_offset
        self.rescale_tick_tolerance = rescale_tick_tolerance
        self.rescale_tick_upper_boundary_offset = rescale_tick_upper_boundary_offset
        self.tick_lower_boundary_offset = tick_lower_boundary_offset
        self.tick_spread_lower = tick_spread_lower
        self.tick_spread_upper = tick_spread_upper
        self.tick_upper_boundary_offset = tick_upper_boundary_offset
        self.tick_spacing = tick_spacing
        self.init_tick_spread = init_tick_spread


default_rm_params: RemixDAOParams = RemixDAOParams(
    tick_spread_upper=60,
    tick_spread_lower=60,
    tick_upper_boundary_offset=0,
    tick_lower_boundary_offset=0,
    rescale_tick_upper_boundary_offset=10,
    rescale_tick_lower_boundary_offset=10,
    tick_spacing=10,
    rescale_tick_tolerance=10,
    init_tick_spread=75)


class RemixDaoUtils:
    # was_in_range: bool = False
    current_position_info: PositionInfo = None
    lp_market: UniLpMarket = None
    market_key: MarketInfo = None
    params: RemixDAOParams = None

    def __init__(self, lp_market: UniLpMarket, market_key: MarketInfo, params: RemixDAOParams):
        self.lp_market = lp_market
        self.market_key = market_key
        self.params = params
        self.printed = False

    def get_current_position(self) -> Position:
        return self.lp_market.positions[self.current_position_info]

    def get_current_position_info(self) -> PositionInfo:
        return self.current_position_info

    def set_current_position_info(self, position_info: PositionInfo):
        self.current_position_info = position_info

    def get_lp_row_data(self, row_data: RowData) -> Series:
        """
        data in the csv with added column with extra calculation

        sample data:
        class "Series":
         netAmount0                                             0
         netAmount1                                             0
         closeTick                                      -194880.0
         openTick                                       -194880.0
         lowestTick                                     -194880.0
         highestTick                                    -194880.0
         inAmount0                                              0
         inAmount1                                              0
         currentLiquidity                      326198489959949285
         open                3442.6453466940986616023432482558875
         price               3442.6453466940986616023432482558875
         low                 3442.6453466940986616023432482558875
         high                3442.6453466940986616023432482558875
         volume0                                                0
         volume1                                                0
         sma_1_day                                    3389.257997
         volatility                                       0.00742
        """
        return row_data.market_status[self.market_key]

    # def is_rescale_allowed_with_one_tick_spacing(self, current_tick: int, current_tick_lower: int,
    #                                              current_tick_upper: int) -> bool:
    #     # Verify Rescale Condition
    #     return current_tick < (current_tick_lower - self.params.tick_gap_lower) or current_tick > (
    #             current_tick_upper + self.params.tick_gap_upper)

    def is_rescale_allowed_with_non_one_tick_spacing(
            self,
            was_in_range, last_rescale_tick,
            tick_spacing, current_tick, current_tick_lower, current_tick_upper
    ):
        # Verify Rescale Parameter
        if (
                (last_rescale_tick > current_tick_upper and current_tick < current_tick_lower)
                or (last_rescale_tick < current_tick_lower and current_tick > current_tick_upper)
        ):
            return was_in_range
            # if not was_in_range:
            #     print(f"wasInRange parameter error, last_rescale_tick: {last_rescale_tick}, current_tick: {current_tick}, current_tick_lower: {current_tick_lower}, curren_tick_upper: {current_tick_upper}")
            # assert was_in_range, "wasInRange parameter error"

        # Verify Rescale Condition
        if was_in_range:
            # It means the tick passed our range and keeps far away from our range
            if (
                    current_tick < current_tick_lower - (self.params.tick_lower_boundary_offset * tick_spacing)
                    or current_tick > current_tick_upper + (self.params.tick_upper_boundary_offset * tick_spacing)
            ):
                return True
        else:
            # It means the tick is far away from our range since last time
            if (
                    (current_tick < current_tick_lower and current_tick < last_rescale_tick)
                    or (current_tick > current_tick_upper and current_tick > last_rescale_tick)
            ):
                return True

        return False

    def calculate_one_tick_spacing_rescale_tick_boundary(self, current_tick, current_tick_lower):
        # tick_spread_upper, tick_spread_lower, _, _, _, _, _, _ = get_rescale_info(strategy_address, controller_address)

        if current_tick < current_tick_lower:
            new_tick_lower = current_tick + 1
            new_tick_upper = new_tick_lower + self.params.tick_spread_lower
        else:
            new_tick_upper = current_tick - 1
            new_tick_lower = new_tick_upper - self.params.tick_spread_upper

        return new_tick_lower, new_tick_upper

    @staticmethod
    def floor_tick(tick: int, tick_spacing: int) -> int:
        base_floor = int(tick / tick_spacing)

        if tick < 0 and tick % tick_spacing != 0:
            return (base_floor - 1) * tick_spacing
        return base_floor * tick_spacing

    @staticmethod
    def ceiling_tick(tick: int, tick_spacing: int) -> int:
        base_floor = int(tick / tick_spacing)

        if tick > 0 and tick % tick_spacing != 0:
            return (base_floor + 1) * tick_spacing
        return base_floor * tick_spacing

    def calculate_non_one_tick_spacing_rescale_tick_boundary(self, tick_spacing, current_tick, current_tick_lower):
        # tick_spread_upper, tick_spread_lower, _, _, rescale_tick_upper_boundary_offset, rescale_tick_lower_boundary_offset, _, _ = get_rescale_info(strategy_address, controller_address)

        if current_tick < current_tick_lower:
            tick_spread = self.params.tick_spread_lower

        else:
            tick_spread = self.params.tick_spread_upper

        tick_distance = tick_spacing if tick_spread == 0 else 2 * tick_spread * tick_spacing

        if current_tick < current_tick_lower:
            new_tick_lower = RemixDaoUtils.ceiling_tick(current_tick, tick_spacing) + (
                    self.params.rescale_tick_lower_boundary_offset * tick_spacing)
            new_tick_upper = new_tick_lower + tick_distance
        else:
            new_tick_upper = RemixDaoUtils.floor_tick(current_tick, tick_spacing) - (
                    self.params.rescale_tick_upper_boundary_offset * tick_spacing)
            new_tick_lower = new_tick_upper - tick_distance

        return new_tick_lower, new_tick_upper

    def get_tick_info(self, row_data: RowData) -> Tuple[
        int, int, int, int]:  # (int24 tickSpacing, int24 currentTick, int24 currentTickLower, int24 currentTickUpper)

        tick_spacing = self.params.tick_spacing  # IStrategyInfo(strategyAddress).tickSpacing();
        # (currentTick, currentTickLower, currentTickUpper) = LiquidityNftHelper.getTickInfo(
        #     IStrategyInfo(strategyAddress).liquidityNftId(),
        #     Constants.UNISWAP_V3_FACTORY_ADDRESS(),
        #     Constants.NONFUNGIBLE_POSITION_MANAGER_ADDRESS()
        # );

        pos_info = self.get_current_position_info()
        if pos_info is None:
            current_tick_lower, current_tick_upper = 0, 0
        else:
            current_tick_lower, current_tick_upper = pos_info[0], pos_info[1]
        lp_row_data = self.get_lp_row_data(row_data)
        current_tick = lp_row_data.closeTick
        return tick_spacing, current_tick, current_tick_lower, current_tick_upper

    def verify_and_get_new_rescale_tick_boundary(self, row_data: RowData, was_in_range: bool,
                                                 last_rescale_tick: int) -> (bool, int, int):
        # Get Tick Info
        tick_spacing, current_tick, current_tick_lower, current_tick_upper = self.get_tick_info(row_data)

        # print(f"verify_and_get_new_rescale_tick_boundary, current_tick: {current_tick}, current_tick_lower: {current_tick_lower}, current_tick_upper: {current_tick_upper}")

        # Verify Not In Range (Exclude Exact Boundary)
        if not (current_tick < current_tick_lower or current_tick > current_tick_upper):
            return False, 0, 0

        # Get Rescale Info and Verify
        # if tick_spacing == 1:
        #     allow_rescale = self.is_rescale_allowed_with_one_tick_spacing(
        #         current_tick, current_tick_lower, current_tick_upper
        #     )
        # else:
        allow_rescale = self.is_rescale_allowed_with_non_one_tick_spacing(
            was_in_range, last_rescale_tick,
            tick_spacing, current_tick, current_tick_lower, current_tick_upper
        )

        # Calculate newTickUpper & newTickLower
        if not allow_rescale:
            return False, 0, 0
        else:
            # if tick_spacing == 1:
            #     new_tick_lower, new_tick_upper = self.calculate_one_tick_spacing_rescale_tick_boundary(
            #         current_tick, current_tick_lower
            #     )
            # else:
            new_tick_lower, new_tick_upper = self.calculate_non_one_tick_spacing_rescale_tick_boundary(
                tick_spacing, current_tick, current_tick_lower
            )

        # Verify Rescale Result
        if current_tick_upper == new_tick_upper and current_tick_lower == new_tick_lower:
            return False, new_tick_upper, new_tick_lower
        else:
            return True, new_tick_upper, new_tick_lower

    @staticmethod
    def calculate_trade(base: Decimal, quote: Decimal, price: Decimal, base_percent: Decimal,
                        quote_percent: Decimal) -> (Decimal, Decimal):

        # Calculate the desired amounts
        base_desired = base_percent * (base + quote / price)
        quote_desired = quote_percent * (quote + price * base)

        # Determine the amounts to trade
        base_to_trade = base - base_desired
        quote_to_trade = quote - quote_desired

        if base_to_trade < 0:
            # Need to acquire more x
            quote_to_trade = -base_to_trade * price
            base_to_trade = 0
        elif quote_to_trade < 0:
            # Need to acquire more y
            base_to_trade = -quote_to_trade / price
            quote_to_trade = 0

        # print(
        #     f"calculate_trade, base: {base}, quote: {quote}, price: {price}, base_percent: {base_percent}, "
        #     f"quote_percent: {quote_percent}, base_to_trade: {base_to_trade}, quote_to_trade: {quote_to_trade}")
        return base_to_trade, quote_to_trade
