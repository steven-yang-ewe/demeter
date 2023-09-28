import unittest
from _decimal import Decimal
from datetime import datetime, timedelta, date, timezone
import numpy as np
import pandas as pd

from demeter import MarketInfo, ChainType, TokenInfo, MarketTypeEnum, Broker
from demeter.aave import (
    AaveV3PoolStatus,
    AaveTokenStatus,
    SupplyInfo,
    BorrowInfo,
    InterestRateMode,
    SupplyKey,
    BorrowKey,
    AaveV3CoreLib,
    AaveV3Market,
)
from tests.common import assert_equal_with_error

usdt = TokenInfo("USDT", 6)
dai = TokenInfo("DAI", 6)
matic = TokenInfo("WMATIC", 18)
weth = TokenInfo("weth", 18, "0x7ceb23fd6bc0add59e62ac25578270cff1b9f619")


def to_decimal(v: int) -> Decimal:
    return Decimal(v / 10**27)


class UniLpDataTest(unittest.TestCase):
    def test_load_risk_parameter(self):
        market = AaveV3Market(MarketInfo("aave_test", MarketTypeEnum.aave_v3), "./aave_risk_parameters/polygon.csv")
        self.assertTrue("WETH" in market.risk_parameters.index)
        self.assertTrue("liqThereshold" in market.risk_parameters.columns)

    def test_apy_to_rate(self):
        self.assertEqual(Decimal("0.055891616586562119114037984"), AaveV3CoreLib.rate_to_apy(Decimal("0.054385544255370350575778874")))
        self.assertEqual(Decimal("1.718281785360970821236766882"), AaveV3CoreLib.rate_to_apy(Decimal("1")))
        self.assertEqual(Decimal("0"), AaveV3CoreLib.rate_to_apy(Decimal("0")))

    def test_status_calc(self):
        market = AaveV3Market(MarketInfo("aave_test", MarketTypeEnum.aave_v3), "./aave_risk_parameters/polygon.csv", tokens=[usdt, dai, matic])
        timestamp = datetime(2023, 9, 12, 15)

        price = pd.DataFrame(data={"USDT": Decimal(0.999990), "DAI": Decimal(1), "WMATIC": Decimal(0.509952)}, index=[timestamp])

        pool_stat = AaveV3PoolStatus(timestamp, {})
        pool_stat.tokens[dai] = AaveTokenStatus(
            liquidity_rate=to_decimal(20721780596069986118711585),
            stable_borrow_rate=to_decimal(54183967335086321747448589),
            variable_borrow_rate=to_decimal(33471738680690573979588711),
            liquidity_index=to_decimal(1024896375683851651969973538),
            variable_borrow_index=to_decimal(1043477569752596545043775819),
        )
        pool_stat.tokens[usdt] = AaveTokenStatus(
            liquidity_rate=to_decimal(19374318747418950359017069),
            stable_borrow_rate=to_decimal(54385544255370350575778874),
            variable_borrow_rate=to_decimal(31166051358525919566436671),
            liquidity_index=to_decimal(1046424838969468347281558168),
            variable_borrow_index=to_decimal(1061829096134252340370625412),
        )
        pool_stat.tokens[matic] = AaveTokenStatus(
            liquidity_rate=to_decimal(34590050812934499694395450),
            stable_borrow_rate=to_decimal(81000000000000000000000000),
            variable_borrow_rate=to_decimal(59301392614184653189709969),
            liquidity_index=to_decimal(1033989834711222334753899684),
            variable_borrow_index=to_decimal(1091266512915678985090375114),
        )
        s_dai = SupplyKey(dai)
        s_matic = SupplyKey(matic)
        b_matic_v = BorrowKey(matic, InterestRateMode.variable)
        b_usdt_s = BorrowKey(usdt, InterestRateMode.stable)
        market.set_market_status(timestamp=timestamp, data=pool_stat, price=price.iloc[0])
        market._supplies[s_dai] = SupplyInfo(Decimal(97.56471188217428), True)
        market._supplies[s_matic] = SupplyInfo(Decimal(19.35174760735758), True)
        market._borrows[b_matic_v] = BorrowInfo(Decimal(4.583153411559582))
        market._borrows[b_usdt_s] = BorrowInfo(Decimal(4.709392084139431))
        stat = market.get_market_balance()
        print(stat)
        # supplies
        assert_equal_with_error(stat.supplys[s_dai].value, Decimal("99.99"), 0.001)
        assert_equal_with_error(stat.supplys[s_dai].apy, Decimal("0.02093"), 0.001)
        assert_equal_with_error(stat.supplys[s_dai].amount, Decimal("99.99"), 0.001)

        assert_equal_with_error(stat.supplys[s_matic].value, Decimal("10.21"), 0.001)
        assert_equal_with_error(stat.supplys[s_matic].apy, Decimal("0.0352"), 0.001)
        assert_equal_with_error(stat.supplys[s_matic].amount, Decimal("20.01"), 0.001)

        assert_equal_with_error(stat.supply_balance, Decimal("110.19"), 0.001)
        assert_equal_with_error(stat.supply_apy, Decimal("0.02226"), 0.001)
        assert_equal_with_error(stat.collateral_balance, Decimal("110.18877975"), 0.001)

        # borrows
        assert_equal_with_error(stat.borrows[b_usdt_s].value, Decimal("5"), 0.001)
        assert_equal_with_error(stat.borrows[b_usdt_s].apy, Decimal("0.0559"), 0.001)
        assert_equal_with_error(stat.borrows[b_usdt_s].amount, Decimal("5"), 0.001)

        assert_equal_with_error(stat.borrows[b_matic_v].value, Decimal("2.55"), 0.001)
        assert_equal_with_error(stat.borrows[b_matic_v].apy, Decimal("0.0611"), 0.001)
        assert_equal_with_error(stat.borrows[b_matic_v].amount, Decimal("5"), 0.001)

        assert_equal_with_error(stat.borrow_balance, Decimal("7.55"), 0.001)
        assert_equal_with_error(stat.borrow_apy, Decimal("0.0576"), 0.001)

        assert_equal_with_error(stat.health_factor, Decimal("11.711114759422364164"), 0.001)
        assert_equal_with_error(stat.liquidation_threshold, Decimal("0.8025"), 0.001)
        assert_equal_with_error(stat.current_ltv, Decimal("0.7525"), 0.001)
        assert_equal_with_error(stat.net_apy, Decimal("0.01965"), 0.001)

        self.assertTrue(assert_equal_with_error(stat.net_value, Decimal("102.64"), 0.001))

        # net_apy=Decimal('0.01683792283834931728886791969'))

    def test_data(self):
        market = AaveV3Market(MarketInfo("aave_test", MarketTypeEnum.aave_v3), "./aave_risk_parameters/polygon.csv")
        start = datetime(2023, 10, 1, 0, 0)
        data_size = 10
        df_index = pd.date_range(start, start + timedelta(minutes=data_size - 1), freq="1T")
        token_data = {
            "liquidity_rate": np.zeros(shape=data_size),
            "stable_borrow_rate": np.zeros(shape=data_size),
            "variable_borrow_rate": np.zeros(shape=data_size),
            "liquidity_index": np.zeros(shape=data_size),
            "variable_borrow_index": np.zeros(shape=data_size),
        }
        token_df = pd.DataFrame(index=df_index, data=token_data)

        market.set_token_data(usdt, token_df)
        market.set_token_data(matic, token_df)

        self.assertEqual(len(market.data.columns), 10)
        self.assertEqual(len(market.data.index), 10)
        self.assertTrue(("USDT", "liquidity_rate") in market.data.columns)
        self.assertEqual(market.data[usdt.name]["liquidity_rate"].iloc[0], 0)
        pass

    def test_load_data(self):
        market_key = MarketInfo("aave_test", MarketTypeEnum.aave_v3)
        market = AaveV3Market(market_key, "./aave_risk_parameters/polygon.csv")
        market.data_path = "../samples/data"
        market.load_data("polygon", [weth], date(2023, 8, 14), date(2023, 8, 17))
        self.assertEqual(len(market.data.index), 1440 * 4)
        self.assertEqual(market.data.index[0].to_pydatetime(), datetime(2023, 8, 14, 0, 0, tzinfo=timezone.utc))
        self.assertEqual(market.data.index[1440 * 4 - 1].to_pydatetime(), datetime(2023, 8, 17, 23, 59, tzinfo=timezone.utc))
        self.assertIn(("WETH", "stable_borrow_rate"), market.data)
        self.assertTrue(1 < market.data.iloc[0][weth.name]["liquidity_index"] < 1.1)
        pass

    def get_test_market(self):
        market_key = MarketInfo("aave_test", MarketTypeEnum.aave_v3)
        market = AaveV3Market(market_key, "./aave_risk_parameters/polygon.csv", tokens=[weth])
        # market.data_path = "../samples/data"
        # market.load_data("polygon", [weth], date(2023, 8, 14), date(2023, 8, 17))
        t = datetime(2023, 8, 1)
        price_series = pd.Series(data=[Decimal(1000), Decimal(1)], index=[weth.name, dai.name])
        market.set_market_status(
            timestamp=t,
            data=AaveV3PoolStatus(
                timestamp=t,
                tokens={
                    weth: AaveTokenStatus(
                        liquidity_rate=Decimal("0.05"),
                        stable_borrow_rate=Decimal("0.1"),
                        variable_borrow_rate=Decimal("0.08"),
                        liquidity_index=Decimal("1.6"),
                        variable_borrow_index=Decimal("1"),
                    ),
                    dai: AaveTokenStatus(
                        liquidity_rate=Decimal("0.08"),
                        stable_borrow_rate=Decimal("0.12"),
                        variable_borrow_rate=Decimal("0.1"),
                        liquidity_index=Decimal("1.6"),
                        variable_borrow_index=Decimal("1.6"),
                    ),
                },
            ),
            price=price_series,
        )
        amount = Decimal(5)
        broker = Broker()
        broker.set_balance(weth, amount)
        market.broker = broker
        return market_key, market, broker, price_series

    def test_supply(self):
        market_key, market, broker, price_series = self.get_test_market()
        amount = broker.get_token_balance(weth)
        supply_key = market.supply(weth, amount, False)

        self.assertEqual(len(market._supplies), 1)
        self.assertEqual(market._supplies[supply_key].base_amount, amount / market.market_status.tokens[weth].liquidity_index)
        self.assertEqual(broker.get_token_balance(weth), 0)

        self.assertEqual(market.supplies[supply_key].amount, amount)
        self.assertEqual(market.supplies[supply_key].value, amount * price_series[weth.name])
        self.assertEqual(market.supplies[supply_key].collateral, False)
        self.assertEqual(market.total_supply_value, amount * price_series[weth.name])
        pass

    def test_supply_to_the_same(self):
        market_key, market, broker, price_series = self.get_test_market()
        supply_key = market.supply(weth, Decimal(1), False)
        supply_key = market.supply(weth, Decimal(4), False)

        self.assertEqual(len(market._supplies), 1)
        self.assertEqual(market._supplies[supply_key].base_amount, Decimal(5) / market.market_status.tokens[weth].liquidity_index)
        self.assertEqual(broker.get_token_balance(weth), 0)

        pass

    def test_collateral(self):
        market_key, market, broker, price_series = self.get_test_market()
        amount = broker.get_token_balance(weth)

        supply_key = market.supply(weth, amount, True)

        self.assertEqual(len(market._supplies), 1)
        self.assertEqual(market._supplies[supply_key].base_amount, Decimal(5) / market.market_status.tokens[weth].liquidity_index)
        self.assertEqual(market._supplies[supply_key].collateral, True)

        self.assertEqual(broker.get_token_balance(weth), 0)

        collaterals = market.collateral_value
        value = Decimal(5) * price_series[weth.name]
        self.assertEqual(collaterals[supply_key], value)
        self.assertEqual(market.total_collateral_value, value)

    def test_different_collateral(self):
        market_key, market, broker, price_series = self.get_test_market()
        try:
            supply_key = market.supply(weth, Decimal(1), True)
            supply_key = market.supply(weth, Decimal(1), False)
        except AssertionError as e:
            self.assertTrue("Collateral different from existing supply" in str(e))

    def test_supply_with_float(self):
        market_key, market, broker, price_series = self.get_test_market()
        supply_key = market.supply(weth, 1.2345, True)
        self.assertEqual(market.supplies[supply_key].amount, Decimal("1.2345"))

    def test_withdraw(self):
        market_key, market, broker, price_series = self.get_test_market()
        amount = broker.get_token_balance(weth)

        supply_key = market.supply(weth, amount, True)
        market.withdraw(supply_key, 5)
        self.assertEqual(broker.get_token_balance(weth), amount)
        self.assertTrue(supply_key not in market._supplies)

        supply_key = market.supply(weth, amount, True)
        market.withdraw(supply_key, 2)
        self.assertEqual(broker.get_token_balance(weth), Decimal(2))
        self.assertEqual(market.supplies[supply_key].amount, Decimal(3))
        self.assertEqual(market.supplies[supply_key].base_amount, Decimal("1.875"))
        pass

    def test_max_withdraw(self):
        market_key, market, broker, price_series = self.get_test_market()
        market: AaveV3Market = market
        supply_key = market.supply(weth, Decimal("5"), True)
        borrow_key = market.borrow(dai, 3300, InterestRateMode.variable)
        max_withdraw = market.get_max_withdraw_amount(supply_key)
        self.assertEqual(max_withdraw, Decimal(1))
        market.withdraw(supply_key, max_withdraw)
        self.assertEqual(market.health_factor, Decimal(1))
        pass

    def test_withdraw_too_much(self):
        market_key, market, broker, price_series = self.get_test_market()
        amount = broker.get_token_balance(weth)

        supply_key = market.supply(weth, amount, True)
        try:
            market.withdraw(supply_key, 6)
        except AssertionError as e:
            self.assertIn("not enough available user balance", str(e))

        borrow_key = market.borrow(dai, 3300, InterestRateMode.variable)
        max_withdraw = market.get_max_withdraw_amount(supply_key)
        try:
            market.withdraw(supply_key, max_withdraw * Decimal(1.01))
        except AssertionError as e:
            self.assertIn("health factor lower than liquidation threshold", str(e))
            self.assertEqual(market.supplies[supply_key].amount, amount)

    def test_borrow(self):
        market_key, market, broker, price_series = self.get_test_market()
        market: AaveV3Market = market
        amount = broker.get_token_balance(weth)

        supply_key = market.supply(weth, amount, True)
        borrow_key = market.borrow(dai, 1000, InterestRateMode.variable)

        self.assertEqual(market.borrows[borrow_key].amount, 1000)
        self.assertEqual(market.borrows[borrow_key].base_amount, 625)
        self.assertEqual(market.health_factor, Decimal("4.125"))
        self.assertEqual(market.current_ltv, Decimal("0.8"))
        pass

    def test_borrow_too_much(self):
        market_key, market, broker, price_series = self.get_test_market()
        market: AaveV3Market = market
        amount = broker.get_token_balance(weth)

        supply_key = market.supply(weth, amount, True)
        try:
            borrow_key = market.borrow(dai, 5000, InterestRateMode.variable)
        except AssertionError as e:
            self.assertIn("collateral cannot cover new borrow", str(e))

    def test_borrow_stable(self):
        market_key, market, broker, price_series = self.get_test_market()
        market: AaveV3Market = market
        amount = broker.get_token_balance(weth)

        supply_key = market.supply(weth, amount, True)
        try:
            borrow_key = market.borrow(weth, 0.1, InterestRateMode.stable)
        except AssertionError as e:
            self.assertIn("stable borrowing not enabled", str(e))

        borrow_key = market.borrow(dai, 1000, InterestRateMode.stable)

        borrows = market.borrows[borrow_key]
        self.assertEqual(borrows.amount, 1000)
        self.assertEqual(borrows.base_amount, 625)
        self.assertEqual(market.health_factor, Decimal("4.125"))
        self.assertEqual(market.current_ltv, Decimal("0.8"))

        pass

    def test_repay(self):
        market_key, market, broker, price_series = self.get_test_market()
        market: AaveV3Market = market
        amount = broker.get_token_balance(weth)

        supply_key = market.supply(weth, amount, True)
        borrow_key = market.borrow(dai, 1000, InterestRateMode.variable)
        self.assertEqual(broker.get_token_balance(dai), Decimal(1000))
        repay_amount = market.get_max_repay_amount(borrow_key)

        try:
            market.repay(borrow_key, repay_amount + 1)
        except AssertionError as e:
            self.assertIn("amount exceed debt", str(e))

        market.repay(borrow_key, repay_amount)
        self.assertEqual(len(market._borrows), Decimal(0))
        self.assertEqual(broker.get_token_balance(dai), Decimal(0))
        pass

    def test_max_borrow(self):
        market_key, market, broker, price_series = self.get_test_market()
        market: AaveV3Market = market

        supply_key = market.supply(weth, Decimal(1), True)
        max_borrow = market.get_max_borrow_amount(weth)
        self.assertEqual(max_borrow, Decimal("0.792"))
        try:
            borrow_key = market.borrow(weth, max_borrow / Decimal(0.99), InterestRateMode.variable)
        except AssertionError as e:
            self.assertIn("collateral cannot cover new borrow", str(e))

    def test_change_collateral(self):
        market_key, market, broker, price_series = self.get_test_market()
        market: AaveV3Market = market

        supply_key = market.supply(weth, Decimal(3), False)
        market.change_collateral(False, supply_key)
        self.assertEqual(market._supplies[supply_key].collateral, False)

        market.change_collateral(True, supply_key)
        self.assertEqual(market._supplies[supply_key].collateral, True)

        borrow_key = market.borrow(dai, 1000, InterestRateMode.variable)
        hf_old = market.health_factor
        try:
            market.change_collateral(False, supply_key)
        except AssertionError as e:
            self.assertIn("health factor lower than liquidation threshold", str(e))
        self.assertEqual(hf_old, market.health_factor)

        market.repay(borrow_key, 1000)
        market.change_collateral(False, supply_key)
        self.assertEqual(market._supplies[supply_key].collateral, False)

    def test_liquidate_half(self):
        market_key, market, broker, price_series = self.get_test_market()
        market: AaveV3Market = market
        supply_key = market.supply(weth, Decimal("4.2"), True)
        borrow_key = market.borrow(dai, 3300, InterestRateMode.variable)
        self.assertEqual(market.health_factor, Decimal("1.05"))
        market.update()  # trigger liquidate, nothing will happen as hf > 1

        t = datetime(2023, 8, 1, 1)
        price_series = pd.Series(data=[Decimal(920), Decimal(1)], index=[weth.name, dai.name])
        old_market_status = market.market_status
        market.set_market_status(
            timestamp=t,
            data=AaveV3PoolStatus(timestamp=t, tokens=old_market_status.tokens),
            price=price_series,
        )
        self.assertEqual(market.health_factor, Decimal("0.966"))
        market.update()  # trigger again

        self.assertEqual(market.health_factor, Decimal("1.06575"))
        # 920 * (4.2 - 1650/920*1.05)
        # collateral_price * collateral_amount  - half_debt_amount * delt_price * (1 + liq_bones))
        self.assertEqual(market.supplies[supply_key].value, Decimal("2131.5"))
        self.assertEqual(market.borrows[borrow_key].value, Decimal("1650"))  # 3300 - 3300/2, delt - liquidated

        pass

    def test_liquidate_all(self):
        market_key, market, broker, price_series = self.get_test_market()
        market: AaveV3Market = market
        supply_key = market.supply(weth, Decimal("4.2"), True)
        borrow_key = market.borrow(dai, 3300, InterestRateMode.variable)
        self.assertEqual(market.health_factor, Decimal("1.05"))
        market.update()  # trigger liquidate, nothing will happen as hf > 1

        t = datetime(2023, 8, 1, 1)
        price_series = pd.Series(data=[Decimal(900), Decimal(1)], index=[weth.name, dai.name])
        old_market_status = market.market_status
        market.set_market_status(
            timestamp=t,
            data=AaveV3PoolStatus(timestamp=t, tokens=old_market_status.tokens),
            price=price_series,
        )
        self.assertEqual(market.health_factor, Decimal("0.945"))
        market.update()  # trigger again

        self.assertEqual(market.health_factor, Decimal("inf"))

        self.assertEqual(market.supplies[supply_key].value, Decimal("315"))
        self.assertEqual(len(market.borrows), 0)

        pass

    def test_liquidate_all_collateral_but_still_have_delt(self):
        market_key, market, broker, price_series = self.get_test_market()
        market: AaveV3Market = market
        supply_key = market.supply(weth, Decimal("4.2"), True)
        borrow_key = market.borrow(dai, 3300, InterestRateMode.variable)
        self.assertEqual(market.health_factor, Decimal("1.05"))
        market.update()  # trigger liquidate, nothing will happen as hf > 1

        t = datetime(2023, 8, 1, 1)
        price_series = pd.Series(data=[Decimal(800), Decimal(1)], index=[weth.name, dai.name])
        old_market_status = market.market_status
        market.set_market_status(
            timestamp=t,
            data=AaveV3PoolStatus(timestamp=t, tokens=old_market_status.tokens),
            price=price_series,
        )
        self.assertEqual(market.health_factor, Decimal("0.84"))
        market.update()  # trigger again

        self.assertEqual(market.health_factor, Decimal("0"))

        self.assertNotIn(supply_key, market.supplies)
        self.assertEqual(market.borrows[borrow_key].value, Decimal("100"))  # 3300 - 3300/2, delt - liquidated

        pass
