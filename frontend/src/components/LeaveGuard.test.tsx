/**
 * Какой щелчок — уход со страницы (проверка прода 10.10.2026): спрашивать надо ровно
 * там, где набранное пропадёт, а ссылку в новую вкладку и тот же экран — не держать.
 */

import { afterEach, describe, expect, it } from 'vitest';

import { leavingTo } from './LeaveGuard';

const HERE = '/agent';

function clickOn(
  attributes: Record<string, string>,
  init: MouseEventInit = { button: 0 },
): MouseEvent {
  const link = document.createElement('a');
  for (const [name, value] of Object.entries(attributes)) link.setAttribute(name, value);
  const label = document.createElement('span');
  link.append(label);
  document.body.append(link);
  const event = new MouseEvent('click', { bubbles: true, cancelable: true, ...init });
  // Щелчок по подписи внутри ссылки — как по пункту меню со значком.
  Object.defineProperty(event, 'target', { value: label });
  return event;
}

afterEach(() => document.body.replaceChildren());

describe('уход со страницы', () => {
  it('ссылка на другой экран — уход, с адресом и строкой параметров', () => {
    expect(leavingTo(clickOn({ href: '/threads?state=waiting' }), HERE)).toBe(
      '/threads?state=waiting',
    );
  });

  it.each([
    ['тот же экран', { href: '/agent' }, {}],
    ['новая вкладка', { href: '/threads', target: '_blank' }, {}],
    ['скачивание', { href: '/api/donors/export', download: '' }, {}],
    ['чужой сайт — его спросит сам браузер', { href: 'https://elsewhere.example.test/' }, {}],
    ['щелчок с клавишей — ссылка рядом', { href: '/threads' }, { metaKey: true }],
    ['средняя кнопка', { href: '/threads' }, { button: 1 }],
  ])('%s — не уход', (_, attributes, init) => {
    expect(leavingTo(clickOn(attributes, { button: 0, ...init }), HERE)).toBeNull();
  });

  it('щелчок мимо ссылок — не уход', () => {
    const event = new MouseEvent('click', { bubbles: true, cancelable: true });
    Object.defineProperty(event, 'target', { value: document.body });
    expect(leavingTo(event, HERE)).toBeNull();
  });
});
