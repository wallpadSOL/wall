from agent import detector as d


def mk(rates_per_tick, start=1000, tick=10):
    """Build counts from a list of per-tick message counts, oldest first."""
    return {start + i * tick: n for i, n in enumerate(rates_per_tick)}


def test_warming_until_a_minute_is_observed():
    counts = mk([50] * 10, start=1000)
    # joined at 1000; rated tick at now=1065 is 1050, needs 990..1040 -> no
    v = d.evaluate(counts=counts, now=1065, watch_start=1000)
    assert v.state == d.WARMING
    # at now=1075 rated=1060, baseline ticks 1000..1050 all observed
    v = d.evaluate(counts=counts, now=1075, watch_start=1000)
    assert v.state != d.WARMING
    assert v.ticks_observed == 7


def test_quiet_at_steady_chat():
    counts = mk([60] * 8, start=1000)       # 6/s all along
    v = d.evaluate(counts=counts, now=1075, watch_start=1000)
    assert v.state == d.QUIET
    assert v.rate == 6.0 and v.baseline == 6.0 and v.multiplier == 1.0


def test_spike_at_three_times_the_last_minute():
    counts = mk([60] * 6 + [190], start=1000)   # 6/s then 19/s
    v = d.evaluate(counts=counts, now=1075, watch_start=1000)
    assert v.state == d.SPIKE
    assert v.multiplier > 3.0
    assert v.cooldown_until == 1075 + 90


def test_floor_blocks_tiny_chats_but_not_warm():
    counts = mk([3] * 6 + [15], start=1000)     # 0.3/s -> 1.5/s, 5x but thin
    v = d.evaluate(counts=counts, now=1075, watch_start=1000)
    assert v.state == d.WARM
    assert v.multiplier >= 3.0


def test_cooldown_turns_spike_into_warm():
    counts = mk([60] * 6 + [190, 200], start=1000)
    v1 = d.evaluate(counts=counts, now=1075, watch_start=1000)
    assert v1.fired
    v2 = d.evaluate(counts=counts, now=1085, watch_start=1000,
                    cooldown_until=v1.cooldown_until)
    assert v2.state == d.WARM and not v2.fired


def test_baseline_min_keeps_dead_chats_honest():
    counts = mk([0] * 6 + [25], start=1000)     # nothing, then 2.5/s
    v = d.evaluate(counts=counts, now=1075, watch_start=1000)
    assert v.baseline == 0.0
    assert v.multiplier == 2.5 / 0.3
    assert v.state == d.SPIKE


def test_counts_by_tick_aligns_to_epoch():
    c = d.counts_by_tick([1000, 1003.9, 1009.99, 1010, 1019])
    assert c == {1000: 3, 1010: 2}


def test_line_for():
    assert d.line_for(6.0) == 18.0
    assert d.line_for(0.1) == 2.0
