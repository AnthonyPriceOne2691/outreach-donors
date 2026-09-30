# Verify report

**Date:** YYYY-MM-DD
**Verifier:** <process:ci|agent:NAME|human:NAME>
**asserts_reviewed_by:** <human:NAME at=… | n/a (все утверждения ведут к одобренным примерам)>
<!-- Обязательно для M/L (§3.1d уровень 3, DoD §3.2.7). Порядок такой:
     1) `bash scripts/lint/assert_digest.sh >> этот файл` — дайджест обязан быть
        вставлен, в нём есть строка `asserts_without_example: N`;
     2) N = 0 → все ожидания ведут к примерам, подписанным человеком ДО кода;
        законно писать `n/a (…)`, повторная подпись ничего не добавляет;
     3) N > 0 → читать нужно только строки с `-` в первой колонке: эти ожидания
        не подписывал никто. Подпись `human:NAME at=…` обязательна.
     `n/a` без вставленного дайджеста или при N > 0 — ошибка delivery_check. -->
**CI run:** <URL зелёного прогона>
<!-- URL, а не слова «CI зелёный»: без ссылки DoD §3.2.3 не закрыт.
     Взять: gh run list --workflow quality --branch <ветка> --limit 1 --json url -->
**Commit:** <sha, на котором прогон зелёный>
<!-- Заполнить ОДНИМ значением. Должен отличаться от `builder:` в STATUS (§5.2);
     на M/L совпадение = ошибка delivery_check. -->

## Shape oracles
- [ ] PASS/FAIL — (pre-commit / CQG / tsc / …)

## Behavior oracles
- [ ] PASS/FAIL — tests …

## Product oracles
- [ ] PASS/FAIL — delivery/evals/smoke
- [ ] PASS/FAIL — active/eval-smoke acceptance

## Spec coverage gaps
- …

## Verdict
- [ ] READY FOR HANDOFF
- [ ] NEED CONVERGE (new tasks)
- [ ] BLOCKED
