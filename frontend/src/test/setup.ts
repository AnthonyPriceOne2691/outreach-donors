import '@testing-library/jest-dom/vitest';

import { notifications } from '@mantine/notifications';
import { configure } from '@testing-library/react';
import { afterEach, vi } from 'vitest';

import { misses } from './server';

// `findBy…` и `waitFor` ждали секунду — у полного прогона под нагрузкой (pre-push рядом со
// сборкой, два дерева разом) экран не успевал, и тест падал не по делу. Три секунды — с
// запасом, но меньше потолка теста (5 с): упавший тест по-прежнему говорит, чего не дождался.
configure({ asyncUtilTimeout: 3000 });

/**
 * jsdom не знает ссылок на объекты (`URL.createObjectURL`). Скачивание (`api/donors.saveFile`)
 * убирает ссылку таймером через секунду — под нагрузкой уже после теста, который подставлял
 * свои заглушки и снял их: «revokeObjectURL is not a function» вне теста роняло прогон целиком.
 * Ровные заглушки стоят всегда; тест подставляет свои и снимает — эти возвращаются после него.
 */
function objectUrls(): void {
  if (typeof URL.createObjectURL !== 'function') {
    Object.defineProperty(URL, 'createObjectURL', {
      configurable: true,
      writable: true,
      value: () => 'blob:test',
    });
  }
  if (typeof URL.revokeObjectURL !== 'function') {
    Object.defineProperty(URL, 'revokeObjectURL', {
      configurable: true,
      writable: true,
      value: () => undefined,
    });
  }
}
objectUrls();

// Хранилище и заглушка сети чистятся между тестами: иначе пропуск,
// оставленный одним тестом, пускает следующий, и порядок запуска
// начинает значить.
afterEach(() => {
  localStorage.clear();
  // Память разделов меню (`layout/sectionPlace.ts`) — на вкладку.
  sessionStorage.clear();
  vi.restoreAllMocks();
  // Хуки «после» идут в обратном порядке: этот — после хуков файла, снявших свои заглушки.
  objectUrls();
  // Уведомления Mantine живут в общем хранилище модуля: сверх пяти видимых
  // новые ждут в очереди, и уведомление теста, идущего после болтливых
  // соседей, не показывалось вовсе — тест падал от порядка запуска.
  notifications.clean();
  // Промах мимо записанных ответов роняет тест, даже если экран проглотил
  // его как «не загрузилось» (см. `misses` в `server.ts`).
  const missed = misses.splice(0);
  if (missed.length > 0) {
    throw new Error(`Тест ходил мимо записанных ответов:\n${missed.join('\n')}`);
  }
});

// jsdom не умеет того, чего ждёт Mantine. Заглушки минимальные —
// ровно чтобы компоненты монтировались.
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }),
});

class ResizeObserverStub {
  observe = () => {};
  unobserve = () => {};
  disconnect = () => {};
}
window.ResizeObserver = ResizeObserverStub;

// Холста в jsdom нет: без заглушки он печатает «Not implemented» на каждую
// отрисовку рамки (ширина колонки меряется холстом). «Нет холста» — честный
// ответ, у рамки на него запасная ширина.
HTMLCanvasElement.prototype.getContext = (() =>
  null) as typeof HTMLCanvasElement.prototype.getContext;

if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = () => {};
}

// `<wbr>` в браузере строчный, а у jsdom вычисленного `display` у него нет —
// и имя ссылки собиралось с пробелами на месте швов: «green-blog .example
// .test» вместо домена (06.10.2026, швы длинных имён — `components/Seams`).
// Правило — то, что браузер и так применяет.
const wbrInline = document.createElement('style');
wbrInline.textContent = 'wbr { display: inline; }';
document.head.appendChild(wbrInline);
