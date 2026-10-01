# Eval smoke — this shipment

Derived from spec acceptance. Run during verify.

- [x] A1–A13 — `pytest tests/test_contour_waves.py`: каждый пример спеки — свой
      тест (id в строке `def`), с положительными контролями рядом с красными.
- [x] A1 на настоящем дереве ветки: `python scripts/contour_waves.py --base origin/main`
      — exit 0, «продаж в дереве нет — судить нечего».
- [x] A4, A2, A3 и A5 на настоящем дереве с подложенным
      `backend/features/sales/__init__.py` (файл удалён, не закоммичен) — числа
      в `verify-report.md`.
- [x] A13 на настоящем дереве: без строки `shared_changes:` в STATUS этого среза
      проверка красная и называет пять файлов общего кода.
- [x] Мета-гейт видит новый гейт: без `scripts/contour_waves.py`
      `check_gate_coverage.sh` красный («упомянут в конфиге, но файла нет»).
