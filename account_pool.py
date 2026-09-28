"""账号池：provider 内多账号 / 多 Key 的调度、AIMD 限速、冷却与统计。

借鉴自 st-rotator（github.com/Phoeky/st-rotator）的精华，适配 TokenEngine 的多厂商场景：

* **账号层级状态聚合** —— 同账号下的多把 Key 共享配额，限流要按账号聚合，
  否则会出现"一个 Key 429 → 切下一个 Key → 又 429"的级联雪崩，白打一堆 429。
* **AIMD 自适应限速** —— 商汤这类"隐藏动态限流"的平台，人工试出的固定速率会在
  服务端策略变化后失效。让限速器自己找平衡点：撞 429 乘性降速、干净窗口加性提速。
  主动把请求摊平，比撞了 429 再退避划算（429 本身也消耗配额和时间）。
* **精细错误分类** —— 区分「暂时限流」（可换账号重试）与「凭据失效」（换账号也没用，
  应直接排除），避免在坏 Key 上空转。
* **单请求等待预算** —— 防止无限重试把单个请求挂几分钟，尾延迟爆炸。

与 st-rotator 的差异：st-rotator 是"单厂商多账号"，本模块是"多厂商路由里的
provider 内部账号池"——provider 之间仍由 router 按 priority 调度，provider 内部
才用本池做账号级调度。

向后兼容：provider 若没有 ``accounts`` 字段，本池会自动用单条 ``api_key`` 兜底，
行为与改造前一致。
"""

from __future__ import annotations

import hashlib
import random
import threading
import time
from collections import deque
from dataclasses import dataclass
from enum import Enum


class KeyStatus(str, Enum):
    """账号（Key）的可用性状态。"""

    HEALTHY = "healthy"    # 可用
    COOLDOWN = "cooldown"  # 临时冷却（429 / 5xx / 超时）
    INVALID = "invalid"    # 凭据失效（401/403），换账号才有救


def mask_key(key: str) -> str:
    """脱敏展示：日志里不出现完整 Key，但要能区分是哪一把。"""
    if not key:
        return "***"
    n = len(key)
    if n <= 6:
        return key[0] + "*" * (n - 1)
    if n <= 14:
        return f"{key[:2]}{'*' * (n - 4)}{key[-2:]}"
    return f"{key[:6]}...{key[-4:]}"


def key_id(key: str) -> str:
    """给一把 Key 生成稳定短标识（sha256 前 12 位），既不泄漏原文又能唯一定位。"""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]


