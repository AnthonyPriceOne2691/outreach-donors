/**
 * Окно списка диалогов: рисуются первые сто строк и «ещё сто» по кнопке.
 *
 * Список приходит целиком (`useThreadList`), и до 11.11.2026 рисовался целиком: сотни и тысячи
 * диалогов разом — на телефоне лента в полтора десятка экранов (разведка экранов 10.11.2026).
 * Режется отрисовка, а не данные: счётчики фильтра, «найдено N из M», J и K, «Следующий ждущий»
 * считают весь список. Страниц нет: ждущие человека стоят сверху, а остальное здесь ищут поиском
 * и фильтром состояния, а не номером страницы.
 *
 * **Открытый диалог виден всегда.** Его строка дальше показанного — окно растёт до неё, до
 * целой сотни: ссылка на диалог из середины списка не теряет выделенную строку, J и K идут и за
 * край показанного.
 *
 * **Смена фильтра, поиска и части — счёт с начала:** это другой список.
 *
 * **Возврат к списку — к тем же строкам** (`components/ScrollMemory`). На узком окне переписка —
 * отдельная страница, и вернувшемуся из неё список рисуется заново: с первой сотней прокрутка
 * не дотянулась бы до строки, из которой уходили. Показанное помнится на вкладку браузера, пока
 * список тот же; хранилище закрыто — первые сто, как без памяти.
 */

import { useEffect, useState } from 'react';

import type { ThreadFilters } from './threadFilters';

/** Строк за раз: первая сотня и каждая следующая. */
export const LIST_STEP = 100;

const STORE = 'outreach.threads.shown';

/** Сколько строк показано и у какого списка. */
interface Asked {
  scope: string;
  count: number;
}

/** Какой это список: часть, «Кому писали», состояние и поиск. Другой список — счёт с начала. */
export function listScope(filters: ThreadFilters): string {
  return JSON.stringify([filters.part, filters.role, filters.state, filters.search]);
}

/**
 * Сколько строк рисовать из `total`: показанное по кнопке, но не меньше, чем до открытого
 * диалога (`selectedAt` — его место в списке, −1 — открытого в списке нет) — до целой сотни.
 */
export function windowSize(asked: number, selectedAt: number, total: number): number {
  const toSelected = Math.ceil((selectedAt + 1) / LIST_STEP) * LIST_STEP;
  return Math.min(total, Math.max(asked, toSelected, LIST_STEP));
}

function remembered(scope: string): number {
  try {
    const kept = JSON.parse(sessionStorage.getItem(STORE) ?? 'null') as Partial<Asked> | null;
    const count = kept?.scope === scope ? kept.count : undefined;
    return typeof count === 'number' && Number.isInteger(count) && count > LIST_STEP
      ? count
      : LIST_STEP;
  } catch {
    // Хранилище закрыто или в нём негодное — первые сто.
    return LIST_STEP;
  }
}

function remember(asked: Asked): void {
  try {
    sessionStorage.setItem(STORE, JSON.stringify(asked));
  } catch {
    // Хранилище закрыто — вернувшемуся к списку рисуются первые сто.
  }
}

export interface ListWindow {
  /** Сколько строк рисовать. */
  count: number;
  /** Сколько осталось за окном. */
  rest: number;
  /** Показать ещё сотню. */
  more: () => void;
}

export function useListWindow(scope: string, selectedAt: number, total: number): ListWindow {
  const [asked, setAsked] = useState<Asked>(() => ({ scope, count: remembered(scope) }));
  // Счёт другого списка этому не годится: сменили фильтр — снова первая сотня. И прежний счёт
  // забыт: вернулись к прежнему фильтру — тоже первая сотня, а не то, что было показано до него.
  const own = asked.scope === scope ? asked.count : LIST_STEP;
  if (asked.scope !== scope) setAsked({ scope, count: LIST_STEP });
  const count = windowSize(own, selectedAt, total);
  useEffect(() => {
    remember({ scope, count: own });
  }, [scope, own]);
  return {
    count,
    rest: total - count,
    more: () => setAsked({ scope, count: count + LIST_STEP }),
  };
}
