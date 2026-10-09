/**
 * Отказ в уведомлении не исчезает сам, «готово» — исчезает (`notices.ts`).
 */

import { notifications } from '@mantine/notifications';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { notify } from './notices';

afterEach(() => {
  vi.restoreAllMocks();
});

function spy() {
  return vi.spyOn(notifications, 'show').mockReturnValue('id');
}

describe('уведомление', () => {
  it('отказ без срока остаётся, пока его не закроют', () => {
    const show = spy();
    notify({ title: 'Не отправили', message: 'ящик на паузе', color: 'red' });
    expect(show).toHaveBeenCalledWith({
      title: 'Не отправили',
      message: 'ящик на паузе',
      color: 'red',
      autoClose: false,
    });
  });

  it('явный срок у вызова сильнее правила', () => {
    const show = spy();
    notify({ message: 'повторите', color: 'red', autoClose: 8000 });
    expect(show).toHaveBeenCalledWith({ message: 'повторите', color: 'red', autoClose: 8000 });
  });

  it('«готово» и предупреждения уходят сами', () => {
    const show = spy();
    notify({ message: 'Цена записана', color: 'green' });
    notify({ message: 'Почта выключена', color: 'yellow' });
    expect(show).toHaveBeenNthCalledWith(1, { message: 'Цена записана', color: 'green' });
    expect(show).toHaveBeenNthCalledWith(2, { message: 'Почта выключена', color: 'yellow' });
  });
});
