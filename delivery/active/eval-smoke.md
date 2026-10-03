# Eval smoke — this shipment

Derived from spec acceptance. Run during verify. Поставка — срез 1.4, часть 1.4a.

- [ ] A1–A4 — `pytest tests/test_sales_geo.py` на базе дерева: таблица по форме (249 кодов,
      пояса в базе поясов), RU/EN/псевдонимы, столичный пояс с замечанием, колонка
      побеждает, непонятое не роняет строку, запись в базу.
- [ ] Ожидания 1.3 — `pytest tests/test_sales_intake.py tests/test_sales_intake_cli.py`:
      страна кодом вместо пустой с замечанием.
- [ ] Новые тесты на старом коде — красные (verify-report).
- [ ] Обратные прогоны правил страны и пояса — красные (verify-report).
