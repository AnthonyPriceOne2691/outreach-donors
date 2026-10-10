/**
 * Меню свёрнуто до значков — по кнопке в шапке (замечание Anthony 10.10.2026: «чтобы
 * боковое меню могло сворачиваться по кнопочке, вместо надписей — значки»).
 *
 * Выбор помнит браузер (`localStorage`): это удобство одного человека, а не настройка
 * учётки. Хранилища нет — меню развёрнуто, как раньше. На телефоне меню и так
 * выезжает по кнопке, и всегда с надписями.
 */

import { useCallback, useState } from 'react';

const KEY = 'outreach.nav.folded';

/**
 * Ширина свёрнутой колонки — такая, что значок стоит там же, где в развёрнутой:
 * поле колонки 12, кромка рамки 1 и её поле 10, кромка пункта 1 и его поле 12, значок
 * 20 — и столько же справа. Значки при сворачивании не сдвигаются, уходят только
 * подписи.
 */
export const FOLDED_WIDTH = 2 * (12 + 1 + 10 + 1 + 12) + 20;

function read(): boolean {
  try {
    return localStorage.getItem(KEY) === '1';
  } catch {
    return false;
  }
}

function write(folded: boolean): void {
  try {
    if (folded) localStorage.setItem(KEY, '1');
    else localStorage.removeItem(KEY);
  } catch {
    // Хранилище закрыто — без памяти: следующий заход начнётся с развёрнутого меню.
  }
}

export function useNavFold(): [boolean, () => void] {
  const [folded, setFolded] = useState(read);
  const toggle = useCallback(
    () =>
      setFolded((was) => {
        write(!was);
        return !was;
      }),
    [],
  );
  return [folded, toggle];
}
