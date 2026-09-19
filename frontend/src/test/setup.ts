import '@testing-library/jest-dom/vitest';

import { afterEach, vi } from 'vitest';

// Хранилище и заглушка сети чистятся между тестами: иначе пропуск,
// оставленный одним тестом, пускает следующий, и порядок запуска
// начинает значить.
afterEach(() => {
  localStorage.clear();
  vi.restoreAllMocks();
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

if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = () => {};
}
