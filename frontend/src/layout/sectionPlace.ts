/**
 * Куда ведёт пункт меню раздела, где список и запись — на одном экране: туда,
 * откуда из раздела ушли.
 *
 * Замечание Anthony 10.10.2026: открыл диалог, перешёл в соседний раздел, вернулся
 * в «Диалоги» — переписка закрыта, выбор потерян. Пункт меню вёл на голый
 * `/threads`. Теперь у разделов с пометкой `remember` (`Shell`) он ведёт на последний
 * адрес внутри раздела — с открытым диалогом и фильтром списка.
 *
 * Память — на вкладку браузера (`sessionStorage`): перезагрузка её не стирает, новая
 * вкладка начинает с начала раздела. Хранилище закрыто (приватное окно, запрет
 * сайта) — пункт ведёт в начало раздела, как раньше.
 */

import { useCallback, useEffect, useState } from 'react';
import { useLocation } from 'react-router-dom';

const KEY = 'outreach.nav.places';

type Places = Record<string, string>;

function read(): Places {
  try {
    const parsed: unknown = JSON.parse(sessionStorage.getItem(KEY) ?? '{}');
    if (typeof parsed !== 'object' || parsed === null) return {};
    return Object.fromEntries(
      Object.entries(parsed).filter(
        (entry): entry is [string, string] => typeof entry[1] === 'string',
      ),
    );
  } catch {
    return {};
  }
}

function write(places: Places): void {
  try {
    sessionStorage.setItem(KEY, JSON.stringify(places));
  } catch {
    // Хранилище закрыто — без памяти: пункт ведёт в начало раздела.
  }
}

/** Раздел адреса: путь раздела, с которого адрес начинается, — самый длинный из подходящих. */
export function sectionOf(pathname: string, paths: readonly string[]): string | null {
  let found: string | null = null;
  for (const path of paths) {
    const inside =
      path === '/' ? pathname === '/' : pathname === path || pathname.startsWith(`${path}/`);
    if (inside && (found === null || path.length > found.length)) found = path;
  }
  return found;
}

/** Последний адрес внутри каждого из `remembered` разделов; функция — куда вести пункт. */
export function useSectionPlaces(remembered: readonly string[]): (path: string) => string {
  const location = useLocation();
  const [places, setPlaces] = useState(read);
  const here = `${location.pathname}${location.search}`;
  const section = sectionOf(location.pathname, remembered);

  useEffect(() => {
    if (section === null) return;
    setPlaces((was) => {
      if (was[section] === here) return was;
      const next = { ...was, [section]: here };
      write(next);
      return next;
    });
  }, [section, here]);

  return useCallback((path: string) => places[path] ?? path, [places]);
}
