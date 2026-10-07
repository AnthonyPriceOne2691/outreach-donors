"""Тревога сторожа — одна на все правила: общего сторожа тишины (`silence.py`) и сторожа
почты этапов (`mail_watch.py`). Своим модулем: правила почты не тянут за собой общие."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Alarm:
    """Одна тревога: что молчит, с каких пор и что это значит."""

    code: str
    title: str
    detail: str