class AdaptiveRateLimiter:
    """AIMD 自适应限速器：成功时缓慢提速，撞 429 时快速降速。

    思路直接借用 TCP 拥塞控制：

    * **撞到 429** → ``rate *= decrease``（乘性退让）
    * **持续 ``recovery_seconds`` 没有 429** → ``rate += increase_step``（加性试探）

    稳态会收敛到"刚好不出 429"的速率附近，并能自动跟随服务端策略漂移。

    调参口诀（st-rotator 实测教训）：**降速要温和，恢复要及时，下限别太低**。
    ``decrease`` 太狠 + ``recovery_seconds`` 太长会导致触底爬不回来，吞吐反而更差。
    """

    __slots__ = (
        "_disabled", "_min", "_max", "_rate", "_burst", "_decrease", "_step", "_recovery",
        "_clock", "_sleeper", "_lock", "_next", "_last_penalty", "_last_raise",
        "_penalties", "_raises", "_successes", "_history",
    )

    def __init__(
        self,
        rate: float = 0.0,
        *,
        burst: float = 1.0,
        min_rate: float = 0.15,
        max_rate: float = 2.0,
        decrease: float = 0.85,
        increase_step: float = 0.05,
        recovery_seconds: float = 8.0,
        clock=time.monotonic,
        sleeper=time.sleep,
    ) -> None:
        # rate <= 0 表示**不限速**（默认）。TokenEngine 是多厂商路由，多数源并不需要
        # 主动限速；只有像商汤这种"隐藏动态限流"的源才应显式配 rate_control.rate 开启。
        self._disabled = float(rate) <= 0
        self._min = 0.0 if self._disabled else max(float(min_rate), 1e-6)
        self._max = max(float(max_rate), self._min)
        self._rate = 0.0 if self._disabled else min(max(float(rate), self._min), self._max)
        self._burst = max(1.0, float(burst))
        self._decrease = min(max(float(decrease), 0.1), 0.99)
        self._step = max(float(increase_step), 0.0)
        self._recovery = max(float(recovery_seconds), 0.0)
        self._clock = clock
        self._sleeper = sleeper
        self._lock = threading.Lock()
        self._next = 0.0
        self._last_penalty = float("-inf")
        self._last_raise = float("-inf")
        self._penalties = 0
        self._raises = 0
        self._successes = 0
        self._history: list[tuple[float, float]] = []

    @property
    def rate(self) -> float:
        with self._lock:
            return self._rate

    @property
    def enabled(self) -> bool:
        return not self._disabled

    def reset(self) -> None:
        """管理台「冷却重置」：限速器回到初始速率，清空惩罚/回升历史。"""
        with self._lock:
            self._rate = self._max if not self._disabled else 0.0
            self._next = 0.0
            self._last_penalty = float("-inf")
            self._last_raise = float("-inf")
            self._penalties = 0
            self._raises = 0
            self._successes = 0
            self._history = []

    def acquire(self, timeout: float | None = None) -> float:
        """按当前速率阻塞放行，返回实际等待秒数。超预算抛 TimeoutError。"""
        if self._disabled:
            return 0.0
        with self._lock:
            interval = 1.0 / self._rate
            now = self._clock()
            earliest = now - (self._burst - 1.0) * interval
            base = max(earliest, self._next)
            wait = max(0.0, base - now)
            if timeout is not None and wait > timeout:
                raise TimeoutError(
                    f"自适应限速等待超时（需等 {wait:.2f}s，预算 {timeout:.2f}s）"
                )
            self._next = base + interval
        if wait > 0:
            self._sleeper(wait)
        return wait

    def refund(self) -> None:
        """请求在真正发出前中止时回滚时间片，避免队列漂移。"""
        if self._disabled:
            return
        with self._lock:
            interval = 1.0 / self._rate
            now = self._clock()
            earliest = now - (self._burst - 1.0) * interval
            self._next = max(earliest, self._next - interval)

    def on_success(self) -> None:
        """一次成功：若已"干净"足够久，小幅提速试探上限。"""
        if self._disabled:
            return
        with self._lock:
            self._successes += 1
            if self._rate >= self._max:
                return
            now = self._clock()
            if now - self._last_penalty < self._recovery:
                return  # 刚被限流过，先稳住
            if now - self._last_raise < self._recovery:
                return  # 提速别太频繁
            self._rate = min(self._max, self._rate + self._step)
            self._last_raise = now
            self._raises += 1
            self._record(now)

    def on_rate_limited(self) -> float:
        """一次 429：乘性降速，返回降速后的速率。"""
        if self._disabled:
            return 0.0
        with self._lock:
            self._penalties += 1
            now = self._clock()
            self._last_penalty = now
            self._rate = max(self._min, self._rate * self._decrease)
            self._record(now)
            return self._rate

    def _record(self, now: float) -> None:
        self._history.append((round(now, 3), round(self._rate, 4)))
        if len(self._history) > 200:
            del self._history[:100]

    def stats(self) -> dict:
        if self._disabled:
            return {"mode": "disabled", "rate": 0.0}
        with self._lock:
            return {
                "mode": "adaptive",
                "rate": round(self._rate, 3),
                "min_rate": self._min,
                "max_rate": self._max,
                "successes": self._successes,
                "penalties": self._penalties,
                "raises": self._raises,
            }


