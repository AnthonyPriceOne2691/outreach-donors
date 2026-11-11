/**
 * «Диалоги» на широком окне: список слева, переписка справа (`ThreadsScreen`).
 *
 * Замечание Anthony 09.10.2026: «много места пустого — справа сразу отображать
 * диалог». Проверяется то, ради чего раскладка: выбранный диалог виден в списке,
 * переход к соседнему — без возврата к списку, фильтр переживает выбор, ждущие
 * человека — сверху. Узкое окно (список и переписка отдельными страницами) — в
 * `ThreadsPage.test` и `ThreadPage.test`: там `matchMedia` по умолчанию узкий.
 *
 * Переписка открывается с частью в адресе (`?stage=donors`), как из пункта меню «Диалоги
 * с донорами»: без неё переписка дописала бы часть сама (`threadPart.ts`), и ссылки до и
 * после замены адреса отличались бы.
 */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useLocation } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import { SPLIT_QUERY, workKey } from '../layout/split';
import { ADMIN, HOME_ROUTES, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve, type Recorded } from '../test/server';
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

/** Что ушло на сервер в последнем `openWide`. */
let served: Recorded = { calls: [] };

/** Сколько раз список диалогов спрошен целиком. */
const listFetches = () => served.calls.filter((call) => call.path === '/api/threads').length;

