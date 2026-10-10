/**
 * «Диалоги» на широком окне: список слева, переписка справа (`ThreadsScreen`).
 *
 * Замечание Anthony 09.10.2026: «много места пустого — справа сразу отображать
 * диалог». Проверяется то, ради чего раскладка: выбранный диалог виден в списке,
 * переход к соседнему — без возврата к списку, фильтр переживает выбор, ждущие
 * человека — сверху. Узкое окно (список и переписка отдельными страницами) — в
 * `ThreadsPage.test` и `ThreadPage.test`: там `matchMedia` по умолчанию узкий.
 */

import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useLocation } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import { SPLIT_QUERY, workKey } from '../layout/split';
import { ADMIN, HOME_ROUTES, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import { reply } from '../test/threadFixtures';
import { shortWhen } from './ThreadList';

function thread(id: number, host: string, state: string, at: string) {
  return {
    id,
    host,
    contact_email: `editor@${host}`,
    campaign: 'Демонстрация',
    stage: 'donors',
    state,
    messages_sent: 1,
    last_event_at: at,
    last_reply_at: null,
    price_white: null,
    price_grey: null,
    currency: null,
  };
}

const THREADS = [
  thread(1, 'digest-weekly.example.test', 'priced', '2026-09-19T10:00:00+00:00'),
  thread(2, 'green-blog.example.test', 'waiting', '2026-09-18T10:00:00+00:00'),
  thread(3, 'tech-review.example.test', 'needs_review', '2026-09-17T10:00:00+00:00'),
];

function view(card: ReturnType<typeof thread>) {
  return {
    card,
    letters: [
      {
        id: card.id * 10,
        step: 0,
        status: 'delivered',
        subject: 'Advertising rates',
        body: 'Good afternoon,',
        sent_at: '2026-09-16T10:00:00+00:00',
        uniqueness: 0.19,
        answers_reply_id: null,
      },
    ],
    incoming: [],
    corridor: { min: 0.15, max: 0.25 },
  };
}

/** Где сейчас экран: адрес целиком, с фильтром. */
function Where() {
  const location = useLocation();
  return <output data-testid="where">{`${location.pathname}${location.search}`}</output>;
}

const narrow = Object.getOwnPropertyDescriptor(window, 'matchMedia');

beforeEach(() => {
  // Широкое окно: совпадает только запрос раскладки рядом.
  window.matchMedia = (query: string) =>
    ({
      matches: query === SPLIT_QUERY,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }) as MediaQueryList;
});

afterEach(() => {
  if (narrow !== undefined) Object.defineProperty(window, 'matchMedia', narrow);
});

async function openWide(path: string, threads = THREADS, extra: Record<string, unknown> = {}) {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  serve({
    'GET /api/auth/me': { body: ADMIN },
    'GET /api/threads': { body: threads },
    ...Object.fromEntries(
      threads.map((card) => [`GET /api/threads/${card.id}`, { body: view(card) }]),
    ),
    'GET /api/replies/unbound?page=1': { body: { rows: [], total: 0, page: 1, limit: 20 } },
    ...(extra as Record<string, never>),
  });
  renderWith(
    <>
      <AppRoutes />
      <Where />
    </>,
    path,
  );
  return within(await screen.findByRole('navigation', { name: 'Список диалогов' }));
}

const where = () => screen.getByTestId('where');

describe('диалоги на широком окне: список и переписка рядом', () => {
  it('открытый диалог — справа, его строка выбрана, «К списку» нет: список рядом', async () => {
    const list = await openWide('/threads/3');

    expect(
      await screen.findByRole('heading', { name: 'tech-review.example.test' }),
    ).toBeInTheDocument();
    expect(await list.findByRole('link', { current: 'page' })).toHaveAttribute(
      'href',
      '/threads/3',
    );
    expect(screen.queryByRole('link', { name: 'К списку' })).not.toBeInTheDocument();
  });

  it('ждущие человека — сверху, остальные по последнему событию', async () => {
    const list = await openWide('/threads');

    const rows = await list.findAllByRole('link');
    expect(rows.map((row) => row.getAttribute('href'))).toEqual([
      '/threads/3',
      '/threads/1',
      '/threads/2',
    ]);
  });

  it('без выбранного — не пустота: что делать и кнопка к первому ждущему', async () => {
    const user = userEvent.setup();
    await openWide('/threads');

    expect(screen.getByRole('heading', { name: 'Выберите диалог в списке' })).toBeInTheDocument();
    await user.click(
      await screen.findByRole('link', { name: /Ждут человека: 1 — открыть первый/ }),
    );

    expect(where()).toHaveTextContent('/threads/3');
    expect(
      await screen.findByRole('heading', { name: 'tech-review.example.test' }),
    ).toBeInTheDocument();
  });

  it('выбор строки держит фильтр в адресе диалога', async () => {
    const user = userEvent.setup();
    const list = await openWide('/threads?state=needs_review');

    await user.click(await list.findByRole('link', { name: /tech-review/ }));

    expect(where()).toHaveTextContent('/threads/3?state=needs_review');
    expect(
      await screen.findByRole('heading', { name: 'tech-review.example.test' }),
    ).toBeInTheDocument();
  });

  it('соседний диалог — клавишами J и K и стрелками в списке, без возврата к списку', async () => {
    const user = userEvent.setup();
    const list = await openWide('/threads/3');
    await list.findByRole('link', { current: 'page' });

    await user.keyboard('j');
    expect(where()).toHaveTextContent('/threads/1');
    await user.keyboard('k');
    expect(where()).toHaveTextContent('/threads/3');

    (await list.findByRole('link', { current: 'page' })).focus();
    await user.keyboard('{ArrowDown}');
    expect(where()).toHaveTextContent('/threads/1');
  });

  it('«Следующий ждущий» — ниже открытого, после последнего — верхний, решённый ведёт к верхнему', async () => {
    const user = userEvent.setup();
    const lead = thread(4, 'price-desk.example.test', 'lead', '2026-09-16T10:00:00+00:00');
    await openWide('/threads/3', [...THREADS, lead]);
    const next = () => screen.findByRole('link', { name: /Следующий ждущий/ });

    expect(await next()).toHaveAttribute('href', '/threads/4');
    await user.click(await next());
    expect(where()).toHaveTextContent('/threads/4');
    expect(
      await screen.findByRole('heading', { name: 'price-desk.example.test' }),
    ).toBeInTheDocument();
    await waitFor(async () => expect(await next()).toHaveAttribute('href', '/threads/3'));

    await user.keyboard('j');
    expect(where()).toHaveTextContent('/threads/1');
    await waitFor(async () => expect(await next()).toHaveAttribute('href', '/threads/3'));
  });

  it('«Следующий ждущий» держит фильтр из адреса', async () => {
    const lead = thread(4, 'price-desk.example.test', 'lead', '2026-09-16T10:00:00+00:00');
    await openWide('/threads/1?state=lead', [...THREADS, lead]);
    expect(await screen.findByRole('link', { name: /Следующий ждущий/ })).toHaveAttribute(
      'href',
      '/threads/4?state=lead',
    );
  });

  it('единственный ждущий открыт — кнопки «Следующий ждущий» нет', async () => {
    await openWide('/threads/3');
    expect(
      await screen.findByRole('heading', { name: 'tech-review.example.test' }),
    ).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /Следующий ждущий/ })).not.toBeInTheDocument();
  });

  it('вернулись в «Диалоги» из другого раздела — открыт тот же диалог с тем же фильтром', async () => {
    const user = userEvent.setup();
    await openWide('/threads/3?state=needs_review', THREADS, HOME_ROUTES);
    const menu = within(screen.getByRole('navigation', { name: 'Разделы' }));
    await screen.findByRole('heading', { name: 'tech-review.example.test' });

    await user.click(menu.getByRole('link', { name: 'Обзор' }));
    expect(where()).toHaveTextContent(/^\/$/);
    await user.click(menu.getByRole('link', { name: /^Диалоги/ }));

    expect(where()).toHaveTextContent('/threads/3?state=needs_review');
    expect(
      await screen.findByRole('heading', { name: 'tech-review.example.test' }),
    ).toBeInTheDocument();
  });

  it('«Не привязаны» — на всю ширину: у ответа без письма переписки справа нет', async () => {
    const user = userEvent.setup();
    await openWide('/threads/3');

    await user.click(await screen.findByRole('radio', { name: 'Не привязаны — 0' }));

    expect(where()).toHaveTextContent('/threads?tab=unbound');
    expect(screen.queryByRole('navigation', { name: 'Список диалогов' })).not.toBeInTheDocument();
  });

  it('с «Не привязаны» обратно на «Диалоги» — открыт тот же диалог с тем же фильтром', async () => {
    // Проверка QA 10.10.2026: вкладка «Диалоги» вела в начало списка, и открытый
    // диалог закрывался.
    const user = userEvent.setup();
    await openWide('/threads/3?state=needs_review');
    await screen.findByRole('heading', { name: 'tech-review.example.test' });

    await user.click(await screen.findByRole('radio', { name: 'Не привязаны — 0' }));
    expect(where()).toHaveTextContent('/threads?tab=unbound');
    await user.click(await screen.findByRole('radio', { name: /^Диалоги/ }));

    expect(where()).toHaveTextContent('/threads/3?state=needs_review');
    expect(
      await screen.findByRole('heading', { name: 'tech-review.example.test' }),
    ).toBeInTheDocument();
  });

  it('отказ разбора цены — у своей переписки: ушли в соседнюю и вернулись — отказа нет', async () => {
    // Переписка рядом со списком не пересоздаётся при выборе строки: отказ, оставшийся
    // от прошлого раза, стоял бы над полями, уже сброшенными к разбору модели.
    const user = userEvent.setup();
    const refusal = '«-5» — не цена: впишите число больше нуля, например 150 или 150.50.';
    const asking = { ...view(THREADS[2]!), incoming: [reply({ id: 70, needs_review: true })] };
    await openWide('/threads/3', THREADS, {
      'GET /api/threads/3': { body: asking },
      'PATCH /api/replies/70': { status: 400, body: { detail: refusal } },
    });
    const white = await screen.findByLabelText('Белая цена');
    await user.clear(white);
    await user.type(white, '-5');
    await user.click(screen.getByRole('button', { name: 'Подтвердить' }));
    expect(await screen.findByText(`— ${refusal}`)).toBeInTheDocument();

    await user.keyboard('j');
    await screen.findByRole('heading', { name: 'digest-weekly.example.test' });
    await user.keyboard('k');
    await screen.findByRole('heading', { name: 'tech-review.example.test' });

    expect(screen.getByLabelText('Белая цена')).toHaveValue('300');
    expect(screen.queryByText(`— ${refusal}`)).not.toBeInTheDocument();
  });

  it('поиск — подсказкой в слово, что ищется — в имени поля', async () => {
    // На 1280 колонка списка — 20rem: «Донор или адрес» обрезалось до «Донор или ад»
    // (проверка QA 10.10.2026).
    await openWide('/threads');

    const search = screen.getByRole('textbox', { name: 'Поиск по донору или адресу' });
    expect(search).toHaveAttribute('placeholder', 'Поиск');
  });
});

describe('мелочи раскладки', () => {
  it('рама держит ключ по разделу только у раздела со списком рядом и только на широком окне', () => {
    expect(workKey('/threads/3', true)).toBe('/threads');
    expect(workKey('/threads', true)).toBe('/threads');
    expect(workKey('/threads/3', false)).toBe('/threads/3');
    expect(workKey('/threadsx', true)).toBe('/threadsx');
    expect(workKey('/donors/5', true)).toBe('/donors/5');
  });

  it('дата строки — коротко: время сегодня, день и месяц этого года, полная — прошлых лет', () => {
    const now = new Date('2026-10-09T15:00:00');
    expect(shortWhen(new Date('2026-10-09T09:05:00').toISOString(), now)).toBe('09:05');
    expect(shortWhen(new Date('2026-10-08T14:09:00').toISOString(), now)).toBe('08.10');
    expect(shortWhen(new Date('2025-12-31T10:00:00').toISOString(), now)).toBe('31.12.2025');
    expect(shortWhen(null, now)).toBe('—');
  });
});
