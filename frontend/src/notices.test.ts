/**
 * Отказ в уведомлении не исчезает сам, «готово» — исчезает (`notices.ts`).
 */

import { notifications } from '@mantine/notifications';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { notify, refuse, settle } from './notices';

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

describe('отказ действия и его успех', () => {
  // Проверка прода 10.10.2026: красный «Смета не посчиталась» висел рядом с новой,
  // удачной сметой. Успех снимает отказ своего действия — и только его.
  function shows() {
    let made = 0;
    const show = vi.spyOn(notifications, 'show').mockImplementation(() => `id-${(made += 1)}`);
    const hide = vi.spyOn(notifications, 'hide').mockImplementation((id: string) => id);
    return { show, hide };
  }

  it('успех снимает свой отказ, а чужой — нет', () => {
    const { hide } = shows();
    const own = refuse('смета', { title: 'Смета не посчиталась', message: 'нет связи' });
    const foreign = notify({ title: 'Письмо не ушло', message: 'ящик на паузе', color: 'red' });

    settle('смета');

    expect(hide).toHaveBeenCalledTimes(1);
    expect(hide).toHaveBeenCalledWith(own);
    expect(hide).not.toHaveBeenCalledWith(foreign);
  });

  it('отказ — красный и не гаснет сам; повторный встаёт на место прежнего', () => {
    const { show, hide } = shows();
    const first = refuse('запуск', { title: 'Прогон не запущен', message: 'раз' });
    refuse('запуск', { title: 'Прогон не запущен', message: 'два' });

    expect(show).toHaveBeenLastCalledWith({
      title: 'Прогон не запущен',
      message: 'два',
      color: 'red',
      autoClose: false,
    });
    expect(hide).toHaveBeenCalledWith(first);
  });

  it('успех без прежнего отказа ничего не снимает', () => {
    const { hide } = shows();
    settle('сборка');
    expect(hide).not.toHaveBeenCalled();
  });
});
