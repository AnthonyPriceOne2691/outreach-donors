# Eval smoke — this shipment

Derived from spec acceptance. Run during verify. Поставка — срез 1.5, часть 1.5a.

- [ ] A2 (сервер) — фильтры `state` и `reason` отдают только отклонённых с причиной:
      `tests/test_api_sales_screen.py` на базе дерева.
- [ ] A4 (сервер) — без права `sales` — 403 словами: `tests/test_api_sales_screen.py`.
- [ ] Списки — гипотезы со счётчиками, лиды новейшими первыми, гипотеза и поиск, 20 на
      странице, незнакомый код ничего не находит, негодное состояние — 422.
- [ ] Новые тесты на старом коде — красные (verify-report).
