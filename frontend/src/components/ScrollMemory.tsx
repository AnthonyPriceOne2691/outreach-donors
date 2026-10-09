/**
 * Возврат к списку — на то же место, где был открытый пункт.
 *
 * Замечание Anthony 09.10.2026: «открываю что-то ниже по списку — донора, диалог, — а при
 * выходе обратно список опять сначала, а не с того места». Номер страницы и фильтры живут
 * в адресе и возвращались и раньше (`PageSwitch`, `state.from` у `BackLink`), прокрутка —
 * нет: `BrowserRouter` её не помнит (`ScrollRestoration` есть только у data-router), а
 * браузер ставит свою раньше, чем список дорисован, и попадает в начало.
 *
 * **Место помнится по адресу списка** — путь со строкой параметров (`/donors?page=2&…`):
 * возвращают и «назад» браузера, и «К списку» (`BackLink`), а «К списку» — новый переход,
 * не шаг истории. Хранится в `sessionStorage`: обновление страницы его не теряет, у другой
 * вкладки — своё.
 *
 * **Когда что.** «Назад» и «вперёд» браузера и «К списку» — на запомненное место: список
 * может дорисовываться по ответу сервера, и место ставится, как только страница до него
 * доросла (не дольше `RESTORE_MS`; человек начал листать сам — не мешаем). Переход на другой
 * экран — в начало: иначе карточка, открытая из низа списка, открывалась бы посередине.
 * Смена фильтров и страницы на том же экране прокрутку не трогает.
 */

import { useEffect, useLayoutEffect, useRef } from 'react';
import { NavigationType, useLocation, useNavigationType } from 'react-router-dom';

const STORE = 'scroll-places';

/** Сколько мест помнить: хватает на любую работу во вкладке, а хранилище не растёт без конца. */
const PLACES = 40;

/** Дольше ждать дорисовки списка незачем: за это время человек уже листает сам. */
export const RESTORE_MS = 3000;

/** Человек взялся за страницу сам — возврат места останавливается. */
const INPUT = ['wheel', 'touchstart', 'keydown', 'pointerdown'] as const;

type Places = Record<string, number>;

function readPlaces(): Places {
  try {
    const parsed: unknown = JSON.parse(sessionStorage.getItem(STORE) ?? '{}');
    return parsed !== null && typeof parsed === 'object' ? (parsed as Places) : {};
  } catch {
    return {};
  }
}

function remember(place: string, y: number): void {
  const places = readPlaces();
  // Свежее — в конец: старые места вытесняются первыми.
  delete places[place];
  places[place] = Math.round(y);
  try {
    sessionStorage.setItem(
      STORE,
      JSON.stringify(Object.fromEntries(Object.entries(places).slice(-PLACES))),
    );
  } catch {
    // Хранилище закрыто (приватный режим, квота) — место просто не запомнится.
  }
}

/** Поставить прокрутку на `y`, как только страница до неё доросла. Возвращает отмену. */
function restore(y: number): () => void {
  const started = performance.now();
  let frame = 0;
  let stopped = false;
  const stop = () => {
    stopped = true;
    cancelAnimationFrame(frame);
    for (const event of INPUT) window.removeEventListener(event, stop);
  };
  const step = () => {
    // Кадр мог уже стоять в очереди, когда человек взялся листать сам.
    if (stopped) return;
    const room = Math.max(document.documentElement.scrollHeight - window.innerHeight, 0);
    window.scrollTo(0, Math.min(y, room));
    if (room >= y || performance.now() - started > RESTORE_MS) stop();
    else frame = requestAnimationFrame(step);
  };
  for (const event of INPUT) window.addEventListener(event, stop, { passive: true });
  step();
  return stop;
}

/** Переход просит вернуть место: так его просит «К списку» (`BackLink`). */
export function wantsPlace(state: unknown): boolean {
  return state !== null && typeof state === 'object' && 'place' in state && state.place === true;
}

export function ScrollMemory() {
  const location = useLocation();
  const type = useNavigationType();
  const place = location.pathname + location.search;
  // Чьё место пишет прокрутка: меняется в момент перехода, до первой прокрутки нового экрана.
  const current = useRef(place);
  const path = useRef(location.pathname);

  useEffect(() => {
    // Свою прокрутку браузер ставил раньше, чем список дорисован, — и попадал в начало.
    window.history.scrollRestoration = 'manual';
    let frame = 0;
    const onScroll = () => {
      // Место и позиция — в момент прокрутки: к следующему кадру мог случиться переход,
      // и позиция нового экрана легла бы на место списка.
      const at = current.current;
      const y = window.scrollY;
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => remember(at, y));
    };
    window.addEventListener('scroll', onScroll, { passive: true });
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener('scroll', onScroll);
    };
  }, []);

  useLayoutEffect(() => {
    current.current = place;
    const moved = path.current !== location.pathname;
    path.current = location.pathname;
    if (type === NavigationType.Pop || wantsPlace(location.state)) {
      const y = readPlaces()[place];
      return y === undefined ? undefined : restore(y);
    }
    if (type === NavigationType.Push && moved) window.scrollTo(0, 0);
    return undefined;
  }, [location.key, location.pathname, location.state, place, type]);

  return null;
}
