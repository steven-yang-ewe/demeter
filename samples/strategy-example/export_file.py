import decimal
from datetime import datetime
from decimal import Decimal
from typing import List, Tuple, Dict

from demeter import BaseAction
from demeter.metrics import MetricEnum
from demeter.uniswap import SellAction, CollectFeeAction, AddLiquidityAction, RemoveLiquidityAction
import csv


class ExportData(object):
    time: datetime
    price: decimal.Decimal | None
    tick: int | None
    base_removed: decimal.Decimal | None
    quote_removed: decimal.Decimal | None
    base_fee: decimal.Decimal
    quote_fee: decimal.Decimal
    base_added: decimal.Decimal | None
    quote_added: decimal.Decimal | None
    price_lower: decimal.Decimal | None
    price_upper: decimal.Decimal | None
    tick_lower: int | None
    tick_upper: int | None
    new_tick_lower: int | None
    new_tick_upper: int | None
    was_in_range: bool
    total_base_fee: decimal.Decimal
    total_quote_fee: decimal.Decimal
    lp_net_value: decimal.Decimal
    total_net_value: decimal.Decimal
    base_balance: decimal.Decimal
    quote_balance: decimal.Decimal
    indicator_value: decimal.Decimal | None


def to_str(d: decimal.Decimal | int | None) -> str:
    if d is not None:
        if isinstance(d, decimal.Decimal) and d == decimal.Decimal(0):
            return "0"
        else:
            return str(d)
    else:
        return ""


def export_file(file_path: str, actions: List[ExportData]):
    with open(file_path, 'w') as f:
        csvwriter = csv.writer(f, delimiter=',', quoting=csv.QUOTE_MINIMAL, quotechar='"')
        csvwriter.writerow(
            ["time", "price", "total_net_value", "lp_net_value",
             "base_removed", "quote_removed", "base_added", "quote_added",
             "base_balance", "quote_balance",
             "base_fee", "quote_fee", "tick", "new_tick_lower", "new_tick_upper",
             "indicator_value", "tick_lower", "tick_upper",
             "price_lower", "price_upper",
             "was_in_range", "total_base_fee", "total_quote_fee"])

        for action in actions:
            csvwriter.writerow(
                [action.time.strftime("%Y-%m-%d %H:%M:%S"), to_str(action.price), to_str(action.total_net_value),
                 to_str(action.lp_net_value),
                 to_str(action.base_removed), to_str(action.quote_removed), to_str(action.base_added),
                 to_str(action.quote_added),
                 to_str(action.base_balance), to_str(action.quote_balance),
                 to_str(action.base_fee), to_str(action.quote_fee), to_str(action.tick), to_str(action.new_tick_lower),
                 to_str(action.new_tick_upper),
                 to_str(action.indicator_value), to_str(action.tick_lower), to_str(action.tick_upper),
                 to_str(action.price_lower), to_str(action.price_upper),
                 action.was_in_range, to_str(action.total_base_fee), to_str(action.total_quote_fee)])

        pass


# def export_file(file_path: str, actions: List[BaseAction]):
#     with open(file_path, 'w') as f:
#         csvwriter = csv.writer(f, delimiter=',', quoting=csv.QUOTE_MINIMAL, quotechar='"')
#         csvwriter.writerow(
#             ["time", "action", "base", "quote", "base_after", "quote_after", "price_lower", "price_upper", "tick_lower",
#              "tick_upper"])
#
#         for action in actions:
#             if isinstance(action, SellAction):
#                 csvwriter.writerow(
#                     [action.timestamp.strftime("%Y-%m-%d %H:%M:%S"), action.action_type, action.base_change,
#                      action.quote_change, action.base_balance_after, action.quote_balance_after, action.price,
#                      action.price])
#                 pass
#             elif isinstance(action, CollectFeeAction):
#                 csvwriter.writerow(
#                     [action.timestamp.strftime("%Y-%m-%d %H:%M:%S"), action.action_type, action.base_amount,
#                      action.quote_amount, action.base_balance_after, action.quote_balance_after])
#                 pass
#             elif isinstance(action, AddLiquidityAction):
#                 csvwriter.writerow(
#                     [action.timestamp.strftime("%Y-%m-%d %H:%M:%S"), action.action_type, action.base_amount_actual,
#                      action.quote_amount_actual, action.base_balance_after, action.quote_balance_after,
#                      action.lower_quote_price, action.upper_quote_price, action.position[0], action.position[1]])
#                 pass
#             elif isinstance(action, RemoveLiquidityAction):
#                 csvwriter.writerow(
#                     [action.timestamp.strftime("%Y-%m-%d %H:%M:%S"), action.action_type, action.base_amount,
#                      action.quote_amount, action.base_balance_after, action.quote_balance_after,
#                      "", "", action.position[0], action.position[1]])
#                 pass
#
#         pass

def export_apr_results(file_path: str, metrics: List[Tuple[str, Dict[str, Decimal]]]):
    with open(file_path, 'w') as f:
        csvwriter = csv.writer(f, delimiter=',', quoting=csv.QUOTE_MINIMAL, quotechar='"')
        csvwriter.writerow(
            ["Strategy", "Return", "Rate of Return", "APR", "Max Draw Down", "Sharpe Ratio", "Volatility",
             "Alpha", "Beta",
             "Total Net Value", "LP Net Value", "Total Fee", "Fee to Total Net Value",
             "Total Base Fee Used in Swap", "Total Quote Fee Used in Swap",
             "Benchmark return rate", "Benchmark APR",
             "Spread Mean", "Spread Median", "Rebalance/Rescale Count"])

        for (strategy, m) in metrics:
            # strategy = metric[0]
            # m = metric[1]
            csvwriter.writerow(
                [strategy, m[MetricEnum.return_value.name], m[MetricEnum.return_rate.name],
                 m[MetricEnum.annualized_return.name],
                 m[MetricEnum.max_draw_down.name], m[MetricEnum.sharpe_ratio.name], m[MetricEnum.volatility.name],
                 m[MetricEnum.alpha.name], m[MetricEnum.beta.name],
                 m["total_net_value"], m["lp_net_value"], m["total_fee"], m["fee_to_total_net_value"],
                 m['total_base_swap_fee'], m['total_quote_swap_fee'],
                 m[MetricEnum.benchmark_rate.name], m[MetricEnum.annualized_benchmark_rate.name],
                 m["spread_mean"], m["spread_median"],m["action_count"], ])

    pass
