/**
 * Память экрана в браузере: номер последней задачи, выбранный этап.
 *
 * Хранилище бывает недоступно (приватное окно, запрет сайта) — тогда помним
 * до перезагрузки и молчим: это удобство, а не данные, и экран обязан
 * работать без него.
 */

export function remembered(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

export function remember(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // Хранилище недоступно (приватное окно) — помним до перезагрузки.
  }
}

/** Забыть: задача кончилась, и её итог после перезагрузки уже не нужен на экране. */
export function forget(key: string): void {
  try {
    window.localStorage.removeItem(key);
  } catch {
    // Хранилище недоступно — забывать нечего: помнили только до перезагрузки.
  }
}
