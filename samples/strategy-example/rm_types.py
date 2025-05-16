from dataclasses import dataclass
from datetime import datetime, date
from decimal import Decimal
from enum import Enum

from demeter import TokenInfo

ZERO = Decimal(0)

class RangeStrategy(str, Enum):
    remix_dao = "remix-dao"
    std = "std"
    atr = "atr"


class RescaleFrequency(str, Enum):
    hourly = "hourly"
    daily = "daily"

class DcaTiming(str, Enum):
    base_only = "base_only"
    quote_only = "quote_only"
    out_of_range = "out_of_range"
    always = "always"
    none = "none"

class DcaAddition(str, Enum):
    on_base_only = "on_base_only"
    on_quote_only = "on_quote_only"
    none = "none"

@dataclass
class GlobalParams:
    token0: TokenInfo
    token1: TokenInfo
    fee: float
    base_token: TokenInfo
    quote_token: TokenInfo
    init_quote: Decimal
    chain_name: str
    contract_address: str
    swap_fee: bool = False
    init_quote_usdc: Decimal = ZERO
    dca_usdc_amount: Decimal = ZERO
    dca_add_if_non_empty: bool = False
    dca_add_timing: DcaTiming = DcaTiming.base_only
    dca_addon_price_percent: Decimal = Decimal(10000)  # in decimal form 0.1 is 10%
    dca_addon_amount_percent: Decimal = ZERO  # in the amount to add in percent, 1 is to add 100%, 0.4 is to add 40%
    dca_addition: DcaAddition = DcaAddition.none
    init_short_amount: Decimal = ZERO
    short_stop_loss_ratio: Decimal = Decimal(1)

@dataclass
class ShortInfo:
    short_amount: Decimal
    short_stop_loss_hit: bool = False
    short_stop_loss_price: Decimal | None = None
    short_price: Decimal | None = None
    short_stop_loss_cnt: int = 0
    short_win_cnt: int = 0
    prev_stop_loss: bool = False
    consecutive_short_stop_loss_cnt: int = 0
    short_total_gain: Decimal = ZERO
    short_total_loss: Decimal = ZERO
    short_to_lp: Decimal = ZERO
    lp_to_short: Decimal = ZERO


class TestParams:

    def __init__(self, range_strategy: RangeStrategy, indicator_mult: float, report_name: str,
                 cal_start_datetime: datetime,
                 data_start_date: date,
                 data_end_date: date,
                 folder: str,
                 indicator_length_hr: int = 1,
                 to_swap: bool = False,
                 aggressive: bool = True,
                 compound: bool = False,
                 rescale_frequency: RescaleFrequency = RescaleFrequency.hourly,
                 initial_swap: bool = True,
                 flip_param_dates: list[datetime] = [],
                 start_with_bull_param: bool = True):
        self.range_strategy = range_strategy
        self.indicator_mult = indicator_mult
        self.report_name = report_name
        self.to_swap = to_swap
        self.aggressive = aggressive
        self.compound = compound
        if range_strategy == RangeStrategy.atr:
            self.indicator_length_min = indicator_length_hr * 60
        elif range_strategy == RangeStrategy.std:
            self.indicator_length_min = indicator_length_hr * 60
        else:
            self.indicator_length_min = 0
        self.rescale_frequency = rescale_frequency
        self.cal_start_datetime = cal_start_datetime
        self.data_start_date = data_start_date
        self.data_end_date = data_end_date
        self.folder = folder
        self.initial_swap = initial_swap
        self.flip_param_dates = flip_param_dates
        self.start_with_bull_param = start_with_bull_param
        

# class PriceAction(Enum):
#     lower_low = "lower_low"
#     higher_high = "higher_high"

class PriceActionLog:
    def __init__(self, price: Decimal, lp_price: Decimal):
        self.price = price
        self.lp_price = lp_price
        

