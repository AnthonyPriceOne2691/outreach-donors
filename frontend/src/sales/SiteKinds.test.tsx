/**
 * Поле «Вид сайта» окна «Качество сайтов»: выбор — флажками с подписями для человека, текущий — из
 * карточки гипотезы. На сервер уходит весь выбор, когда его поправили (пустой — снять выбор), и
 * ничего, когда не трогали; порядок — как у сервера. Имена и числа выдуманы.
 */

import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import type { HypothesisCard } from '../api/salesTypes';
import { ADMIN, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import type { Recorded } from '../test/server';
import type { PlanRow } from './planApi';
import { PlanQuality } from './PlanQuality';
import { kindsChange, siteKindsOf } from './SiteKinds';

const QUALITY = '/api/sales/plan-hypotheses/7/quality';

const ROW: PlanRow = {
  id: 7,
  hypothesis_id: 70,
  name: 'Стоматологии — пример',
  position: 1,
  sheet: 'Лист 1',
  markets: ['gr'],
  language: 'el',
  target: 300,
  max_pages: 2,
  cap_usd: 40,
  stage: 'collecting',
  found: 12,
  spent_usd: 1.5,
  keywords: 4,
  bought: 2,
  wave_page: 1,
  wave_bought: 2,
  reserved_usd: 0,
  problems: [],
};

function cardWith(kinds?: string[]): HypothesisCard {
  const card: HypothesisCard = {
    id: 70,
    name: 'Стоматологии — пример',
    description: 'Клиники со своим сайтом.',
    created_at: '2026-11-19T09:00:00+00:00',
    leads: { new: 0, ready: 0, rejected: 0 },
    total: 0,
  };
  return kinds === undefined ? card : ({ ...card, site_kinds: kinds } as HypothesisCard);
}

function setUp(): Recorded {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  return serve({
    'GET /api/auth/me': { body: ADMIN },
    'GET /api/sales/plans': { body: { plans: [] } },
    [`PATCH ${QUALITY}`]: { body: ROW },
  });
}

function sentBodies(recorded: Recorded): unknown[] {
  return recorded.calls.filter((call) => call.path === QUALITY).map((call) => call.body);
}

describe('«Вид сайта» в «Качестве сайтов»', () => {
  it('поля нет в карточке — ничего не выбрано; выбрали издание — уходит только вид', async () => {
    const onClose = vi.fn();
    const recorded = setUp();
    renderWith(<PlanQuality row={ROW} card={cardWith()} onClose={onClose} />);
    const user = userEvent.setup();

    for (const label of ['Сайт самой компании', 'Каталог, биржа, площадка', 'Издание или блог']) {
      expect(screen.getByRole('checkbox', { name: label })).not.toBeChecked();
    }
    expect(
      screen.getByText('Пусто — какие сайты подходят, решает описание сегмента'),
    ).toBeVisible();
    await user.click(screen.getByRole('checkbox', { name: 'Издание или блог' }));
    await user.click(screen.getByRole('button', { name: 'Сохранить' }));

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(sentBodies(recorded)).toEqual([{ site_kinds: ['media'] }]);
  });

  it('выбор карточки отмечен; сняли всё — уходит пустой выбор', async () => {
    const onClose = vi.fn();
    const recorded = setUp();
    renderWith(<PlanQuality row={ROW} card={cardWith(['media', 'own'])} onClose={onClose} />);
    const user = userEvent.setup();

    const own = screen.getByRole('checkbox', { name: 'Сайт самой компании' });
    const media = screen.getByRole('checkbox', { name: 'Издание или блог' });
    expect(own).toBeChecked();
    expect(media).toBeChecked();
    expect(
      screen.getByRole('checkbox', { name: 'Не компания: госорган, школа, ассоциация' }),
    ).not.toBeChecked();
    await user.click(own);
    await user.click(media);
    await user.click(screen.getByRole('button', { name: 'Сохранить' }));

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(sentBodies(recorded)).toEqual([{ site_kinds: [] }]);
  });

  it('вид не трогали — его в запросе нет; ничего не поправили — запроса нет', async () => {
    const onClose = vi.fn();
    const recorded = setUp();
    renderWith(<PlanQuality row={ROW} card={cardWith(['own'])} onClose={onClose} />);

    await userEvent.setup().click(screen.getByRole('button', { name: 'Сохранить' }));

    expect(onClose).toHaveBeenCalled();
    expect(sentBodies(recorded)).toEqual([]);
  });
});

describe('«Вид сайта» — имя группы и подсказка', () => {
  it('группа названа словами подписи, без кнопки «i»; подсказка говорит про пересуд и наблюдение', async () => {
    setUp();
    renderWith(<PlanQuality row={ROW} card={cardWith()} onClose={vi.fn()} />);

    const group = screen.getByRole('group', { name: 'Вид сайта' });
    expect(group).toHaveAccessibleName('Вид сайта');
    expect(within(group).getAllByRole('checkbox')).toHaveLength(4);
    await userEvent.setup().hover(screen.getByRole('button', { name: 'Что значит «Вид сайта»' }));

    const hint = await screen.findByText(/Поправили — пересудите прогон/);
    expect(hint).toHaveTextContent(
      'судья в наблюдении такой сайт не отсекает — он в «Судья отказал бы»',
    );
  });
});

describe('выбор — порядком сервера', () => {
  it('карточка и правка — по порядку видов, чужие значения не в счёт', () => {
    expect(siteKindsOf(cardWith(['media', 'forum', 'own']))).toEqual(['own', 'media']);
    expect(siteKindsOf(undefined)).toEqual([]);
    expect(kindsChange(['own', 'media'], ['media', 'own'])).toEqual({});
    expect(kindsChange([], ['other', 'intermediary'])).toEqual({
      site_kinds: ['intermediary', 'other'],
    });
  });
});