async function openWide(path: string, threads = THREADS, extra: Record<string, unknown> = {}) {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  served = serve({
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

/** Ответ без письма — выдуманный, словами сервера (`replies/unbound.py`). */
const STRAY = {
  id: 8,
  received_at: '2026-09-28T15:40:00+00:00',
  from_email: 'elena@donor.example.test',
  to: ['team@donor.example.test'],
  subject: 'Rates',
  kind: 'human',
  preview: 'Our rate card is attached.',
  text: 'Our rate card is attached.',
  attachments: [],
  reason: 'no_label',
  reason_text:
    'Метки в адресе нет, и заголовков цепочки тоже: письмо написано заново, а не ответом на наше. Донора ищут по отправителю и тексту.',
  reason_short: 'написано заново, не ответом на наше письмо',
};
const ONE_UNBOUND = {
  'GET /api/replies/unbound?page=1': { body: { rows: [STRAY], total: 1, page: 1, limit: 20 } },
};

/** Открывающий тег узла — имя, классы, стили и признаки, без содержимого. */
const shell = (node: Element) => (node.cloneNode(false) as Element).outerHTML;

/**
 * Шапка колонки, как её видит раскладка: сами узлы заголовка и переключателя, чем нарисован
 * переключатель и что стоит от него до корня раскладки. Пикселей в jsdom нет — сверяется
 * то, из чего они получаются: одна карточка, те же поля и размер, тот же порядок над ним.
 */
function headOf(part: string) {
  const tabs = screen.getByRole('radiogroup', { name: 'Вкладки диалогов' });
  const title = screen.getByRole('heading', { name: part });
  const head = tabs.closest('.threadsHead')!;
  const card = head.closest('.threadsSplitList')!;
  const path: string[] = [];
  for (let node: Element | null = tabs; node !== null && node !== card.parentElement; ) {
    path.push(shell(node));
    node = node.parentElement;
  }
  return {
    tabs,
    title,
    card,
    root: card.parentElement!,
    // От переключателя вверх до карточки: каждый узел — тем же тегом, классами и стилями.
    path,
    // Над переключателем — только строка заголовка, и шапка стоит в колонке первой.
    above: [...head.children].map((child) => child.contains(title) || child === tabs),
    first: head.previousElementSibling === null && head.contains(title),
    level: title.tagName,
  };
}

describe('диалоги на широком окне: список и переписка рядом', () => {
  it('открытый диалог — справа, его строка выбрана, «К списку» нет: список рядом', async () => {
    const list = await openWide('/threads/3?stage=donors');

    expect(
      await screen.findByRole('heading', { name: 'tech-review.example.test' }),
    ).toBeInTheDocument();
    expect(await list.findByRole('link', { current: 'page' })).toHaveAttribute(
      'href',
      '/threads/3?stage=donors',
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

  it('«Ждут человека» — той стороны, что выбрана: при «Рекламодатели» кнопка не ведёт к донору', async () => {
    // Замечание ревью продаж к #380: число и «открыть первый» считали обе стороны.
    const brand = {
      ...thread(4, 'brand.example.test', 'lead', '2026-09-20T10:00:00+00:00'),
      stage: 'advertisers',
    };
    await openWide('/threads', [...THREADS, brand]);
    expect(
      await screen.findByRole('link', { name: /Ждут человека: 2 — открыть первый/ }),
    ).toBeInTheDocument();

    const user = userEvent.setup();
    await user.click(screen.getByRole('radio', { name: 'Рекламодатели' }));

    expect(
      await screen.findByRole('link', { name: /Ждут человека: 1 — открыть первый/ }),
    ).toHaveAttribute('href', '/threads/4?role=advertisers');
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
    await openWide('/threads/3?stage=donors', [...THREADS, lead]);
    const next = () => screen.findByRole('link', { name: /Следующий ждущий/ });

    expect(await next()).toHaveAttribute('href', '/threads/4?stage=donors');
    await user.click(await next());
    expect(where()).toHaveTextContent('/threads/4?stage=donors');
    expect(
      await screen.findByRole('heading', { name: 'price-desk.example.test' }),
    ).toBeInTheDocument();
    await waitFor(async () =>
      expect(await next()).toHaveAttribute('href', '/threads/3?stage=donors'),
    );

    await user.keyboard('j');
    expect(where()).toHaveTextContent('/threads/1?stage=donors');
    await waitFor(async () =>
      expect(await next()).toHaveAttribute('href', '/threads/3?stage=donors'),
    );
  });

  it('переход к соседней переписке не перекачивает весь список — он рядом и уже есть', async () => {
    // Проверка прода 10.10.2026: «Следующий ждущий» монтируется с каждой перепиской, и его
    // наблюдатель списка перекачивал весь /api/threads на каждом переходе — J, K и кнопкой.
    const user = userEvent.setup();
    const lead = thread(4, 'price-desk.example.test', 'lead', '2026-09-16T10:00:00+00:00');
    await openWide('/threads/3', [...THREADS, lead]);
    const next = () => screen.findByRole('link', { name: /Следующий ждущий/ });
    await next();
    expect(listFetches()).toBe(1);

    await user.click(await next());
    await screen.findByRole('heading', { name: 'price-desk.example.test' });
    await next();
    await user.keyboard('j');
    await screen.findByRole('heading', { name: 'digest-weekly.example.test' });
    await next();

    expect(listFetches()).toBe(1);
  });

  it('после решения по ответу и после ответа список рядом обновляется', async () => {
    const user = userEvent.setup();
    const asking = { ...view(THREADS[2]!), incoming: [reply({ id: 70, needs_review: true })] };
    await openWide('/threads/3', THREADS, {
      'GET /api/threads/3': { body: asking },
      'PATCH /api/replies/70': {
        body: { id: 70, reviewed_by: 'админ@site.com', stored_price: true },
      },
      'POST /api/threads/3/answer': { body: { id: 9, sender_email: 'anna@mail.test', real: true } },
    });
    await screen.findByLabelText('Белая цена');
    expect(listFetches()).toBe(1);

    await user.click(screen.getByRole('button', { name: 'Подтвердить' }));
    await waitFor(() => expect(listFetches()).toBe(2));

    await user.type(screen.getByLabelText('Текст ответа'), 'Thanks! Which topics?');
    await user.click(screen.getByRole('button', { name: 'Отправить' }));
    await waitFor(() => expect(listFetches()).toBe(3));
  });

  it('«Следующий ждущий» держит фильтр из адреса', async () => {
    const lead = thread(4, 'price-desk.example.test', 'lead', '2026-09-16T10:00:00+00:00');
    await openWide('/threads/1?stage=donors&state=lead', [...THREADS, lead]);
    expect(await screen.findByRole('link', { name: /Следующий ждущий/ })).toHaveAttribute(
      'href',
      '/threads/4?stage=donors&state=lead',
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
    await openWide('/threads/3?stage=donors&state=needs_review', THREADS, HOME_ROUTES);
    const menu = within(screen.getByRole('navigation', { name: 'Разделы' }));
    await screen.findByRole('heading', { name: 'tech-review.example.test' });

    await user.click(menu.getByRole('link', { name: 'Обзор' }));
    expect(where()).toHaveTextContent(/^\/$/);
    // У админа в меню три пункта одного пути — доноров, рекламодателей и продаж.
    await user.click(menu.getByRole('link', { name: 'Диалоги с донорами' }));

    expect(where()).toHaveTextContent('/threads/3?stage=donors&state=needs_review');
    expect(
      await screen.findByRole('heading', { name: 'tech-review.example.test' }),
    ).toBeInTheDocument();
  });

  it('«Не привязаны» — на всю ширину: у ответа без письма переписки справа нет', async () => {
    const user = userEvent.setup();
    await openWide('/threads/3?stage=donors');

    await user.click(await screen.findByRole('radio', { name: 'Не привязаны — 0' }));

    expect(where()).toHaveTextContent('/threads?stage=donors&tab=unbound');
    expect(screen.queryByRole('navigation', { name: 'Список диалогов' })).not.toBeInTheDocument();
  });

  it('шапка — заголовок части и вкладки — та же и на том же месте в обеих вкладках', async () => {
    // Разведка экранов 10.11.2026: «Не привязаны» рисовала страница списка со своей шапкой, и
    // переключатель под указателем уезжал — у продаж на 55 px вниз, у рекламодателей на 21 px
    // вниз и на 30 влево. Теперь шапка одна: смена вкладки её не пересоздаёт и не перестраивает.
    const user = userEvent.setup();
    await openWide('/threads/3?stage=donors', THREADS, ONE_UNBOUND);
    const toUnbound = await screen.findByRole('radio', { name: 'Не привязаны — 1' });
    const threads = headOf('Диалоги с донорами');
    expect(threads.root).toHaveClass('threadsSplit');
    expect(threads.root).not.toHaveAttribute('data-whole');
    expect(threads).toMatchObject({ above: [true, true], first: true, level: 'H4' });

    await user.click(toUnbound);
    const replies = await screen.findByRole('list', { name: 'Непривязанные ответы' });

    const unbound = headOf('Диалоги с донорами');
    // Те же узлы — буквально: заголовок, переключатель и карточка остались на месте.
    expect(unbound.tabs).toBe(threads.tabs);
    expect(unbound.title).toBe(threads.title);
    expect(unbound.card).toBe(threads.card);
    expect(unbound.root).toBe(threads.root);
    // И нарисованы так же: теги, классы, размер и поля от переключателя до карточки.
    expect(unbound.path).toEqual(threads.path);
    expect(unbound).toMatchObject({ above: [true, true], first: true, level: 'H4' });
    // Меняется только то, что под переключателем: ответы — в той же карточке, на обе колонки.
    expect(unbound.root).toHaveAttribute('data-whole');
    expect(unbound.card).toContainElement(replies);
    expect(unbound.root.querySelector('.threadsSplitPane')).toBeNull();
    expect(within(replies).getByText('elena@donor.example.test')).toBeInTheDocument();

    await user.click(screen.getByRole('radio', { name: /^Диалоги/ }));
    await screen.findByRole('navigation', { name: 'Список диалогов' });
    const back = headOf('Диалоги с донорами');
    expect(back.tabs).toBe(threads.tabs);
    expect(back.path).toEqual(threads.path);
    expect(back.root).not.toHaveAttribute('data-whole');
  });

  it('«Не привязаны» по ссылке: та же шапка, страницы ответов — в адресе и в запросе', async () => {
    // Вкладку на широком окне рисует колонка списка: страница из адреса доходит до ответов,
    // а число во вкладке — всех ответов, а не этой страницы.
    const user = userEvent.setup();
    localStorage.setItem(TOKEN_KEY, 'пропуск');
    served = serve({
      'GET /api/auth/me': { body: ADMIN },
      'GET /api/threads': { body: THREADS },
      'GET /api/replies/unbound?page=1': {
        body: { rows: [STRAY], total: 25, page: 1, limit: 20 },
      },
      'GET /api/replies/unbound?page=2': {
        body: {
          rows: [{ ...STRAY, id: 30, from_email: 'late@donor.example.test' }],
          total: 25,
          page: 2,
          limit: 20,
        },
      },
    });
    renderWith(
      <>
        <AppRoutes />
        <Where />
      </>,
      '/threads?stage=donors&tab=unbound',
    );

    expect(await screen.findByText('elena@donor.example.test')).toBeInTheDocument();
    expect(headOf('Диалоги с донорами')).toMatchObject({ first: true, level: 'H4' });
    expect(screen.getByRole('radio', { name: 'Не привязаны — 25' })).toBeChecked();
    expect(screen.queryByRole('navigation', { name: 'Список диалогов' })).toBeNull();

    const pager = screen.getByRole('navigation', { name: 'Страницы непривязанных ответов' });
    await user.click(within(pager).getByRole('button', { name: 'Страница 2' }));

    expect(await screen.findByText('late@donor.example.test')).toBeInTheDocument();
    expect(where()).toHaveTextContent('/threads?stage=donors&tab=unbound&page=2');
    expect(served.calls.map((call) => call.path)).toContain('/api/replies/unbound?page=2');
    expect(screen.getByRole('radio', { name: 'Не привязаны — 25' })).toBeChecked();
  });

  it('правила раскладки: карточка «Не привязаны» берёт колонки сетки, шапка — в первой', () => {
    // Ширину шапки держит сетка, а не число: на обеих вкладках это первая колонка той же
    // сетки за вычетом поля и кромки карточки. Пиксели меряются в браузере, не здесь.
    const glass = readFileSync(resolve(process.cwd(), 'src/styles/glass.css'), 'utf8');

    expect(glass).toMatch(
      /\.threadsSplit\[data-whole\] > \.threadsSplitList \{\s*grid-column: 1 \/ -1;\s*display: grid;\s*grid-template-columns: subgrid;/,
    );
    expect(glass).toMatch(
      /\.threadsSplit\[data-whole\] \.threadListColumn \{\s*display: contents;/,
    );
    expect(glass).toMatch(
      /\.threadsSplit\[data-whole\] \.threadsHead \{\s*grid-column: 1;\s*margin-inline-end: calc\(var\(--mantine-spacing-md\) \+ 1px\);/,
    );
    // Страница на «Не привязаны» не становится короче окна: прокрутку не поджимает.
    expect(glass).toMatch(
      /\.threadsSplit\[data-whole\] \{\s*height: auto;\s*min-height: max\(32rem, var\(--threads-height\)\);/,
    );
  });

  it('с «Не привязаны» обратно на «Диалоги» — открыт тот же диалог с тем же фильтром', async () => {
    // Проверка QA 10.10.2026: вкладка «Диалоги» вела в начало списка, и открытый
    // диалог закрывался.
    const user = userEvent.setup();
    await openWide('/threads/3?stage=donors&state=needs_review');
    await screen.findByRole('heading', { name: 'tech-review.example.test' });

    await user.click(await screen.findByRole('radio', { name: 'Не привязаны — 0' }));
    expect(where()).toHaveTextContent('/threads?stage=donors&tab=unbound');
    await user.click(await screen.findByRole('radio', { name: /^Диалоги/ }));

    expect(where()).toHaveTextContent('/threads/3?stage=donors&state=needs_review');
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

  it('имя части в колонке — на ступень меньше, выгрузка лидов — в шапке колонки', async () => {
    // На 1440 колонка списка — 20rem: «Диалоги с рекламодателями» крупным заголовком рвались
    // на две строки, и значок выгрузки уходил за край (снимок 23.10.2026).
    const brand = {
      ...thread(4, 'brand.example.test', 'lead', '2026-09-20T10:00:00+00:00'),
      stage: 'advertisers',
    };
    await openWide('/threads?stage=advertisers', [...THREADS, brand]);

    const column = screen.getByRole('heading', { name: 'Диалоги с рекламодателями' });
    expect(column.tagName).toBe('H4');
    expect(screen.getByRole('button', { name: 'Выгрузить лиды' })).toBeInTheDocument();
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
