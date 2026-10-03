# Eval smoke — this shipment

Derived from spec acceptance. Run during verify. Поставка — часть 1.3c (3 из 3); пункты частей 1 и 2 — из #147 и 1.3b.

- [x] A1–A4, A6, A8 — `pytest tests/test_sales_intake.py` на базе дерева:
      каждое правило приёма — тест с точными значениями и текстами; A8 — лиды,
      домены пачками по одному, журнал; строк `contacts` нет.
- [x] Миграция журнала — `pytest tests/test_sales_model.py`: исполняется
      в процессе, значение `sales_leads_imported` — последнее в `auditaction`.
- [x] Новые тесты на старом коде — красные (verify-report).
- [x] Обратные прогоны и ручные мутанты ядра — красные (verify-report).
- [x] A2 и A6 через консоль, A5 и A9 — ссылка на Google-таблицу —
      `pytest tests/test_sales_intake_cli.py tests/test_sales_intake_sheet.py` (32):
      закрытая таблица при 200, 401 и 403, нет таблицы и листа, сеть/429/5xx, предел
      размера, байты с экрана; Google — только `MockTransport`.
- [x] A7, а также A5, A6 и A8 через API — `pytest tests/test_api_sales_intake.py` (13):
      предпросмотр 5 000 строк без записи, загрузка с журналом и без строк `contacts`,
      отказы 400/404/502 словами, 403 без права.
