# Eval smoke — this shipment

Derived from spec acceptance. Run during verify.

- [x] A1 — `pytest tests/test_sales_boundary.py`: импорт продаж, подложенный
      в каждый из четырёх пакетов почты на копии дерева, краснит гейт слоёв;
      без импорта и без контракта тот же гейт зелёный.
- [x] A1 живьём — тот же импорт в самом `backend/features/letters/` →
      `bash scripts/lint/check_layers_gate.sh` красный, после возврата зелёный.
      Правило depcruise — так же: импорт экрана продаж в экран почты.
- [x] A2 — числа файлов `sales/` у ruff, mypy, `scripts/gates.py`,
      `lint-imports`, ратчета сложности, DRY и diff-coverage — все больше нуля,
      в verify-report.
- [x] A3 — `python scripts/contour_waves.py --base <база PR>`: exit 0,
      «В1 deployed», нарушений нет.
