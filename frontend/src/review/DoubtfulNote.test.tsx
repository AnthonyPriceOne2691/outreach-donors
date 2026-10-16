/**
 * Сомнительные в «Предложенных»: строка над списком называет обе цифры — сколько видно и
 * сколько предложено, — а кнопка в конце списка открывает скрытых.
 *
 * Замечание владельца 16.10.2026: вкладка «Предложены — 14», а в списке три строки — связь
 * двух чисел держалась на сером «скрыто 11» у переключателя, и её не читали.
 */

import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { renderWith } from '../test/render';
import { DoubtfulNote, noteWords, ShowDoubtful } from './DoubtfulNote';

describe('строка о сомнительных', () => {
  it('называет, сколько видно из скольких и сколько скрыто', () => {
    expect(noteWords(14, 11, false)).toBe(
      'В списке — 3 из 14 предложенных: ещё 11 сайтов судья считает сомнительными — они скрыты, а не выброшены.',
    );
  });

  it('согласует слова с числом: один, два, двадцать один', () => {
    expect(noteWords(5, 1, false)).toBe(
      'В списке — 4 из 5 предложенных: ещё 1 сайт судья считает сомнительным — он скрыт, а не выброшен.',
    );
    expect(noteWords(5, 2, false)).toContain(
      'ещё 2 сайта судья считает сомнительными — они скрыты',
    );
    expect(noteWords(30, 21, false)).toContain('ещё 21 сайт судья считает сомнительным — он скрыт');
  });

  it('все предложенные сомнительны — без «0 из»', () => {
    expect(noteWords(11, 11, false)).toBe(
      'Все предложенные — 11 сайтов — судья считает сомнительными: они скрыты, а не выброшены.',
    );
  });

  it('сомнительные показаны — где они в списке', () => {
    expect(noteWords(14, 11, true)).toBe('В списке — все предложенные, 11 сомнительных — внизу.');
  });

  it('сомнительных нет и они не показаны — строки нет, переключателя тоже', () => {
    renderWith(<DoubtfulNote total={3} hidden={0} shown={false} onShow={() => undefined} />);

    expect(screen.queryByLabelText('Показать сомнительные')).toBeNull();
    expect(screen.queryByText(/сомнительн/)).toBeNull();
  });

  it('переключатель отдаёт «показать» и «спрятать»', async () => {
    const onShow = vi.fn();
    renderWith(<DoubtfulNote total={14} hidden={11} shown={false} onShow={onShow} />);

    await userEvent.setup().click(screen.getByLabelText('Показать сомнительные'));

    expect(onShow).toHaveBeenCalledWith(true);
  });
});

describe('кнопка в конце списка', () => {
  const props = { pending: true, shown: false, hidden: 11, rest: 0 };

  it('видимые показаны все, сомнительные скрыты — кнопка с числом открывает их', async () => {
    const onShow = vi.fn();
    renderWith(<ShowDoubtful {...props} onShow={onShow} />);

    await userEvent.setup().click(screen.getByRole('button', { name: 'Показать 11 сомнительных' }));

    expect(onShow).toHaveBeenCalledOnce();
  });

  it('нет кнопки, пока внизу есть «Показать ещё», и когда показывать нечего', () => {
    const { rerender } = renderWith(<ShowDoubtful {...props} rest={50} onShow={() => undefined} />);
    expect(screen.queryByRole('button')).toBeNull();

    for (const other of [{ shown: true }, { hidden: 0 }, { pending: false }]) {
      rerender(<ShowDoubtful {...props} {...other} onShow={() => undefined} />);
      expect(screen.queryByRole('button')).toBeNull();
    }
  });
});
