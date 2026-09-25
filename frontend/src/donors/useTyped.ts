/**
 * Поле фильтра, которое печатают: своё значение сразу, в адрес — после паузы.
 *
 * Без паузы каждая буква — запрос к серверу и запись в адрес, и таблица
 * мигает на середине слова: «3» на пути к «30» — это отдельный фильтр.
 *
 * Направлений два, и оба нужны. Набранное уходит в адрес после паузы.
 * Адрес, сменившийся снаружи («назад», «Сбросить фильтры»), переписывает
 * поле — но только если значение и правда другое: пробел, набранный
 * в конце, не съедается.
 *
 * **Отправка узнаёт последнее значение адреса через ссылку, а не через
 * зависимость эффекта.** Иначе эффект срабатывал бы на каждую смену
 * адреса, и «назад» посреди паузы возвращало бы в адрес недонабранное.
 */

import { useDebouncedValue } from '@mantine/hooks';
import { useEffect, useRef, useState } from 'react';

/** Пауза после последнего знака. */
export const TYPING_PAUSE_MS = 300;

export function useTyped<T>(
  committed: T,
  commit: (value: T) => void,
  same: (draft: T, committed: T) => boolean,
): [T, (value: T) => void] {
  const [draft, setDraft] = useState(committed);
  const [typed] = useDebouncedValue(draft, TYPING_PAUSE_MS);
  const latest = useRef({ committed, commit, same });

  // Объявлен первым: эффекты идут по порядку, и отправка ниже видит
  // значение адреса с этой же отрисовки.
  useEffect(() => {
    latest.current = { committed, commit, same };
  });

  useEffect(() => {
    const now = latest.current;
    if (!now.same(typed, now.committed)) now.commit(typed);
  }, [typed]);

  useEffect(() => {
    setDraft((current) => (same(current, committed) ? current : committed));
  }, [committed, same]);

  return [draft, setDraft];
}
