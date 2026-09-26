/**
 * Ответ донора и вердикт судьи — теми же словами, что фильтры отбора,
 * и цена — общей функцией денег (26.09.2026).
 *
 * Компонент общий у отбора и рассмотрения прогона: сырая строка сервера
 * «250.00 EUR» стояла на обоих экранах рядом с «1 250,00 €» на соседних.
 */

import { screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import type { MachineView, SellerView } from '../api/types';
import { renderWith } from '../test/render';
import { JudgeVerdict, SellerAnswer } from './JudgeVerdict';

const SILENT: SellerView = { answer: null, answered_at: null, price: null, currency: null };

describe('ответ донора', () => {
  it('цена — с разрядами, запятой и знаком валюты', () => {
    renderWith(
      <SellerAnswer seller={{ ...SILENT, answer: 'sells', price: '1250.5', currency: 'EUR' }} />,
    );

    expect(screen.getByText('продаёт')).toBeInTheDocument();
    expect(screen.getByText('1 250,50 €')).toBeInTheDocument();
  });

  it('валюта без знака — кодом, крупная цена — с разрядами', () => {
    renderWith(
      <SellerAnswer
        seller={{ ...SILENT, answer: 'sells', price: '999999.99', currency: 'USDT' }}
      />,
    );

    expect(screen.getByText('999 999,99 USDT')).toBeInTheDocument();
  });

  it('«бесплатно» и «не продаёт» — без цены, «не отвечал» — тихим словом', () => {
    renderWith(
      <>
        <SellerAnswer seller={{ ...SILENT, answer: 'free', price: '10', currency: 'USD' }} />
        <SellerAnswer seller={{ ...SILENT, answer: 'declines' }} />
        <SellerAnswer seller={SILENT} />
      </>,
    );

    expect(screen.getByText('берёт бесплатно')).toBeInTheDocument();
    expect(screen.getByText('не продаёт')).toBeInTheDocument();
    expect(screen.getByText('не отвечал')).toBeInTheDocument();
    expect(screen.queryByText('10,00 $')).toBeNull();
  });
});

describe('вердикт судьи', () => {
  it('вердикта нет — тем же словом, что в фильтре «Судья»', () => {
    const none: MachineView = {
      intent: null,
      recommendation: null,
      decided_by: null,
      quote: null,
      reason: null,
      source_url: null,
      home_shop: [],
      home_reached: null,
      judged_at: null,
    };
    renderWith(<JudgeVerdict machine={none} />);

    expect(screen.getByText('судья не смотрел')).toBeInTheDocument();
  });
});
