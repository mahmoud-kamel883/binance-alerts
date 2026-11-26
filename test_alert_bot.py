import json, os, tempfile, unittest, urllib.error, io
import alert_bot as b

IV = 3600000
NOW = 1_800_000_000_000 - (1_800_000_000_000 % IV) + 5 * 60000  # 5 min after an hour boundary

def make_klines(n=120, signal=True):
    """Flat-ish series that dips then recovers with a volume spike on the last closed candle."""
    k = []
    t0 = NOW - (n + 1) * IV + (IV - 5 * 60000) - IV  # align so open times are hour boundaries
    t0 = (NOW - NOW % IV) - n * IV
    price = 100.0
    for i in range(n):
        if i < 60: price *= 1.001
        elif i < n - 4: price *= 0.995
        else: price *= 1.03 if signal else 0.995
        vol = 100.0 * (6 if (signal and i == n - 1) else 1)
        o = t0 + i * IV
        k.append([o, str(price), str(price * 1.002), str(price * 0.99), str(price), str(vol), o + IV - 1])
    k.append([t0 + n * IV, "1", "1", "1", "1", "1", t0 + (n + 1) * IV - 1])  # open candle
    return k

CFG = dict(b.CFG, watchlist_file=None, required_conditions=["rsi_recovery", "volume_spike"], min_conditions_met=2, rsi_cross_level=40,
           rsi_lookback=8, cooldown_candles=3, top_n=1, max_alerts_per_run=5)

def fake_get(klines):
    def g(path, params=None):
        if path.endswith("ticker/24hr"):
            return [{"symbol": "ABCUSDT", "quoteVolume": "9000000", "priceChangePercent": "2.0"}]
        return klines
    return g

