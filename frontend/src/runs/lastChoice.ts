/**
 * Последний выбор формы прогона — страна и глубина выдачи — в этом браузере
 * (аудит экранов 09.10.2026, второй круг, «меньше действий»).
 *
 * Их меняют редко, а выбирали заново на каждом прогоне: форма открывалась на США
 * и 10 результатах. Разведка: умолчание должно быть самым частым выбором, а
 * настройки меняют меньше 5 % людей (NN/g, «The Power of Defaults»), — самый
 * частый выбор человека и есть его прошлый.
 *
 * **Только удобство одного браузера**: хранилища нет (приватное окно, запрет
 * сайта) или в нём чужое — форма открывается с прежними умолчаниями.
 */

import { useCallback, useState } from 'react';

import { DEPTHS } from './depth';
import type { Depth } from './depth';

const KEY = 'outreach.run.last-choice';

type Choice = Partial<Record<'country' | 'depth', unknown>>;

function read(): Choice {
  try {
    const parsed: unknown = JSON.parse(localStorage.getItem(KEY) ?? '{}');
    return typeof parsed === 'object' && parsed !== null ? parsed : {};
  } catch {
    return {};
  }
}

function write(choice: Choice): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(choice));
  } catch {
    // Хранилище закрыто — выбор не запомнится, форма работает как прежде.
  }
}

/** Код страны выдачи: две латинские буквы, как в списке сервера. */
export function isCountry(value: unknown): value is string {
  return typeof value === 'string' && /^[a-z]{2}$/.test(value);
}

export function isDepth(value: unknown): value is Depth {
  return DEPTHS.some((depth) => depth === value);
}

/** Поле формы прогона, которое помнит прошлый выбор; негодное из хранилища — умолчание. */
export function useLastChoice<T>(
  name: keyof Choice,
  fallback: T,
  accept: (value: unknown) => value is T,
): [T, (value: T) => void] {
  const [value, setValue] = useState<T>(() => {
    const stored = read()[name];
    return accept(stored) ? stored : fallback;
  });
  const choose = useCallback(
    (next: T) => {
      setValue(next);
      write({ ...read(), [name]: next });
    },
    [name],
  );
  return [value, choose];
}
