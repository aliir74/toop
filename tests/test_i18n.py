from __future__ import annotations

import pytest

from toop.config import settings
from toop.i18n import MESSAGES, indicator_label, score_word, t


def test_catalog_parity() -> None:
    """Every key must exist in both languages — guards half-translated keys."""
    assert set(MESSAGES["fa"]) == set(MESSAGES["en"])


def test_t_explicit_language() -> None:
    assert t("admin.reject", "en") == "Sorry, this command is admin-only."
    assert t("admin.reject", "fa") == "متاسفم، این دستور فقط برای ادمین است."


def test_t_reads_settings_when_lang_omitted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "BOT_LANG", "fa")
    assert t("admin.reject") == "متاسفم، این دستور فقط برای ادمین است."
    monkeypatch.setattr(settings, "BOT_LANG", "en")
    assert t("admin.reject") == "Sorry, this command is admin-only."


def test_unknown_language_falls_back_to_fa() -> None:
    assert t("admin.reject", "de") == t("admin.reject", "fa")


def test_missing_key_falls_back_to_other_language() -> None:
    """A key present in only one language resolves via the fallback chain."""
    MESSAGES["en"]["test.only_fa"] = MESSAGES["en"].get("test.only_fa", "")
    MESSAGES["fa"]["test.only_fa"] = "فقط فارسی"
    del MESSAGES["en"]["test.only_fa"]
    try:
        assert t("test.only_fa", "en") == "فقط فارسی"
    finally:
        del MESSAGES["fa"]["test.only_fa"]


def test_missing_in_all_raises() -> None:
    with pytest.raises(KeyError):
        t("does.not.exist", "en")


def test_kwargs_interpolated() -> None:
    msg = t("sessions.opened", "en", sid=3, date="2026-06-08")
    assert msg == "Session #3 opened for 2026-06-08."


def test_indicator_label() -> None:
    assert indicator_label("block", "en") == "Block"
    assert indicator_label("block", "fa") == "دفاع روی تور"
    assert indicator_label("unknown", "en") == "unknown"


def test_score_word() -> None:
    assert score_word(5, "fa") == "عالی"
    assert score_word(1, "en") == "Very weak"
