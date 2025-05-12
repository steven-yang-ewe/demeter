import decimal
from dataclasses import dataclass
from datetime import datetime, time
from decimal import Decimal
from typing import List, Tuple, Dict

from demeter import BaseAction
from demeter.result import MetricEnum
# from demeter.metrics import MetricEnum
from demeter.uniswap import SellAction, CollectFeeAction, AddLiquidityAction, RemoveLiquidityAction
import csv

ZERO = decimal.Decimal("0")


class ExportData(object):
    def __init__(self):
        self.time: datetime = datetime.now()
        self.price: decimal.Decimal | None = None
        self.tick: int | None = None
        self.base_removed: decimal.Decimal | None = None
        self.quote_removed: decimal.Decimal | None = None
        self.base_fee: decimal.Decimal = ZERO
        self.quote_fee: decimal.Decimal = ZERO
        self.base_added: decimal.Decimal | None = None
        self.quote_added: decimal.Decimal | None = None
        self.price_lower: decimal.Decimal | None = None
        self.price_upper: decimal.Decimal | None = None
        self.tick_lower: int | None = None
        self.tick_upper: int | None = None
        self.new_tick_lower: int | None = None
        self.new_tick_upper: int | None = None
        self.was_in_range: bool = False
        self.total_base_fee: decimal.Decimal = ZERO
        self.total_quote_fee: decimal.Decimal = ZERO
        self.lp_net_value: decimal.Decimal = ZERO
        self.total_net_value: decimal.Decimal = ZERO
        self.base_balance: decimal.Decimal = ZERO
        self.quote_balance: decimal.Decimal = ZERO
        self.indicator_value: decimal.Decimal | None = None
        self.param_type: str = "bull"
        # self.open_short: bool = False
        self.short_amount: decimal.Decimal = ZERO
        # self.short_amount_change: decimal.Decimal = ZERO
        self.short_price: decimal.Decimal | None = None
        self.short_stop_loss: decimal.Decimal | None = None
        self.lp_change: decimal.Decimal = ZERO
        self.short_stop_loss_hit: bool = False
        self.short_percent_change: decimal.Decimal = ZERO
        self.short_resolved_price: decimal.Decimal | None = ZERO

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
             # "was_in_range",
             "total_base_fee", "total_quote_fee",
             #"param_type",
             "short_amount", "short_price", "short_stop_loss", "lp_change",
             "stop_loss_hit", "short_percent_change", "short_resolved_price"])

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
                 # action.was_in_range,
                 to_str(action.total_base_fee), to_str(action.total_quote_fee),
                 #action.param_type,
                 to_str(action.short_amount), to_str(action.short_price), to_str(action.short_stop_loss), to_str(action.lp_change),
                 to_str(action.short_stop_loss_hit), to_str(action.short_percent_change), to_str(action.short_resolved_price)])

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
             "Total Fee Return",
            # "Total Base Fee Used in Swap", "Total Quote Fee Used in Swap",
             "Benchmark return rate", "Benchmark APR",
             "Spread Mean", "Spread Median", "Rebalance/Rescale Count", "Benchmark Max Draw Down",
             "Total DCA Amount", "DCA Count", "DCA Addon Count",
             "Rate of Return USD", "Total Net Value USD", "LP Net Value USD", "Total Fee USD", "Total Invested USD",
             "Invest Quote-only Return Rate",
             "Remain Short", "Total Net Value With Short", "Total Return With Short",
             "Early End Date"])


        for (strategy, m) in metrics:
            # strategy = metric[0]
            # m = metric[1]
            d = m.get("early_end_date")
            formatted_time = ""
            if d is not None and not d == ZERO:
                dt_object = datetime.fromtimestamp(d)
                formatted_time = datetime.strftime(dt_object, '%Y-%m-%d %H:%M:%S')

            csvwriter.writerow(
                [strategy, m[MetricEnum.return_value.name], m[MetricEnum.return_rate.name],
                 m[MetricEnum.annualized_return.name],
                 m[MetricEnum.max_draw_down.name], m[MetricEnum.sharpe_ratio.name], m[MetricEnum.volatility.name],
                 m[MetricEnum.alpha.name], m[MetricEnum.beta.name],
                 to_str(m["total_net_value"]), to_str(m["lp_net_value"]), to_str(m["total_fee"]),
                 to_str(m["fee_to_total_net_value"]),
                 to_str(m["total_fee_return"]),
                 #    to_str(m['total_base_swap_fee']), to_str(m['total_quote_swap_fee']),
                 m[MetricEnum.benchmark_rate.name], m[MetricEnum.annualized_benchmark_rate.name],
                 m["spread_mean"], m["spread_median"], m["action_count"], m["benchmark_max_draw_down"],
                 m["total_dca"], m["dca_count"], to_str(m.get("dca_addon_count", None)),
                 to_str(m.get("total_return_usd", None)),
                 to_str(m.get("total_net_value_usd", None)), to_str(m.get("lp_net_value_usd", None)),
                 to_str(m.get("total_fee_usd", None)), to_str(m.get("total_invested_usd", None)),
                 to_str(m.get("quote_return_usd", None)),
                 to_str(m.get("short_amount")), to_str(m.get("total_net_value_with_short")), to_str(m.get("total_gl_with_short")),
                 formatted_time])

    pass

