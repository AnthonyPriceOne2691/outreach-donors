# Eval smoke — this shipment

Derived from spec acceptance. Run during verify. Поставка — часть 1.3a (1 из 3).

- [ ] A1–A4, A6, A8 — `pytest tests/test_sales_intake.py` на базе дерева:
      каждое правило приёма — тест с точными значениями и текстами; A8 — лиды,
      домены пачками по одному, журнал; строк `contacts` нет.
- [ ] Миграция журнала — `pytest tests/test_sales_model.py`: исполняется
      в процессе, значение `sales_leads_imported` — последнее в `auditaction`.
- [ ] Новые тесты на старом коде — красные (verify-report).
- [ ] Обратные прогоны и ручные мутанты ядра — красные (verify-report).
- [ ] A2 и A6 через консоль, A5 и A9 — ссылка на Google-таблицу: часть 1.3b.
- [ ] A7, а также A5, A6 и A8 через API: часть 1.3c.
