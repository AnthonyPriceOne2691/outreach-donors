## Ревью рисковых мест

- деньги: маячок поднят словом «цена» в комментарии `RivalsLabel` (`frontend/src/runs/CompetitorRun.tsx`).
  Денежный путь не тронут: смета (`CompetitorEstimate`, `BudgetVerdict`) и условие запуска в
  `CompetitorFields` прежние — «Запустить» не нажимается без свежей сметы, и цена в юнитах стоит над
  кнопкой. В окошко `InfoHint` переехала только строка «Списки платятся юнитами Ahrefs — того же ключа,
  что метрики»; сама смета бесплатна (`estimate` в `backend/api/runs/competitors.py` списков и юнитов не
  тратит). Риска нет.
- безопасность: маячок поднят контекстом ханка — строкой `import { useSession } from '../auth/AuthProvider'`
  в `SelectionPage.tsx`, под которой добавлен импорт `InfoHint`. Права не тронуты: `mayDecide`
  (`can('prices')`) у «Отбора» и у разбора прогона прежние, `SelectionTable` и `RunReviewPage` решают по
  ним, как раньше; новых запросов, токенов и сессий в диффе нет. Риска нет.