def export_apr_results_with_short(file_path: str, metrics: List[Tuple[str, Dict[str, Decimal]]]):
    with open(file_path, 'w') as f:
        csvwriter = csv.writer(f, delimiter=',', quoting=csv.QUOTE_MINIMAL, quotechar='"')
        csvwriter.writerow(
            ["Strategy", "Return", "Rate of Return", "APR", "Max Draw Down", "Sharpe Ratio", "Volatility",
             "Alpha", "Beta",
             "Total Net Value", "LP Net Value", "Total Fee", "Fee to Total Net Value",
             "Total Fee Return",
            # "Total Base Fee Used in Swap", "Total Quote Fee Used in Swap",
             "Benchmark return rate", "Benchmark APR",
             "Spread Mean", "Spread Median", "Rebalance/Rescale Count", "Benchmark Max Draw Down",
             # "Total DCA Amount", "DCA Count", "DCA Addon Count",
             # "Rate of Return USD", "Total Net Value USD", "LP Net Value USD", "Total Fee USD", "Total Invested USD",
             # "Invest Quote-only Return Rate",
             "Remain Short", "Short Return", "Short Return Rate",
             "Total Net Value With Short", "Total Return With Short",
             "Short Stop Loss Count", "Short Total Loss",
             "Short Win Count", "Short Total Gain",
             "Short Consecutive Loss Count", "Short Actual Gain/Loss",
             "Rescale Count", "Rebalance Count"])


        for (strategy, m) in metrics:
            # strategy = metric[0]
            # m = metric[1]
            csvwriter.writerow(
                [strategy, m[MetricEnum.return_value.name], m[MetricEnum.return_rate.name],
                 m[MetricEnum.annualized_return.name],
                 m[MetricEnum.max_draw_down.name], m[MetricEnum.sharpe_ratio.name], m[MetricEnum.volatility.name],
                 m[MetricEnum.alpha.name], m[MetricEnum.beta.name],
                 to_str(m["total_net_value"]), to_str(m["lp_net_value"]), to_str(m["total_fee"]),
                 to_str(m["fee_to_total_net_value"]),
                 to_str(m["total_fee_return"]),
                 #    to_str(m['total_base_swap_fee']), to_str(m['total_quote_swap_fee']),
                 m[MetricEnum.benchmark_rate.name], m[MetricEnum.annualized_benchmark_rate.name],
                 m["spread_mean"], m["spread_median"], m["action_count"], m["benchmark_max_draw_down"],
                 # m["total_dca"], m["dca_count"], to_str(m.get("dca_addon_count", None)),
                 # to_str(m.get("total_return_usd", None)),
                 # to_str(m.get("total_net_value_usd", None)), to_str(m.get("lp_net_value_usd", None)),
                 # to_str(m.get("total_fee_usd", None)), to_str(m.get("total_invested_usd", None)),
                 # to_str(m.get("quote_return_usd", None)),
                 to_str(m.get("short_amount")), to_str(m.get("short_return")), to_str(m.get("short_return_rate")),
                 to_str(m.get("total_net_value_with_short")), to_str(m.get("total_gl_with_short")),
                 to_str(m.get("short_stop_loss_cnt")), to_str(m.get("short_total_loss_amount")),
                 to_str(m.get("short_total_win_cnt")), to_str(m.get("short_total_gain_amount")),
                 to_str(m.get("short_consecutive_loss_cnt")), to_str(m.get("short_total_gl")),
                 to_str(m.get("total_rescale_cnt")), to_str(m.get("total_rebalance_cnt")),])

    pass


