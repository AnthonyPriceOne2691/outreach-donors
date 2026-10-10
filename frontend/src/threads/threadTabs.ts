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
 */

import { keepPreviousData, useQuery } from '@tanstack/react-query';

import { listUnbound } from '../api/outreach';

export type ThreadTab = 'threads' | 'unbound';

export const THREAD_TABS: Record<ThreadTab, string> = {
  threads: 'Диалоги',
  unbound: 'Не привязаны',
};

export const THREAD_TAB_KEYS = Object.keys(THREAD_TABS) as ThreadTab[];

export function readThreadTab(params: URLSearchParams): ThreadTab {
  return params.get('tab') === 'unbound' ? 'unbound' : 'threads';
}

export function writeThreadTab(tab: ThreadTab): URLSearchParams {
  return tab === 'unbound' ? new URLSearchParams({ tab }) : new URLSearchParams();
}

/**
 * Где человек был на вкладке «Диалоги»: открытая переписка и фильтр списка. Вкладка
 * «Диалоги» возвращает туда же, а не в начало списка — проверка QA 10.10.2026: ушёл на
 * «Не привязаны», вернулся — открытый диалог закрыт. Тот же приём, что у пунктов меню
 * (`layout/sectionPlace.ts`): память на вкладку браузера, хранилище закрыто — начало
 * списка, как раньше.
 */
const PLACE_KEY = 'outreach.threads.place';

export function rememberThreadsPlace(place: string): void {
  try {
    sessionStorage.setItem(PLACE_KEY, place);
  } catch {
    // Хранилище закрыто — вкладка «Диалоги» ведёт в начало списка.
  }
}

export function threadsPlace(): string {
  try {
    const place = sessionStorage.getItem(PLACE_KEY);
    // Только адрес этого раздела: чужое значение в хранилище из «Диалогов» не уводит.
    return place !== null && /^\/threads(?:[/?]|$)/.test(place) ? place : '/threads';
  } catch {
    return '/threads';
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
