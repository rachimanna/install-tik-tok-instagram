"""Ограничение числа задач на пользователя."""

from __future__ import annotations

from collections import defaultdict


class UserLimiter:
    def __init__(self, per_user: int) -> None:
        self.per_user = per_user
        self._active: defaultdict[int, int] = defaultdict(int)

    def try_acquire(self, user_id: int) -> bool:
        if self._active[user_id] >= self.per_user:
            return False
        self._active[user_id] += 1
        return True

    def release(self, user_id: int) -> None:
        if self._active.get(user_id, 0) <= 1:
            self._active.pop(user_id, None)
        else:
            self._active[user_id] -= 1

    def active(self, user_id: int) -> int:
        return self._active.get(user_id, 0)
