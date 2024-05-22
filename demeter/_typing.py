from decimal import Decimal
from enum import Enum


class Formats:
    # follow the document here: https://python-reference.readthedocs.io/en/latest/docs/functions/format.html
    global_num_format: str = ".8g"


# constant value for number 1
DECIMAL_0 = Decimal(0)

# constant value for number 0
DECIMAL_1 = Decimal(1)


class TimeUnitEnum(Enum):
    """
    Time unit of moving average,

    * minute
    * hour
    * day
    """

    minute = 1
    hour = 60
    day = 60 * 24


class UnitDecimal(Decimal):
    """
    Decimal with unit, such a 1 eth.

    It's inherit from Decimal, but considering performance issues, calculate function is not override,
    so if you do calculate on this object, return type will be Decimal

    :param number: number to keep
    :type number: Decimal
    :param unit: unit of the number, e.g. eth
    :type unit: str
    """

    __integral = Decimal(1)

    def __new__(cls, value, unit: str = ""):
        obj = Decimal.__new__(cls, value)
        obj._unit = unit
        return obj

    def to_str(self):
        """
        Get formatted string like "12.34 eth". Decimal format is predefined by self.output_format attribute

        :return: formatted string
        :rtype: str
        """
        dec = self.quantize(DECIMAL_1) if (self == self.to_integral() and self < 1e29) else self.normalize()
        return "{:{}} {}".format(dec, Formats.global_num_format, self._unit)

    @property
    def unit(self):
        return self._unit

    @unit.setter
    def unit(self, value):
        self._unit = value


class EvaluatorEnum(Enum):
    """
    Types of Strategy Evaluation
    """

    all = 0
    annualized_returns = 1
    benchmark_returns = 2
    max_draw_down = 3
    net_value = 4
    profit = 5
    net_value_up_down_rate = 6
    eth_up_down_rate = 7
    position_fee_profit = 8
    position_fee_annualized_returns = 9
    position_market_time_rate = 10

    def __str__(self):
        return self.name

    def __repr__(self):
        return self.name


class TokenInfo:
    """
    Identity for a token, will be used as key for token dict.

    :param name: token symbol, will be set as unit of a token value, e.g. usdc
    :type name: str
    :param decimal: decimal of this token, e.g. 6
    :type decimal: int
    :param address: Address of token, for aave market, this attribute has to be filled to load data.
    :type decimal: str
    """

    name: str
    decimal: int
    address: str

    def __init__(self, name: str, decimal: int, address: str = ""):
        self.name = name.upper()
        self.decimal = decimal
        self.address = address.lower()

    def __str__(self):
        return self.name

    def __repr__(self):
        return self.name

    def __eq__(self, other):
        if isinstance(other, TokenInfo):
            return self.name == other.name
        else:
            return False

    def __hash__(self):
        return self.name.__hash__()


class DemeterError(Exception):
    def __init__(self, message):
        self.message = message


class ChainType(str, Enum):
    """
    Enum for chains
    """

    ethereum = "ethereum"
    polygon = "polygon"
    optimism = "optimism"
    arbitrum = "arbitrum"
    celo = "celo"
    bsc = "bsc"
    base = "base"
    avalanche = "avalanche"
    fantom = "fantom"
    harmony = "harmony"
