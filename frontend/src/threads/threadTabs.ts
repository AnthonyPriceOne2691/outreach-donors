/**
 * Вкладки экрана диалогов — сами диалоги и ответы без письма — в адресе.
 *
 * **Ответы без письма живут рядом с диалогами, а не отдельным разделом.**
 * Это та же переписка, только приём не нашёл, к какому письму она относится:
 * ищут её там же, где ищут ответ донора, и оттуда же её пообещала команда
 * пробного письма («вкладка «Не привязаны» экрана «Диалоги»»).
 *
 * **Адрес вкладки — только её собственное** (`?tab=unbound&page=2`). Фильтры
 * диалогов на вкладке ответов ничего не значат и из адреса уходят, как у
 * «Отбора» уходит то, чего на вкладке не бывает; первая вкладка — без `tab`,
 * адрес тот же, что в меню. Незнакомое значение — первая вкладка, а не пустой
 * экран: адрес правят руками и присылают устаревшим.
 *
 * **Часть списка (`?stage=sales`) вкладки переживает** (11.10.2026): ответы без письма —
 * общие на всю почту, но человек, пришедший из «Диалогов продаж», остаётся в них — и в
 * меню подсвечен тот же пункт, и вкладка «Диалоги» возвращает в переписку продаж.
 */

import { keepPreviousData, useQuery } from '@tanstack/react-query';

import { listUnbound } from '../api/outreach';
import { PART_PARAM } from '../api/stages';
import type { Part } from '../api/stages';

export type ThreadTab = 'threads' | 'unbound';

export const THREAD_TABS: Record<ThreadTab, string> = {
  threads: 'Диалоги',
  unbound: 'Не привязаны',
};

export const THREAD_TAB_KEYS = Object.keys(THREAD_TABS) as ThreadTab[];

export function readThreadTab(params: URLSearchParams): ThreadTab {
  return params.get('tab') === 'unbound' ? 'unbound' : 'threads';
}

export function writeThreadTab(tab: ThreadTab, part: Part | null = null): URLSearchParams {
  const params = new URLSearchParams();
  if (part !== null) params.set(PART_PARAM, part);
  if (tab === 'unbound') params.set('tab', tab);
  return params;
}

/**
 * Где человек был на вкладке «Диалоги»: открытая переписка и фильтр списка. Вкладка
 * «Диалоги» возвращает туда же, а не в начало списка — проверка QA 10.10.2026: ушёл на
 * «Не привязаны», вернулся — открытый диалог закрыт. Тот же приём, что у пунктов меню
 * (`layout/sectionPlace.ts`): память на вкладку браузера, хранилище закрыто — начало
 * списка, как раньше.
 */
const PLACE_KEY = 'outreach.threads.place';

/** Место — у каждой части своё: из ответов без письма «Диалоги продаж» возвращают в
 *  переписку продаж, а не в последний диалог донора. */
const placeKey = (part: Part | null) => (part === null ? PLACE_KEY : `${PLACE_KEY}.${part}`);

/** Начало списка части: «Диалоги» — `/threads`, «Диалоги продаж» — `/threads?stage=sales`. */
const startOf = (part: Part | null) =>
  part === null ? '/threads' : `/threads?${PART_PARAM}=${part}`;

/** Записать место части; `null` — стереть: часть начнёт с начала списка. */
export function rememberThreadsPlace(place: string | null, part: Part | null = null): void {
  try {
    if (place === null) sessionStorage.removeItem(placeKey(part));
    else sessionStorage.setItem(placeKey(part), place);
  } catch {
    // Хранилище закрыто — вкладка «Диалоги» ведёт в начало списка.
  }
}

/** Место части как записано — без подстановки начала списка; `null` — не записано. */
export function storedThreadsPlace(part: Part | null = null): string | null {
  try {
    return sessionStorage.getItem(placeKey(part));
  } catch {
    return null;
  }
}

export function threadsPlace(part: Part | null = null): string {
  try {
    const place = sessionStorage.getItem(placeKey(part));
    // Только адрес этого раздела: чужое значение в хранилище из «Диалогов» не уводит.
    return place !== null && /^\/threads(?:[/?]|$)/.test(place) ? place : startOf(part);
  } catch {
    return startOf(part);
  }
}

/** Ключ кэша ответов без письма — общий для счётчика во вкладке и для самой
 *  вкладки: первая страница, спрошенная ради числа, открывает вкладку сразу. */
export const UNBOUND_QUERY_KEY = ['unbound'] as const;

export function useUnboundPage(page: number) {
  return useQuery({
    queryKey: [...UNBOUND_QUERY_KEY, page],
    queryFn: () => listUnbound(page),
    // Пока едет следующая страница, стоит прежняя: пустой список на долю
    // секунды читался бы как «ответов нет».
    placeholderData: keepPreviousData,
  });
}