class T(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(); self.sp = os.path.join(self.d, "state.json"); self.log = []

    def go(self, kl, send, dry=False, now=NOW):
        return b.run(now_ms=now, dry=dry, state_path=self.sp, c=CFG, getf=fake_get(kl), sendf=send, log=self.log.append)

    def test_signal_fires_with_time_and_units(self):
        msgs = []; r = self.go(make_klines(), msgs.append)
        self.assertEqual(r["sent"], 1, self.log)
        self.assertIn("USDT", msgs[0]); self.assertIn("UTC", msgs[0]); self.assertIn("ABC/USDT", msgs[0])

    def test_message_format_neutral_with_reference_levels(self):
        res = {"rsi_recovery": True, "above_ma": True, "volume_spike": True, "breakout": False}
        m = b.fmt("ABCUSDT", 100.0, res, 45.0, 1.0, NOW - 5 * 60000 - 1)
        for p, v in (("+2", "102"), ("+4", "104"), ("+10", "110"), ("+25", "125")):
            self.assertIn("%s٪ ➜ %s" % (p, v), m)
        self.assertIn("SPOT", m); self.assertIn("3/4", m)
        self.assertIn("مش تقييم لاحتمال النجاح", m)
        self.assertIn("ليس توصية", m)
        for bad in ("BUY", "Stop", "TP1", "وقف", "شراء الآن", "e-"):
            self.assertNotIn(bad, m)

    def test_small_price_has_no_scientific_notation(self):
        self.assertNotIn("e-", b._num(0.00000123))
        self.assertEqual(b._num(0.5), "0.5")
        self.assertEqual(b._num(1234.5), "1234.5")

    def test_explicit_watchlist_only(self):
        import tempfile
        d = tempfile.mkdtemp(); fn = os.path.join(d, "wl.txt")
        open(fn, "w").write("AAA\nBBB\n")
        orig = b.HERE; b.HERE = d
        try:
            cfg = dict(b.CFG, watchlist_file="wl.txt", min_quote_volume_24h=0, top_n=1)
            rows = [{"symbol": "AAAUSDT", "quoteVolume": "10", "priceChangePercent": "1"},
                    {"symbol": "BBBUSDT", "quoteVolume": "20", "priceChangePercent": "1"},
                    {"symbol": "CCCUSDT", "quoteVolume": "999999999", "priceChangePercent": "1"},
                    {"symbol": "AAABTC", "quoteVolume": "5", "priceChangePercent": "1"}]
            got = b._watch(lambda p, q=None: rows, cfg)
        finally:
            b.HERE = orig
        self.assertEqual([r[1] for r in got], ["BBBUSDT", "AAAUSDT"])

    def test_duplicate_invocation_same_candle(self):
        msgs = []
        self.go(make_klines(), msgs.append)
        r = self.go(make_klines(), msgs.append, now=NOW + 15 * 60000)  # 15-min rerun, same closed candle
        self.assertEqual(len(msgs), 1); self.assertEqual(r["sent"], 0)

    def test_failed_send_not_marked_and_retried(self):
        def bad(m): raise RuntimeError("boom")
        r = self.go(make_klines(), bad)
        self.assertEqual(r["failures"], 1); self.assertEqual(r["sent"], 0)
        self.assertEqual(b.load_state(self.sp)["sent"], {})
        msgs = []; r2 = self.go(make_klines(), msgs.append)
        self.assertEqual(len(msgs), 1)

    def test_dry_run_sends_and_stores_nothing(self):
        msgs = []; self.go(make_klines(), msgs.append, dry=True)
        self.assertEqual(msgs, []); self.assertFalse(os.path.exists(self.sp))

    def test_binance_429_stops_scan_no_retry(self):
        calls = []
        def g(path, params=None):
            calls.append(path); raise b.RateLimited("binance", 30)
        r = b.run(now_ms=NOW, dry=False, state_path=self.sp, c=CFG, getf=g, sendf=lambda m: None, log=self.log.append)
        self.assertEqual(len(calls), 1); self.assertIn("rate limited", r["stopped"])

    def test_rate_limit_block_persists_across_runs(self):
        def g(path, params=None): raise b.RateLimited("binance", 120)
        b.run(now_ms=NOW, dry=True, state_path=self.sp, c=CFG, getf=g, sendf=lambda m: None, log=self.log.append)
        self.assertEqual(b.load_state(self.sp)["blocked_until"]["binance"], NOW + 120000)
        calls = []
        def g2(path, params=None): calls.append(path); return []
        r = b.run(now_ms=NOW + 60000, dry=True, state_path=self.sp, c=CFG, getf=g2, sendf=lambda m: None, log=self.log.append)
        self.assertEqual(calls, []); self.assertIn("blocked until", r["stopped"])
        r = b.run(now_ms=NOW + 130000, dry=True, state_path=self.sp, c=CFG, getf=g2, sendf=lambda m: None, log=self.log.append)
        self.assertIsNone(r["stopped"]); self.assertTrue(calls)

    def test_get_maps_429_and_418_no_retries(self):
        n = {"c": 0}
        def fake_open(url, timeout=0):
            n["c"] += 1
            raise urllib.error.HTTPError(url, 429, "x", {"Retry-After": "17"}, io.BytesIO(b""))
        orig = b.urllib.request.urlopen; b.urllib.request.urlopen = fake_open
        try:
            with self.assertRaises(b.RateLimited) as cm: b.get("/api/v3/ping", sleep=lambda s: None)
            self.assertEqual(cm.exception.retry_after, 17); self.assertEqual(n["c"], 1)
        finally: b.urllib.request.urlopen = orig

    def test_telegram_429_stops_and_not_marked(self):
        def lim(m): raise b.RateLimited("telegram", 5)
        r = self.go(make_klines(), lim)
        self.assertIn("telegram", r["stopped"]); self.assertEqual(b.load_state(self.sp)["sent"], {})

    def test_stale_data_skipped(self):
        msgs = []; r = self.go(make_klines(), msgs.append, now=NOW + 5 * IV)
        self.assertEqual(msgs, []); self.assertTrue(any("stale" in l for l in self.log))

    def test_no_signal_no_alert(self):
        msgs = []; self.go(make_klines(signal=False), msgs.append); self.assertEqual(msgs, [])

    def test_state_selftest_writes_marker(self):
        b.state_selftest(self.sp)
        self.assertIn("selftest_at_ms", b.load_state(self.sp))

    def test_no_identity_cache_reuse(self):
        a = b.prepare(make_klines()[:-1]); c2 = b.prepare(make_klines(signal=False)[:-1])
        self.assertNotEqual(a["closes"][-1], c2["closes"][-1])

if __name__ == "__main__":
    unittest.main(verbosity=2)