@dataclass
class KeyStats:
    """单个账号（Key）的累计统计。"""

    requests: int = 0
    successes: int = 0
    failures: int = 0
    rate_limited: int = 0
    server_errors: int = 0
    client_errors: int = 0
    total_latency: float = 0.0

    @property
    def avg_latency(self) -> float:
        return self.total_latency / self.successes if self.successes else 0.0

    @property
    def success_rate(self) -> float:
        return self.successes / self.requests if self.requests else 1.0

    def to_dict(self) -> dict:
        return {
            "requests": self.requests,
            "successes": self.successes,
            "failures": self.failures,
            "rate_limited": self.rate_limited,
            "success_rate": round(self.success_rate, 4),
            "avg_latency_ms": round(self.avg_latency * 1000, 1),
        }


class Account:
    """单个账号（一个 Key + 它的限速器与状态）。

    一个账号通常对应"一个独立注册的账号"；若某账号下有多把 Key，应拆成多个
    Account（因为配额共享，多 Key 无扩容效果，见交底文档坑 13）。
    """

    __slots__ = (
        "name", "api_key", "status", "stats", "limiter", "rpm_limit",
        "max_concurrency", "inflight", "cooldown_until", "invalid_until",
        "consecutive_failures", "last_error", "_window", "_lock",
    )

    def __init__(
        self,
        name: str,
        api_key: str,
        *,
        rpm_limit: int | None = None,
        max_concurrency: int = 4,
        rate: float = 0.0,
        min_rate: float = 0.15,
        max_rate: float = 2.0,
        decrease: float = 0.85,
        increase_step: float = 0.05,
        recovery_seconds: float = 8.0,
    ) -> None:
        self.name = name
        self.api_key = api_key
        self.status = KeyStatus.HEALTHY
        self.stats = KeyStats()
        self.rpm_limit = rpm_limit
        self.max_concurrency = max(1, int(max_concurrency))
        self.inflight = 0
        self.cooldown_until = 0.0
        self.invalid_until = 0.0
        self.consecutive_failures = 0
        self.last_error = ""
        self._window: deque[float] = deque()
        self._lock = threading.Lock()
        self.limiter = AdaptiveRateLimiter(
            rate,
            min_rate=min_rate,
            max_rate=max_rate,
            decrease=decrease,
            increase_step=increase_step,
            recovery_seconds=recovery_seconds,
        )

    # ---------------------------------------------------------------- 可用性

    def _prune(self, now: float) -> None:
        cutoff = now - 60.0
        w = self._window
        while w and w[0] < cutoff:
            w.popleft()

    def rpm_blocked_until(self, now: float) -> float:
        """本地 RPM 预限流：最近 60s 内请求数达上限则返回解禁时刻。"""
        if not self.rpm_limit:
            return 0.0
        self._prune(now)
        if len(self._window) < self.rpm_limit:
            return 0.0
        return self._window[len(self._window) - self.rpm_limit] + 60.0

    def available_at(self, now: float) -> float:
        return max(self.cooldown_until, self.invalid_until, self.rpm_blocked_until(now))

    def is_usable(self, now: float) -> bool:
        if self.status is KeyStatus.INVALID and now < self.invalid_until:
            return False
        with self._lock:
            if self.inflight >= self.max_concurrency:
                return False
        return self.available_at(now) <= now

    def remaining_cooldown(self, now: float) -> int:
        return max(0, int(self.available_at(now) - now + 0.999))

    # ---------------------------------------------------------------- 占用

    def try_acquire(self) -> bool:
        """尝试占用一个并发位。"""
        with self._lock:
            if self.inflight >= self.max_concurrency:
                return False
            self.inflight += 1
        self._window.append(time.monotonic())
        return True

    def release(self) -> None:
        with self._lock:
            if self.inflight > 0:
                self.inflight -= 1

    # ---------------------------------------------------------------- 反馈

    def on_success(self, latency: float = 0.0) -> None:
        self.stats.requests += 1
        self.stats.successes += 1
        self.stats.total_latency += latency
        self.consecutive_failures = 0
        self.last_error = ""
        if self.status is KeyStatus.COOLDOWN and self.available_at(time.monotonic()) <= time.monotonic():
            self.status = KeyStatus.HEALTHY
        self.limiter.on_success()

    def on_rate_limited(self, cooldown: float, *, reason: str = "rate limited") -> None:
        """429：账号级冷却（同账号所有 Key 共享配额）+ AIMD 降速。"""
        now = time.monotonic()
        self.stats.requests += 1
        self.stats.failures += 1
        self.stats.rate_limited += 1
        self.consecutive_failures += 1
        self.last_error = reason
        self.status = KeyStatus.COOLDOWN
        self.cooldown_until = max(self.cooldown_until, now + cooldown)
        self.limiter.on_rate_limited()

    def on_invalid(self, ttl: float, *, reason: str = "invalid key") -> None:
        """401/403：凭据失效，长时间排除。"""
        now = time.monotonic()
        self.stats.requests += 1
        self.stats.failures += 1
        self.stats.client_errors += 1
        self.last_error = reason
        self.status = KeyStatus.INVALID
        self.invalid_until = max(self.invalid_until, now + ttl)

    def on_server_error(self, cooldown: float, *, reason: str = "server error") -> None:
        """5xx / 超时 / 网络错：不是账号的错，只做短冷却、不累计退避。"""
        now = time.monotonic()
        self.stats.requests += 1
        self.stats.failures += 1
        self.stats.server_errors += 1
        self.last_error = reason
        self.cooldown_until = max(self.cooldown_until, now + cooldown)

    def reset(self) -> None:
        """管理台「冷却重置」：清空账号级冷却与连续失败计数，回到可用态。"""
        now = time.monotonic()
        self.cooldown_until = 0.0
        self.invalid_until = 0.0
        self.consecutive_failures = 0
        self.last_error = ""
        if self.status is not KeyStatus.HEALTHY:
            self.status = KeyStatus.HEALTHY
        self._window.clear()
        self.limiter.reset()

    def snapshot(self) -> dict:
        now = time.monotonic()
        return {
            "name": self.name,
            "key": mask_key(self.api_key),
            "key_id": key_id(self.api_key),
            "status": self.status.value,
            "inflight": self.inflight,
            "max_concurrency": self.max_concurrency,
            "cooldown_remaining": self.remaining_cooldown(now),
            "rate": round(self.limiter.rate, 3),
            "last_error": self.last_error[:120],
            **self.stats.to_dict(),
        }


