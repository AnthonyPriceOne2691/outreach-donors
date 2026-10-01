# Eval smoke — this shipment

Derived from spec acceptance. Run during verify.

- [ ] A1, A4–A7 — `pytest tests/test_sales_model.py` на своей базе: каждый
      пример — свой тест (id в строке `def`), положительные контроли рядом
      с красными.
- [ ] A1 — `pytest tests/test_migrations_match_models.py`: модели и цепочка
      сходятся.
- [ ] A6, обратный прогон — тот же сценарий при `CASCADE` и `RESTRICT`
      краснеет: лид пропадает, удаление у доноров падает.
- [ ] A8 — `pytest tests/test_sales_cli.py`: заведение гипотезы, повтор
      и пустое имя — отказ словами со своим кодом выхода.
- [ ] Новые тесты на старом коде (`origin/main`) — красные, числа
      в verify-report.
- [ ] Проверка волн на дереве ветки (`--base origin/main`): exit 0,
      «В1: триггер сработал, предел — следующий PR продаж».
