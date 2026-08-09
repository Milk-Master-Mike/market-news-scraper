from __future__ import annotations

import asyncio

from market_news_scraper.http import CachedFeedClient, PerHostCadence


class FakeTime:
    def __init__(self) -> None:
        self.now = 0.0
        self.delays: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, delay: float) -> None:
        self.delays.append(delay)
        self.now += delay


async def test_same_host_requests_are_spaced_without_real_sleep() -> None:
    fake = FakeTime()
    cadence = PerHostCadence(clock=fake.clock, sleep=fake.sleep)
    async with cadence.slot("www.sec.gov", 0.2):
        pass
    async with cadence.slot("www.sec.gov", 0.2):
        pass
    assert fake.delays == [0.2]
    assert fake.now == 0.2


async def test_different_hosts_have_independent_cadence() -> None:
    fake = FakeTime()
    cadence = PerHostCadence(clock=fake.clock, sleep=fake.sleep)
    async with cadence.slot("www.sec.gov", 0.2):
        pass
    async with cadence.slot("investor.example.test", 1.0):
        pass
    assert fake.delays == []


async def test_same_host_slots_are_serialized() -> None:
    fake = FakeTime()
    cadence = PerHostCadence(clock=fake.clock, sleep=fake.sleep)
    first_entered = asyncio.Event()
    release_first = asyncio.Event()
    order: list[str] = []

    async def first() -> None:
        async with cadence.slot("investor.example.test", 1.0):
            order.append("first-enter")
            first_entered.set()
            await release_first.wait()
            order.append("first-exit")

    async def second() -> None:
        await first_entered.wait()
        async with cadence.slot("investor.example.test", 1.0):
            order.append("second-enter")

    first_task = asyncio.create_task(first())
    second_task = asyncio.create_task(second())
    await first_entered.wait()
    await asyncio.sleep(0)
    assert order == ["first-enter"]
    release_first.set()
    await asyncio.gather(first_task, second_task)
    assert order == ["first-enter", "first-exit", "second-enter"]
    assert fake.delays == [1.0]


def test_sec_and_issuer_intervals_are_explicit(tmp_path) -> None:
    client = CachedFeedClient(
        cache_dir=tmp_path,
        cache_seconds=0,
        user_agent="test",
        timeout_seconds=1,
        max_concurrency=2,
        sec_min_interval_seconds=0.2,
        issuer_min_interval_seconds=1.0,
    )
    assert client._interval_for_host("www.sec.gov") == 0.2
    assert client._interval_for_host("sec.gov") == 0.2
    assert client._interval_for_host("investor.example.test") == 1.0
