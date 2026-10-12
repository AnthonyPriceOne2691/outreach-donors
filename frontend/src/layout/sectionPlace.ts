/**
 * Куда ведёт пункт меню раздела, где список и запись — на одном экране: туда,
 * откуда из раздела ушли.
 *
 * Замечание Anthony 10.10.2026: открыл диалог, перешёл в соседний раздел, вернулся
 * в «Диалоги» — переписка закрыта, выбор потерян. Пункт меню вёл на голый
 * `/threads`. Теперь у разделов с пометкой `remember` (`Shell`) он ведёт на последний
 * адрес внутри раздела — с открытым диалогом и фильтром списка.
 *
 * **Память — у каждого пункта своя** (11.10.2026): «Диалоги» и «Диалоги продаж» — один
 * путь с разной частью (`?stage=sales`), и место каждого помнится под ключом пункта
 * (`sections.sectionKey`). Иначе пункт доноров возвращал бы в переписку продаж.
 *
 * **Адрес, который экран поправил заменой, — не место прежнего пункта** (проверка
 * ветки 12.10.2026). Уведомление продаж ведёт на `/threads/42` без части: адрес сперва
 * достаётся «Диалогам», а переписка, узнав свой этап, заменяет его на
 * `/threads/42?stage=sales` (`threads/threadPart.ts`). Замена на том же экране, которая
 * уводит адрес к другому пункту, значит, что прежний пункт адрес получил по ошибке: его
 * место возвращается прежним. Так же и в зеркальном случае.
 *
 * Память — на вкладку браузера (`sessionStorage`): перезагрузка её не стирает, новая
 * вкладка начинает с начала раздела. Хранилище закрыто (приватное окно, запрет
 * сайта) — пункт ведёт в начало раздела, как раньше.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { NavigationType, useLocation, useNavigationType } from 'react-router-dom';

import { activeKey, sectionKey } from './sections';
import type { Section } from './sections';

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

/** Место пункта — или его нет (`undefined`): память начинает с начала раздела. */
function withPlace(places: Places, key: string, place: string | undefined): Places {
  if (places[key] === place) return places;
  const next = { ...places };
  if (place === undefined) delete next[key];
  else next[key] = place;
  return next;
}

/** Последняя запись: под каким пунктом, на каком экране и что там было до неё. */
interface Written {
  key: string;
  pathname: string;
  was: string | undefined;
}

/** Последний адрес внутри каждого из `remembered` пунктов; функция — куда вести пункт. */
export function useSectionPlaces(remembered: readonly Section[]): (section: Section) => string {
  const location = useLocation();
  const navigation = useNavigationType();
  const stored = useRef<Places | null>(null);
  stored.current ??= read();
  const [places, setPlaces] = useState(stored.current);
  const last = useRef<Written | null>(null);
  const here = `${location.pathname}${location.search}`;
  const key = activeKey(remembered, location.pathname, location.search);

  useEffect(() => {
    const now = stored.current ?? {};
    const before = last.current;
    // Замена адреса на том же экране увела его к другому пункту — прежний получил его по
    // ошибке: его место — как было до этой записи.
    const stray =
      before !== null &&
      navigation === NavigationType.Replace &&
      before.pathname === location.pathname &&
      before.key !== key;
    let next = stray ? withPlace(now, before.key, before.was) : now;
    last.current = key === null ? null : { key, pathname: location.pathname, was: next[key] };
    if (key !== null) next = withPlace(next, key, here);
    if (next === now) return;
    stored.current = next;
    write(next);
    setPlaces(next);
  }, [key, here, navigation, location.pathname]);

  return useCallback(
    (section: Section) => {
      const own = sectionKey(section);
      return section.remember === true ? (places[own] ?? own) : own;
    },
    [places],
  );
}
