import math
import re
from functools import lru_cache, wraps
from time import monotonic_ns, sleep

from babel.numbers import format_currency
from dateutil.relativedelta import relativedelta as rd
from pycoingecko import CoinGeckoAPI
from tenacity import retry, wait_fixed


class Signals:
    def __init__(self, logging):
        self.logging = logging

    # Credits goes to https://gist.github.com/Morreski/c1d08a3afa4040815eafd3891e16b945
    def timed_lru_cache(
        _func=None, *, seconds: int = 600, maxsize: int = 128, typed: bool = False
    ):
        """Extension of functools lru_cache with a timeout

        Parameters:
        seconds (int): Timeout in seconds to clear the WHOLE cache, default = 10 minutes
        maxsize (int): Maximum Size of the Cache
        typed (bool): Same value of different type will be a different entry

        """

        def wrapper_cache(f):
            f = lru_cache(maxsize=maxsize, typed=typed)(f)
            f.delta = seconds * 10**9
            f.expiration = monotonic_ns() + f.delta

            @wraps(f)
            def wrapped_f(*args, **kwargs):
                if monotonic_ns() >= f.expiration:
                    f.cache_clear()
                    f.expiration = monotonic_ns() + f.delta
                return f(*args, **kwargs)

            wrapped_f.cache_info = f.cache_info
            wrapped_f.cache_clear = f.cache_clear
            return wrapped_f

        # To allow decorator to be used without arguments
        if _func is None:
            return wrapper_cache
        else:
            return wrapper_cache(_func)

    @staticmethod
    @timed_lru_cache(seconds=10800, maxsize=None)
    @retry(wait=wait_fixed(5))
    def cgexchanges(exchange, id):
        cg = CoinGeckoAPI()
        try:
            exchange = cg.get_exchanges_tickers_by_id(id=exchange, coin_ids=id)
        except Exception as e:
            raise IOError("Coingecko API error:" + e)
        return exchange

    @staticmethod
    @timed_lru_cache(seconds=10800, maxsize=None)
    def cgvalues(rank):
        cg = CoinGeckoAPI()
        market = []

        if rank <= 250:
            pages = 1
        else:
            pages = math.ceil(rank / 250)

        for page in range(1, pages + 1):
            page = cg.get_coins_markets(vs_currency="usd", page=page, per_page=250)
            for entry in page:
                market.append(entry)

        return market

    def topvolume(self, id, volume, exchange, market):
        # Check if topcoin has enough volume

        volume_btc = 0
        if volume > 0:
            volume_target = False

            exchange = self.cgexchanges(exchange, id)

            self.logging.debug(self.cgexchanges.cache_info())

            for target in exchange["tickers"]:

                converted_btc = format_currency(
                    target["converted_volume"]["btc"], "", locale="en_US"
                )
                converted_usd = format_currency(
                    target["converted_volume"]["usd"], "USD", locale="en_US"
                )
                btc_price = (
                    target["converted_volume"]["usd"]
                    / target["converted_volume"]["btc"]
                )
                configured_usd = format_currency(
                    (volume * btc_price),
                    "USD",
                    locale="en_US",
                )

                if (
                    target["target"] == market
                    and target["converted_volume"]["btc"] >= volume
                ):
                    volume_target = True
                    volume_btc = target["converted_volume"]["btc"]
                    self.logging.info(
                        market
                        + "_"
                        + str(target["base"])
                        + " daily trading volume is "
                        + converted_btc
                        + " BTC ("
                        + converted_usd
                        + ") and over the configured value of "
                        + str(volume)
                        + " BTC ("
                        + configured_usd
                        + ") on "
                        + exchange["name"]
                    )
                    break
                elif (
                    target["target"] == market
                    and target["converted_volume"]["btc"] < volume
                ):
                    volume_target = False
                    volume_btc = target["converted_volume"]["btc"]
                    self.logging.info(
                        market
                        + "_"
                        + str(target["base"])
                        + " daily trading volume is "
                        + converted_btc
                        + " BTC ("
                        + converted_usd
                        + ") NOT passing the minimum daily BTC volume of "
                        + str(volume)
                        + " BTC ("
                        + configured_usd
                        + ") on "
                        + exchange["name"]
                    )
                    break
                else:
                    volume_target = False
                    volume_btc = 0

            if volume_btc == 0:
                if exchange["tickers"]:
                    self.logging.info(
                        market
                        + "_"
                        + target["base"]
                        + " is not traded on "
                        + exchange["name"]
                    )
                else:
                    self.logging.info("Pair is not traded on " + exchange["name"])
        else:
            volume_target = True

        return volume_target, volume_btc

    def topcoin(self, pairs, rank, volume, exchange, trademarket, first_time):

        market = self.cgvalues(rank)

        self.logging.debug(self.cgvalues.cache_info())
        self.logging.info(
            "Applying CG's top coin filter settings: marketcap <= "
            + str(rank)
            + " with daily BTC volume >= "
            + str(volume)
            + " on "
            + str(exchange)
        )

        if isinstance(pairs, list):
            self.logging.info(
                str(len(pairs))
                + " symrank pair(s) BEFORE top coin filter: "
                + str(pairs)
            )
            pairlist = []
            for pair in pairs:
                for symbol in market:
                    coin = pair
                    if (
                        coin.lower() == symbol["symbol"]
                        and int(symbol["market_cap_rank"]) <= rank
                    ):
                        self.logging.info(
                            str(pair)
                            + " is ranked #"
                            + str(symbol["market_cap_rank"])
                            + " and has passed marketcap filter limit of top #"
                            + str(rank)
                        )
                        # Prevent from being block for 30sec from too many API requests
                        if first_time:
                            sleep(2.2)
                        # Check if topcoin has enough volume
                        enough_volume, volume_btc = self.topvolume(
                            symbol["id"], volume, exchange, trademarket
                        )
                        if enough_volume:
                            pairlist.append((pair, volume_btc))
                            break
        else:
            pairlist = ""
            coin = re.search("(\w+)_(\w+)", pairs).group(2)

            for symbol in market:
                if (
                    coin.lower() == symbol["symbol"]
                    and int(symbol["market_cap_rank"]) <= rank
                ):
                    self.logging.info(
                        str(pairs)
                        + " is ranked #"
                        + str(symbol["market_cap_rank"])
                        + " and has passed marketcap filter limit of top #"
                        + str(rank)
                    )
                    # Check if topcoin has enough volume
                    enough_volume, volume_btc = self.topvolume(
                        symbol["id"], volume, exchange, trademarket
                    )
                    if enough_volume:
                        pairlist = tuple([coin, volume_btc])
                        break

        pairtuple_sorted = []
        if not pairlist:
            self.logging.info(str(pairs) + " not matching the topcoin filter criteria")
        else:
            if isinstance(pairlist, tuple):
                pairtuple_sorted = pairlist
                pairlist = trademarket + "_" + pairlist[0]
                self.logging.info(str(pairlist) + " matching top coin filter criteria")
            else:
                pairtuple_sorted = sorted(pairlist, key=lambda x: x[1], reverse=True)
                self.logging.info(
                    str(len(pairtuple_sorted))
                    + " BTC volume sorted "
                    + trademarket
                    + " symrank pair(s) AFTER top coin filter: "
                    + str(pairtuple_sorted)
                )

                pairlist = []
                for i in range(len(pairtuple_sorted)):
                    pairlist.append(pairtuple_sorted[i][0])

        return pairlist, pairtuple_sorted
