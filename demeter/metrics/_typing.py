from enum import Enum


class MetricEnum(Enum):
    return_value = "Return"
    return_rate = "Rate of Return"
    annualized_return = "APR"
    max_draw_down = "Max Draw Down"
    sharpe_ratio = "Sharpe Ratio"
    volatility = "Volatility"
    alpha = "Alpha"
    beta = "Beta"
    benchmark_rate = "Benchmark return rate"
    annualized_benchmark_rate = "Benchmark APR"

    def __str__(self):
        return self.value

    def __repr__(self):
        return self.value
