# Eval smoke — this shipment

Derived from spec acceptance. Run during verify. Поставка — срез 1.4, часть 1.4a.

- [x] A1–A4 — `pytest tests/test_sales_geo.py` на базе дерева: таблица по форме (249 кодов,
      пояса в базе поясов), RU/EN/псевдонимы, столичный пояс с замечанием, колонка
      побеждает, непонятое не роняет строку, запись в базу — 29 тестов зелёные (в части 73).
- [x] Ожидания 1.3 — `pytest tests/test_sales_intake.py tests/test_sales_intake_cli.py`:
      страна кодом вместо пустой с замечанием — зелёные; полный набор 3456 passed.
- [x] Новые тесты на старом коде — красные: ImportError `geo` при сборе (verify-report).
- [x] Обратные прогоны правил страны и пояса — красные: 3 мутанта `geo.py`/`intake.py`
      убиты строителем на ветке среза (verify-report).
