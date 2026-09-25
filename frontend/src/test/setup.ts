import '@testing-library/jest-dom/vitest';

import { afterEach, vi } from 'vitest';

import { misses } from './server';

// Хранилище и заглушка сети чистятся между тестами: иначе пропуск,
// оставленный одним тестом, пускает следующий, и порядок запуска
// начинает значить.
afterEach(() => {
  localStorage.clear();
  vi.restoreAllMocks();
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
