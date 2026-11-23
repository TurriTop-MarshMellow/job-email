"""按固定规则把一个岗位的事件推成一个当前状态。

状态只向前：已投递 → 笔试中 → 面试中 → 待结果 → 已 offer / 已结束。
「已 offer」和「已结束」是同级终态，到达之后不再接受新的状态。

事件对应的目标状态是固定的，不由模型决定：
- 投递确认 → 已投递
- 笔试 → 笔试中
- 面试邀请、面试链接 → 面试中（两个事件都保留，目标相同，所以第二次只保持）
- 面试反馈 → 待结果
- 通过/进下一轮 → 只在被采纳的那一条上前进一档，到「面试中」为止
  已投递 → 笔试中，笔试中 → 面试中，面试中及之后保持
- 未通过/感谢信 → 已结束
- offer → 已 offer

每条事件至多命中下面一条，命中后不再改状态：
1. 置信度低于阈值：不改状态，标「低置信度」，岗位记为待确认。
2. 达到阈值的通过类（通过/进下一轮、offer）和拒绝类同时存在：
   比较两类里各自最新的一条，日期更新的胜出，同一天再比置信度。
   落败的那一类全部标「冲突」，不参与推算。胜出的一类照常推算。
3. 同一类型里，只有日期最新的那条参与推算；更早的留在时间线里，标成
   「同类型以较新的为准」，不算异常。
4. 已经是终态：之后任何事件都不改状态，标「异常」。
5. 目标状态比当前更早：不改状态，标「异常」。
6. 目标更晚则推进；相同则保持。
"""

from __future__ import annotations

from job_email.models import Event

STATUS_CHAIN = ("已投递", "笔试中", "面试中", "待结果")
TERMINAL = frozenset({"已 offer", "已结束"})
RANK = {name: index for index, name in enumerate(STATUS_CHAIN)}
RANK["已 offer"] = len(STATUS_CHAIN)
RANK["已结束"] = len(STATUS_CHAIN)

TARGET = {
    "投递确认": "已投递",
    "笔试": "笔试中",
    "面试邀请": "面试中",
    "面试链接": "面试中",
    "面试反馈": "待结果",
    "未通过/感谢信": "已结束",
    "offer": "已 offer",
}

# 「进下一轮」没有写明下一档是什么。面试阶段里再进一轮，仍然是面试，不能把状态再推一次。
PASS_NEXT = {
    "已投递": "笔试中",
    "笔试中": "面试中",
    "面试中": "面试中",
    "待结果": "待结果",
}

POSITIVE = frozenset({"通过/进下一轮", "offer"})
NEGATIVE = frozenset({"未通过/感谢信"})
STATE_FLAGS = ("低置信度", "冲突", "异常")


def apply_state(events: list[Event], threshold: float = 0.6) -> tuple[str, bool]:
    """就地写入每条事件的 flags / effect，返回（当前状态, 是否待确认）。"""
    for event in events:
        event.flags = [flag for flag in event.flags if flag not in STATE_FLAGS]
        event.blocked = False
        event.applied_to_state = False
        event.effect = "未采纳"
    events.sort(key=lambda event: (event.occurred_at, event.seq))
    _mark_conflicts(events, threshold)
    authorities = _authorities(events, threshold)

    status = "已投递"
    needs_review = False
    for event in events:
        if event.confidence < threshold:
            _mark(event, "低置信度")
            event.effect = "低置信度"
            event.blocked = True
            needs_review = True
            continue
        if event.blocked:
            event.effect = "冲突"
            continue
        if id(event) not in authorities:
            event.effect = "同类型以较新的为准"
            continue
        if status in TERMINAL:
            _mark(event, "异常")
            event.effect = "异常"
            continue
        target = _target_for(event, status)
        if RANK[target] < RANK[status]:
            _mark(event, "异常")
            event.effect = "异常"
            continue
        if target != status:
            status = target
            event.effect = "推进"
        else:
            event.effect = "保持"
        event.applied_to_state = True
    return status, needs_review


def _authorities(events: list[Event], threshold: float) -> set[int]:
    """同一类型只让最新的一条进入推算。低置信度和冲突落败的不参与竞选。"""
    grouped: dict[str, list[Event]] = {}
    for event in events:
        if event.blocked or event.confidence < threshold:
            continue
        grouped.setdefault(event.event_type, []).append(event)
    chosen: set[int] = set()
    for group in grouped.values():
        latest = max(group, key=lambda event: (event.occurred_at, event.confidence, event.seq))
        chosen.add(id(latest))
    return chosen


def _mark_conflicts(events: list[Event], threshold: float) -> None:
    positive = [event for event in events if event.event_type in POSITIVE and event.confidence >= threshold]
    negative = [event for event in events if event.event_type in NEGATIVE and event.confidence >= threshold]
    if not positive or not negative:
        return
    best_positive = max(positive, key=_rank_key)
    best_negative = max(negative, key=_rank_key)
    losers = negative if _rank_key(best_positive) > _rank_key(best_negative) else positive
    if _rank_key(best_positive) == _rank_key(best_negative):
        losers = positive + negative
    for event in losers:
        _mark(event, "冲突")
        event.blocked = True


def _rank_key(event: Event) -> tuple[str, float, int]:
    return (event.occurred_at, event.confidence, event.seq)


def _target_for(event: Event, status: str) -> str:
    if event.event_type == "通过/进下一轮":
        return PASS_NEXT[status]
    return TARGET[event.event_type]


def _mark(event: Event, flag: str) -> None:
    if flag not in event.flags:
        event.flags.append(flag)