class AccountPool:
    """一个 provider 的账号池：轮换调度、冷却等待、统计。

    provider 配置支持两种形态（向后兼容）：

    * 新式：``"accounts": [{"name": "账号1", "api_key": "sk-...", "rpm_limit": 2}, ...]``
    * 旧式：只有 ``"api_key": "sk-..."`` —— 自动包装成单账号池。

    调度策略：``round_robin``（默认）/ ``least_inflight`` / ``least_recent``。
    """

    def __init__(
        self,
        provider_name: str,
        accounts: list[dict] | None = None,
        *,
        single_key: str | None = None,
        strategy: str = "round_robin",
        cooldown_base: float = 60.0,
        cooldown_factor: float = 1.5,
        cooldown_max: float = 120.0,
        cooldown_jitter: float = 0.5,
        invalid_ttl: float = 600.0,
        server_error_cooldown: float = 15.0,
        default_rate: float = 0.0,
        min_rate: float = 0.15,
        max_rate: float = 2.0,
        decrease: float = 0.85,
        increase_step: float = 0.05,
        recovery_seconds: float = 8.0,
    ) -> None:
        self.provider = provider_name
        self.strategy = strategy
        self.cooldown_base = cooldown_base
        self.cooldown_factor = cooldown_factor
        self.cooldown_max = cooldown_max
        self.cooldown_jitter = cooldown_jitter
        self.invalid_ttl = invalid_ttl
        self.server_error_cooldown = server_error_cooldown
        self._cond = threading.Condition()
        self._rr = 0

        specs: list[dict] = []
        if accounts:
            specs = list(accounts)
        elif single_key:
            specs = [{"name": f"{provider_name}#1", "api_key": single_key}]

        self.accounts: list[Account] = []
        for i, spec in enumerate(specs):
            key = spec.get("api_key") or ""
            if not key:
                continue
            self.accounts.append(
                Account(
                    spec.get("name") or f"{provider_name}#{i + 1}",
                    key,
                    rpm_limit=spec.get("rpm_limit"),
                    max_concurrency=int(spec.get("max_concurrency", 4)),
                    rate=float(spec.get("rate", default_rate)),
                    min_rate=float(spec.get("min_rate", min_rate)),
                    max_rate=float(spec.get("max_rate", max_rate)),
                    decrease=float(spec.get("decrease", decrease)),
                    increase_step=float(spec.get("increase_step", increase_step)),
                    recovery_seconds=float(spec.get("recovery_seconds", recovery_seconds)),
                )
            )

    @property
    def enabled(self) -> bool:
        return bool(self.accounts)

    @property
    def size(self) -> int:
        return len(self.accounts)

    # ---------------------------------------------------------------- 调度

    def _order(self, now: float) -> list[Account]:
        """按策略给出候选顺序（已过滤不可用的）。"""
        usable = [a for a in self.accounts if a.is_usable(now)]
        if self.strategy == "least_inflight":
            usable.sort(key=lambda a: (a.inflight, a.stats.requests))
        elif self.strategy == "least_recent":
            usable.sort(key=lambda a: a.stats.requests)
        else:  # round_robin
            if usable:
                usable = usable[self._rr % len(usable):] + usable[: self._rr % len(usable)]
        return usable

    def acquire(self, timeout: float = 30.0, *, exclude: set[str] | None = None) -> Account | None:
        """取一个可用账号（已占用并发位）。失败返回 None。

        Args:
            timeout: 最长等待秒数（等不到就返回 None，由调用方决定切源还是放弃）。
            exclude: 本次请求已试过、不要再选的账号名集合。
        """
        exclude = exclude or set()
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            now = time.monotonic()
            for a in self._order(now):
                if a.name in exclude:
                    continue
                # AIMD 主动限速：按当前速率排队，把请求摊平（超预算则跳过该账号）
                budget = max(0.0, deadline - now)
                try:
                    a.limiter.acquire(timeout=budget if budget > 0 else 0.01)
                except TimeoutError:
                    continue
                if a.try_acquire():
                    with self._cond:
                        self._rr += 1
                    return a
                a.limiter.refund()

            if time.monotonic() >= deadline:
                return None
            # 所有账号都在冷却/限速中：等最近一个解禁
            waits = [
                a.remaining_cooldown(now)
                for a in self.accounts
                if a.name not in exclude
            ]
            nap = min([w for w in waits if w > 0] or [1])
            with self._cond:
                self._cond.wait(timeout=min(nap, max(0.05, deadline - time.monotonic())))

    def release(self, account: Account) -> None:
        account.release()
        with self._cond:
            self._cond.notify_all()

    def notify(self) -> None:
        """状态变化后唤醒等待者（如冷却提前解除）。"""
        with self._cond:
            self._cond.notify_all()

    # ---------------------------------------------------------------- 冷却计算

    def cooldown_for(self, account: Account) -> float:
        """429 的指数退避冷却时长（带抖动防惊群）。"""
        n = max(1, account.consecutive_failures)
        base = self.cooldown_base * (self.cooldown_factor ** (n - 1))
        base = min(base, self.cooldown_max)
        if self.cooldown_jitter:
            base += random.uniform(0, self.cooldown_jitter * base)
        return base

    # ---------------------------------------------------------------- 观测

    def all_cooling(self, now: float | None = None) -> bool:
        now = now or time.monotonic()
        return bool(self.accounts) and not any(a.is_usable(now) for a in self.accounts)

    def min_cooldown(self, now: float | None = None) -> int:
        now = now or time.monotonic()
        rems = [a.remaining_cooldown(now) for a in self.accounts]
        return min(rems) if rems else 0

    def snapshot(self) -> dict:
        return {
            "provider": self.provider,
            "strategy": self.strategy,
            "size": self.size,
            "accounts": [a.snapshot() for a in self.accounts],
        }


