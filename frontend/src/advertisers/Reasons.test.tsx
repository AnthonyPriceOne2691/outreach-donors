/**
 * Причины балла (`Reasons`) — одним видом в очереди проверки и во «Всех рекламодателях»
 * (разведка экранов 10.11.2026): «+2» не встаёт строкой одно, а длинное пояснение причины — в «i»
 * у короткой, а не тремя строками ячейки.
 */

import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { renderWith } from '../test/render';
import { keptPoints, Reasons } from './Reasons';

const WHY = 'Чаще фонд или сообщество, чем рекламодатель: решает человек';

describe('балл не отрывается от причины', () => {
  it('перед «+N» в конце — неразрывный пробел, остальные пробелы — как были', () => {
    expect(keptPoints('коммерческая ссылка под nofollow +2')).toBe(
      'коммерческая ссылка под nofollow\u00a0+2',
    );
    expect(keptPoints('ссылки с 12 страниц донора +3')).toBe('ссылки с 12 страниц донора\u00a0+3');
    // Без балла в конце — как написал сервер.
    expect(keptPoints('кому не пишем: DR 95 > 80')).toBe('кому не пишем: DR 95 > 80');
  });

  it('в строке причины — неразрывный пробел перед баллом', () => {
    renderWith(<Reasons reasons={['rel=sponsored +5']} />);

    expect(screen.getByText('rel=sponsored +5').textContent).toBe('rel=sponsored\u00a0+5');
  });
});

describe('пояснение причины — в «i» у неё', () => {
  it('у причины с пояснением сервера — значок, у остальных — нет', async () => {
    renderWith(
      <Reasons reasons={['rel=sponsored +5', 'зона .org']} notes={{ 'зона .org': WHY }} />,
    );
    const user = userEvent.setup();

    expect(screen.getByText('зона .org')).toBeInTheDocument();
    expect(screen.queryByText(WHY)).toBeNull();
    expect(screen.getAllByRole('button')).toHaveLength(1);

    await user.hover(screen.getByRole('button', { name: 'Что значит «зона .org»' }));

    expect(await screen.findByText(WHY)).toBeInTheDocument();
  });

  it('сервер старше экрана — пояснений нет, причины как пришли', () => {
    renderWith(<Reasons reasons={['коммерческий анкор +1']} />);

    expect(screen.getByText('коммерческий анкор +1')).toBeInTheDocument();
    expect(screen.queryByRole('button')).toBeNull();
  });
});
