/**
 * Цены списком: каждая цена из ответа — строкой, словами донора.
 *
 * Проверяется договор строки (продукт и ниша как написал донор, деньги
 * общей функцией, срок нашими словами) и то, что пустой и отсутствующий
 * список не рисуют ничего: «цен нет» и «разобран до списка» на экране
 * не должны выглядеть пустой рамкой.
 */

import { screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import type { Offer } from '../api/offers';
import { renderWith } from '../test/render';
import { offerLine, ReplyOffers } from './ReplyOffers';

const GUEST: Offer = {
  product: 'guest post',
  niche: null,
  price: '150',
  currency: 'USD',
  period: null,
};

/** Неразрывные пробелы денег — обычными: так строку читает человек. */
function plain(text: string | null | undefined): string {
  return (text ?? '').replace(/[\u00a0\u202f]/g, ' ');
}

describe('строка цены', () => {
  it('продукт — словами донора, деньги — как у цены рядом', () => {
    expect(plain(offerLine(GUEST))).toBe('guest post — 150,00 $');
  });

  it('ниша идёт за продуктом через точку', () => {
    expect(plain(offerLine({ ...GUEST, niche: 'casino', price: '300' }))).toBe(
      'guest post · casino — 300,00 $',
    );
  });

  it('срок — нашими словами', () => {
    const homepage = { ...GUEST, product: 'homepage link', price: '500' };

    expect(plain(offerLine({ ...homepage, period: 'month' }))).toBe(
      'homepage link — 500,00 $ в месяц',
    );
    expect(plain(offerLine({ ...homepage, period: 'year', currency: 'EUR' }))).toBe(
      'homepage link — 500,00 € в год',
    );
  });

  it('валюта не названа — только число', () => {
    expect(plain(offerLine({ ...GUEST, currency: null }))).toBe('guest post — 150,00');
  });
});

describe('список цен', () => {
  it('каждая цена — своей строкой под подписью', () => {
    renderWith(
      <ReplyOffers
        offers={[GUEST, { ...GUEST, product: 'link insertion', niche: 'crypto', price: '90' }]}
      />,
    );

    expect(screen.getByText('Все цены из ответа')).toBeInTheDocument();
    const rows = within(screen.getByRole('list', { name: 'Все цены из ответа' })).getAllByRole(
      'listitem',
    );
    expect(rows.map((row) => plain(row.textContent))).toEqual([
      'guest post — 150,00 $',
      'link insertion · crypto — 90,00 $',
    ]);
  });

  it.each([
    ['пустой', []],
    ['разобран до списка', null],
    ['поля нет', undefined],
  ])('%s — не рисуется ничего', (_, offers) => {
    renderWith(<ReplyOffers offers={offers} />);

    expect(screen.queryByText('Все цены из ответа')).toBeNull();
    expect(screen.queryByRole('list')).toBeNull();
  });
});
