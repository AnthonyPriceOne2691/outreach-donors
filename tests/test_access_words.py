"""Отказ в праве называет право словами — теми же, что окно прав учётки.

Проверка прода 10.10.2026: снятое право в открытом сеансе давало «Действие «sales»
недоступно этой учётке» — код права латиницей, хотя в окне прав то же право подписано
словами. Отказ пишет сервер, экран его не переводит, поэтому у сервера своя копия слов
(`access/permissions.py`), а здесь — сверка копий построчно, как у слов о доноре
(`test_donor_wording.py`): две копии расходятся молча.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from backend.features.access.permissions import (
    PERMISSION_TITLES,
    AccessDeniedError,
    Actor,
    no_right,
    require,
)
from backend.features.core.domain import Permission, UserRole

LABELS_TS = Path(__file__).resolve().parent.parent / "frontend" / "src" / "api" / "labels.ts"

#: Кто не может ничего: учётка отключена — отказ по любому праву.
NOBODY = Actor(user_id=1, role=UserRole.ADMIN, is_active=False)


def _screen_titles() -> dict[str, str]:
    """`код: 'слова'` из `PERMISSION_TITLES` экрана."""
    source = LABELS_TS.read_text(encoding="utf-8")
    found = re.search(r"const PERMISSION_TITLES\b[^=]*=\s*\{(.*?)\n\};", source, re.S)
    assert found is not None, f"в {LABELS_TS.name} нет PERMISSION_TITLES"
    return dict(re.findall(r"^\s*([a-z_]+): '([^']+)',", found.group(1), re.M))


class TestSameWordsAsTheScreen:
    def test_titles_match_the_permissions_window(self) -> None:
        assert _screen_titles() == {
            permission.value: title for permission, title in PERMISSION_TITLES.items()
        }

    def test_every_permission_has_words(self) -> None:
        """Право без слов ушло бы в отказ кодом — ровно то, что чинили."""
        assert set(PERMISSION_TITLES) == set(Permission)


class TestRefusalSaysTheRight:
    @pytest.mark.parametrize("permission", list(Permission), ids=lambda p: p.value)
    def test_refusal_names_the_right_in_words(self, permission: Permission) -> None:
        with pytest.raises(AccessDeniedError) as refused:
            require(NOBODY, permission)

        said = str(refused.value)
        assert said == no_right(permission)
        assert f"«{PERMISSION_TITLES[permission]}»" in said
        assert f"«{permission.value}»" not in said
        # Отказ говорит, что делать: права выдаёт админ.
        assert "админ" in said

    def test_sales_right_reads_like_the_window(self) -> None:
        """Пример из находки — словами окна прав, без кода."""
        operator = Actor(user_id=2, role=UserRole.OPERATOR, overrides={"sales": False})

        with pytest.raises(AccessDeniedError) as refused:
            require(operator, Permission.SALES)

        assert "sales" not in str(refused.value)
        assert f"«{PERMISSION_TITLES[Permission.SALES]}»" in str(refused.value)

    def test_refusal_carries_its_mark(self) -> None:
        """Признак отказа — машинный, рядом с текстом: по нему экран перечитывает «кто я»."""
        with pytest.raises(AccessDeniedError) as refused:
            require(NOBODY, Permission.VIEW)

        assert refused.value.refusal == "permission"
