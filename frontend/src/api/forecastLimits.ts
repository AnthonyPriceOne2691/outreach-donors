/**
 * Смета прогона: во что упирается бюджет, до какого дня держится остаток ключа и хватит ли
 * денег на счету выдачи (предохранитель DataForSEO, 16.10.2026).
 *
 * Отдельным файлом, а не строками `Forecast` в `types.ts`: тот упёрся в предел длины.
 * Упор назван с 16.10.2026: смета советовала «поднимите кап», когда кончался ключ Ahrefs,
 * и кап поднимали зря.
 */

/** Во что упирается бюджет: лимит ключа или пространства Ahrefs, наш кап, потолок прогона. */
export type BudgetBinding = 'key' | 'workspace' | 'cap' | 'ceiling';

/** Бюджет сметы — общий у прогона по ключам и по ссылкам конкурентов: остаток у Ahrefs,
 *  кап цикла ключа, потолок прогона и упор; одна плашка «помещается / не хватает» на оба. */
export interface BudgetFigures {
  units_left: number;
  /** Кап цикла ключа; `null` — размер ключа неизвестен, см. `cap_unknown`. */
  units_cap: number | null;
  units_spent_in_cycle: number;
  cap_left: number;
  cycle_start: string;
  cycle_by_key: boolean;
  cap_unknown: string | null;
  next_reset: string;
  run_ceiling: number | null;
  budget: number;
  /** Помещается ли смета в бюджет. По этому полю блокируется кнопка. */
  affordable: boolean;
  shortfall: number;
  binding: BudgetBinding | null;
  short_of: BudgetBinding[];
}

export interface ForecastLimits {
  /** Следующее обнуление ключа: до какого дня держится остаток. */
  next_reset: string;
  /** Упор бюджета; кап неизвестен — `null`. */
  binding: BudgetBinding | null;
  /** Каких лимитов не хватает — каждого, жёсткие первыми; помещается — пусто. */
  short_of: BudgetBinding[];
  /** Остаток на счету выдачи (DataForSEO); `null` — счёта нет или он не ответил. */
  serp_balance_usd: number | null;
  /** «Пополните счёт» словами: блокирует запуск, как нехватка юнитов; `null` — купить можно. */
  serp_refusal: string | null;
}
