/**
 * «Чего не хватило» обходу — на экране, а не только в базе и журнале.
 *
 * Записка каскада (`fetch.Degradation`) жила в записи обхода, и «отказали подряд все
 * прокси», «сервис рендеринга отключился», «потолок Этапа 2 выбран» человек видеть не мог:
 * обход выглядел обычным «частично». Теперь записка — за тем же значком, что и причина.
 */

import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import type { CrawlRow } from '../api/crawls';
import { renderWith } from '../test/render';
import { CrawlState, degradationText } from './CrawlTable';

function crawl(overrides: Partial<CrawlRow>): CrawlRow {
  return {
    id: 9,
    host: 'donor.example.test',
    status: 'done',
    outcome: 'partial',
    stop_reason: 'unhealthy',
    pages: 120,
    max_pages: 1000,
    articles: 90,
    links: 40,
    resumes: 0,
    requested_by: 'op@team.example.test',
    created_at: '2026-10-12T08:00:00+00:00',
    finished_at: '2026-10-12T08:30:00+00:00',
    reason: null,
    ...overrides,
  };
}

describe('чего не хватило обходу', () => {
  it('по строке на уровень, словами, а не кодами', () => {
    expect(degradationText(undefined)).toBeNull();
    expect(degradationText({})).toBeNull();
    expect(
      degradationText({
        proxy: 'отказали подряд все прокси (2): последний №2 (gate.example.test:8888) — код 403',
        render: 'потолок Этапа 2 на обход выбран: 10.00 из 10.00 $',
      }),
    ).toBe(
      'Не хватило:\n' +
        'прокси — отказали подряд все прокси (2): последний №2 (gate.example.test:8888) — код 403\n' +
        'сервис рендеринга — потолок Этапа 2 на обход выбран: 10.00 из 10.00 $',
    );
  });

  it('записка — за значком у состояния обхода, вместе с причиной', async () => {
    const user = userEvent.setup();
    renderWith(
      <CrawlState
        crawl={crawl({
          reason: 'остановлен по доле отказов',
          degradation: { proxy: 'отказали подряд все прокси (2)' },
        })}
      />,
    );

    await user.click(screen.getByRole('button', { name: 'Что случилось' }));

    const note = await screen.findByRole('dialog', { name: 'Что случилось' });
    expect(note).toHaveTextContent('остановлен по доле отказов');
    expect(note).toHaveTextContent('Не хватило:');
    expect(note).toHaveTextContent('прокси — отказали подряд все прокси (2)');
  });

  it('без записки и без причины значка нет', () => {
    renderWith(<CrawlState crawl={crawl({})} />);

    expect(screen.queryByRole('button', { name: 'Что случилось' })).not.toBeInTheDocument();
  });
});
