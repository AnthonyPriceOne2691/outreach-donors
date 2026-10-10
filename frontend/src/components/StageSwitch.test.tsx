/**
 * Переключатель этапа: на узком окне — столбиком во всю ширину, на широком — в ряд.
 *
 * Проверка QA 10.10.2026: на 390 px «Донорам · Рекламодателям · Бизнесам ниши» в ряд
 * выходил за край экрана на 22 px, и третий этап срезался. Раскладку jsdom не считает —
 * проверяется, как переключатель встаёт; что столбик помещается, видно на снимке.
 */

import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { renderWith } from '../test/render';
import { StageSwitch } from './StageSwitch';

const TARGETS = [
  { value: 'donors', label: 'Донорам' },
  { value: 'advertisers', label: 'Рекламодателям' },
  { value: 'niche', label: 'Бизнесам ниши' },
];

/** Окно телефона: медиазапрос узкого окна отвечает «да». Заглушку снимает
 *  `restoreAllMocks` после теста (`test/setup.ts`). */
function onPhone() {
  vi.spyOn(window, 'matchMedia').mockImplementation((query: string) => ({
    matches: query.includes('max-width: 36em'),
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }));
}

function renderSwitch(onChange: (stage: string) => void = () => {}) {
  renderWith(
    <StageSwitch
      label="Кому письма"
      value="donors"
      onChange={onChange}
      stages={TARGETS}
      lead="Очередь на отправку."
    />,
  );
  return screen.getByRole('radiogroup', { name: 'Кому письма' });
}

describe('переключатель этапа', () => {
  it('на телефоне — столбиком во всю ширину, и третий этап выбирается', async () => {
    onPhone();
    const onChange = vi.fn();
    const stages = renderSwitch(onChange);

    expect(stages).toHaveAttribute('data-orientation', 'vertical');
    expect(stages).toHaveAttribute('data-full-width');
    await userEvent.setup().click(screen.getByRole('radio', { name: 'Бизнесам ниши' }));
    expect(onChange).toHaveBeenCalledWith('niche');
  });

  it('на широком окне — в ряд, по содержимому, как было', () => {
    const stages = renderSwitch();

    expect(stages).not.toHaveAttribute('data-orientation', 'vertical');
    expect(stages).not.toHaveAttribute('data-full-width');
  });
});