# ---------------------------------------------------------------------- 池注册表

_POOLS: dict[str, AccountPool] = {}
_POOLS_LOCK = threading.Lock()


def get_pool(provider_cfg: dict) -> AccountPool | None:
    """按 provider 配置取（或惰性建）账号池。

    配置里 ``accounts`` / ``api_key`` 变化时自动重建，便于热加载。
    """
    name = provider_cfg.get("name")
    if not name:
        return None
    accounts = provider_cfg.get("accounts")
    single = provider_cfg.get("api_key")
    if not accounts and not single:
        return None

    # 用配置指纹判断是否需要重建
    if accounts:
        fp = "|".join(f"{a.get('name')}:{key_id(a.get('api_key') or '')}" for a in accounts)
    else:
        fp = key_id(single or "")
    rc = provider_cfg.get("rate_control") or {}
    cd = provider_cfg.get("cooldown") or {}
    sig = (
        fp,
        provider_cfg.get("strategy", "round_robin"),
        rc.get("rate"), rc.get("min_rate"), rc.get("max_rate"),
        rc.get("decrease"), rc.get("increase_step"), rc.get("recovery_seconds"),
        cd.get("base"), cd.get("factor"), cd.get("max"), cd.get("jitter"),
        cd.get("invalid_ttl"), cd.get("server_error"),
    )

    with _POOLS_LOCK:
        pool = _POOLS.get(name)
        if pool is not None and getattr(pool, "_sig", None) == sig:
            return pool
        pool = AccountPool(
            name,
            accounts=accounts,
            single_key=single,
            strategy=provider_cfg.get("strategy", "round_robin"),
            cooldown_base=float(cd.get("base", 60)),
            cooldown_factor=float(cd.get("factor", 1.5)),
            cooldown_max=float(cd.get("max", 120)),
            cooldown_jitter=float(cd.get("jitter", 0.5)),
            invalid_ttl=float(cd.get("invalid_ttl", 600)),
            server_error_cooldown=float(cd.get("server_error", 15)),
            default_rate=float(rc.get("rate", 0)),
            min_rate=float(rc.get("min_rate", 0.15)),
            max_rate=float(rc.get("max_rate", 2.0)),
            decrease=float(rc.get("decrease", 0.85)),
            increase_step=float(rc.get("increase_step", 0.05)),
            recovery_seconds=float(rc.get("recovery_seconds", 8.0)),
        )
        pool._sig = sig
        _POOLS[name] = pool
        return pool


def pools_snapshot() -> dict:
    with _POOLS_LOCK:
        return {k: v.snapshot() for k, v in _POOLS.items()}


def drop_pool(name: str) -> None:
    """管理台：丢弃某个 provider 的账号池（下次请求按新配置重建）。"""
    with _POOLS_LOCK:
        _POOLS.pop(name, None)


def drop_all_pools() -> None:
    """管理台：丢弃全部账号池，强制按最新配置重建。"""
    with _POOLS_LOCK:
        _POOLS.clear()


def clear_cooldown(name: str) -> None:
    """管理台：重置某个 provider 账号池里所有账号的冷却与限速器。"""
    with _POOLS_LOCK:
        pool = _POOLS.get(name)
    if pool is None:
        return
    for acc in list(pool.accounts):
        try:
            acc.reset()
        except Exception:
            pass
