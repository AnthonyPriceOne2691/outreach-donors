/**
 * Человек за экраном или нет — для простоя сеанса на сервере (аудит 20.11.2026, №6).
 *
 * Сервер закрывает сеанс после часа без действий человека (`access/sessions.py`), а узнаёт о
 * действиях по запросам. Но экран спрашивает сервер и сам: числа у меню — раз в минуту на любом
 * экране (`layout/work.ts`), тревоги, ход прогона. Без пометки открытая вкладка не простаивала
 * бы никогда. Поэтому запрос, перед которым человек минуту не касался ни мыши, ни клавиатуры,
 * уходит с заголовком фонового (`BACKGROUND_HEADER` в `api/client.ts`): сервер его проверяет,
 * но простой им не прерывает.
 *
 * Действие — нажатие, движение мыши, клавиша, колесо, касание, а ещё щелчок и ввод текста: их без
 * мыши и клавиш дают голосовое управление и диктовка. Прокрутка — нет: её делает и сам экран
 * (лента диалога, рама), и она держала бы сеанс без человека.
 */

/** Сколько тишины делает запрос фоновым: минута — шаг метки действия на сервере. */
export const AWAY_AFTER_MS = 60_000;

const INPUT_EVENTS = [
  'pointerdown',
  'pointermove',
  'keydown',
  'wheel',
  'touchstart',
  'click',
  'input',
] as const;

/** Когда человек последний раз что-то делал. Загрузка страницы — тоже его действие. */
let lastInput = Date.now();

/** Отметить действие человека — по умолчанию сейчас. */
export function noteInput(at: number = Date.now()): void {
  lastInput = at;
}

/** Человек последнюю минуту ничего не делал: запрос экран делает сам. */
export function personAway(now: number = Date.now()): boolean {
  return now - lastInput >= AWAY_AFTER_MS;
}

/** Слушать действия человека на странице. Возвращает «перестать». */
export function watchPresence(target: Window = window): () => void {
  const note = () => noteInput();
  const options = { capture: true, passive: true } as const;
  for (const name of INPUT_EVENTS) target.addEventListener(name, note, options);
  return () => {
    for (const name of INPUT_EVENTS) target.removeEventListener(name, note, options);
  };
}
