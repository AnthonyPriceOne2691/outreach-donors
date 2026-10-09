/**
 * Раздел «Продажи»: меню по праву, лиды с фильтрами под колонками, адрес.
 *
 * Проверяется то, ради чего раздел заведён: без права его нет ни в меню,
 * ни по прямому адресу (A4); фильтр уходит на сервер параметром и живёт
 * в адресе — `?state=rejected&reason=duplicate` читается из адреса и пишется
 * в него (A2); список причин для фильтра берётся из ответа сервера, а не из
 * своего перечня; пусто и отказ названы словами.
 */

import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import type { HypothesesView, KbView, LeadCard, LeadsView } from '../api/salesTypes';
import { ADMIN, HOME_ROUTES, OPERATOR, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import type { Answer, Call, Recorded } from '../test/server';
import { readLeadFilters, writeLeadFilters } from './leadFilters';

/** Сколько ждать экран и его запросы: под нагрузкой машины умолчания в 1 с
 *  не хватало экрану отбора — здесь тот же запас. */
const SCREEN_WAIT = { timeout: 5000 };

const HYPOTHESES: HypothesesView = {
  rows: [
    {
      id: 1,
      name: 'сайты EN',
      description: 'редакции и блоги',
      created_at: '2026-10-01T10:00:00+00:00',
      leads: { new: 2, ready: 1, rejected: 2 },
      total: 5,
    },
    {
      id: 2,
      name: 'сервисы RU',
      description: null,
      created_at: '2026-10-02T10:00:00+00:00',
      leads: { new: 0, ready: 0, rejected: 0 },
      total: 0,
    },
  ],
  total: 2,
};

const IVAN: LeadCard = {
  id: 1,
  email: 'ivan@acme.example.test',
  name: 'Иван Петров',
  position: 'редактор',
  company: 'Acme',
  host: 'acme.example.test',
  country: 'de',
  timezone: 'Europe/Berlin',
  language: 'en',
  hypothesis_id: 1,
  hypothesis: 'сайты EN',
  source: 'import',
  status: 'new',
  rejection_reason: null,
  cleaning_note: null,
  verification_status: null,
  created_at: '2026-10-03T10:00:00+00:00',
};

const TWIN: LeadCard = {
  ...IVAN,
  id: 3,
  email: 'twin@acme.example.test',
  name: null,
  position: null,
  company: null,
  status: 'rejected',
  rejection_reason: 'duplicate',
  cleaning_note: 'дубль: адрес уже у лида №1',
};

const REASONS = {
  duplicate: 1,
  stoplist: 0,
  unsubscribed: 0,
  other_direction: 0,
  unusable: 0,
  no_mail: 1,
  undeliverable: 0,
};

function view(rows: LeadCard[], extra: Partial<LeadsView> = {}): LeadsView {
  return {
    rows,
    total: rows.length,
    page: 1,
    limit: 20,
    states: { new: 2, ready: 1, rejected: 2 },
    reasons: REASONS,
    ...extra,
  };
}

const LEADS = 'GET /api/sales/leads';

/** База знаний — ради числа на вкладке: страница спрашивает её вместе со списками. */
const KB: KbView = {
  rows: [],
  total: 3,
  active: 2,
  version: 'kb-000000000000',
  kinds: ['brief', 'service', 'case', 'objection', 'price_policy', 'forbidden', 'cta'],
  limits: { title: 255, text: 20000, tag: 64, tags: 20 },
};

function at(query: string): string {
  return `${LEADS}?${query}`;
}

async function openScreen(
  routes: Record<string, Answer> = {},
  { path = '/sales', ready = 'ivan@acme.example.test', who = ADMIN } = {},
): Promise<Recorded> {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    'GET /api/auth/me': { body: who },
    'GET /api/sales/hypotheses': { body: HYPOTHESES },
    [LEADS]: { body: view([IVAN, TWIN]) },
    'GET /api/sales/kb': { body: KB },
    ...routes,
  });
  renderWith(<AppRoutes />, path);
  await screen.findByText(ready, {}, SCREEN_WAIT);
  return recorded;
}

/** Что спрашивали у списка лидов — строками запроса, по порядку. */
function asked(recorded: Recorded): string[] {
  return recorded.calls
    .filter((call: Call) => call.method === 'GET' && call.path.startsWith('/api/sales/leads'))
    .map((call: Call) => call.path.replace('/api/sales/leads', '').replace(/^\?/, ''));
}

async function choose(field: string, option: RegExp | string) {
  const user = userEvent.setup();
  // Выпадающий список Mantine в jsdom остаётся `display: none` — раскладки
  // здесь нет, и без `hidden` его пункты не видны запросу. Клик настоящий.
  await user.click(screen.getByRole('textbox', { name: field }));
  await user.click(await screen.findByRole('option', { name: option, hidden: true }, SCREEN_WAIT));
}

function rowOf(email: string): HTMLElement {
  const row = screen.getByText(email).closest('tr');
  if (row === null) throw new Error(`строки ${email} нет`);
  return row;
}

describe('продажи: право', () => {
  it('без права «продажи» пункта в меню нет, а прямой адрес объясняет отказ', async () => {
    // A4. Оператору право положено ролью; здесь оно снято поимённо.
    const stranger = { ...OPERATOR, permissions: ['prices', 'run', 'settings', 'view'] };
    localStorage.setItem(TOKEN_KEY, 'пропуск');
    serve({ 'GET /api/auth/me': { body: stranger }, ...HOME_ROUTES });
    renderWith(<AppRoutes />, '/');
    await screen.findByText(/Вошли как/, {}, SCREEN_WAIT);

    expect(screen.queryByText('Продажи')).not.toBeInTheDocument();

    localStorage.setItem(TOKEN_KEY, 'пропуск');
    serve({ 'GET /api/auth/me': { body: stranger } });
    renderWith(<AppRoutes />, '/sales?state=rejected');

    const refusal = await screen.findByText('Раздел недоступен', {}, SCREEN_WAIT);
    expect(refusal.closest('[role="alert"]')).toHaveTextContent('«раздел продаж» не выдано');
    // Списки не спрашивались: без права на экран сервер и не ходят.
    expect(screen.queryByText('ivan@acme.example.test')).not.toBeInTheDocument();
  });

  it('оператор видит раздел: право в роли', async () => {
    await openScreen({}, { who: OPERATOR });

    // Дважды: пункт меню и заголовок экрана.
    expect(screen.getAllByText('Продажи')).toHaveLength(2);
    expect(screen.getByRole('heading', { name: 'Продажи' })).toBeInTheDocument();
  });
});

describe('продажи: лиды', () => {
  it('строка называет лида, компанию, гипотезу, состояние и причину словами', async () => {
    await openScreen();

    const ivan = within(rowOf('ivan@acme.example.test'));
    expect(ivan.getByText('Иван Петров · редактор')).toBeInTheDocument();
    expect(ivan.getByText('Acme')).toBeInTheDocument();
    expect(ivan.getByRole('link', { name: 'acme.example.test' })).toHaveAttribute(
      'href',
      'https://acme.example.test',
    );
    expect(ivan.getByText('сайты EN')).toBeInTheDocument();
    expect(ivan.getByText('Германия')).toBeInTheDocument();
    expect(ivan.getByText('новый')).toBeInTheDocument();

    const twin = within(rowOf('twin@acme.example.test'));
    expect(twin.getByText('отклонён')).toBeInTheDocument();
    expect(twin.getByText('дубль')).toBeInTheDocument();
    // Слова очистки — за «!» (аудит 09.10.2026): проверяет их тест ниже.
    expect(twin.getByRole('button', { name: 'Что сказала очистка' })).toBeInTheDocument();
  });

  it('адрес и домен лида переносятся по швам, а не посреди слова', async () => {
    // Запись UI_RULES 09.10.2026: «Лиды» продаж оставались без швов.
    const email = 'editor@gambling-news-and-analysis.example.test';
    const host = 'gambling-news-and-analysis.example.test';
    await openScreen({ [LEADS]: { body: view([{ ...IVAN, email, host }]) } }, { ready: email });

    const address = screen.getByText(email);
    expect(address).toHaveTextContent(email);
    expect(address.querySelectorAll('wbr')).toHaveLength(2);
    expect(address.closest('td')).toHaveClass('cellName');
    // Имя ссылки — домен целиком: его копируют и ищут по странице, шов — место переноса.
    const link = screen.getByRole('link', { name: host });
    expect(link.querySelectorAll('wbr')).toHaveLength(2);
  });

  it('сводка — по всем лидам, вкладки с числами, и дорога к загрузке', async () => {
    await openScreen();

    expect(screen.getByText('Лиды — 5')).toBeInTheDocument();
    expect(screen.getByText('Гипотезы — 2')).toBeInTheDocument();
    // Число записей базы — со вкладки лидов; у отправителя считать нечего.
    expect(await screen.findByText('База знаний — 3', {}, SCREEN_WAIT)).toBeInTheDocument();
    expect(screen.getByText('Отправитель')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Загрузить базу' })).toHaveAttribute(
      'href',
      '/sales/import',
    );
  });

  it('фильтры стоят под своими колонками', async () => {
    await openScreen();

    const head = screen.getByRole('columnheader', { name: 'Причина' }).closest('thead');
    if (head === null) throw new Error('шапки таблицы нет');
    for (const name of [
      'Поиск по адресу, имени или компании',
      'Гипотеза',
      'Состояние',
      'Причина отказа',
    ]) {
      expect(within(head).getByRole('textbox', { name })).toBeInTheDocument();
    }
  });

  it('A2: состояние и причина читаются из адреса и уходят на сервер теми же именами', async () => {
    const recorded = await openScreen(
      { [at('state=rejected&reason=duplicate')]: { body: view([TWIN]) } },
      { path: '/sales?state=rejected&reason=duplicate', ready: 'twin@acme.example.test' },
    );

    expect(asked(recorded)).toEqual(['state=rejected&reason=duplicate']);
    expect(screen.getByRole('textbox', { name: 'Состояние' })).toHaveValue('отклонён');
    expect(screen.getByRole('textbox', { name: 'Причина отказа' })).toHaveValue('дубль');
    expect(screen.queryByText('ivan@acme.example.test')).not.toBeInTheDocument();
  });

  it('A2: адрес — источник правды: прочитанное пишется обратно тем же адресом', () => {
    // Перезагрузка страницы — это чтение адреса заново: что прочли, то и спросим.
    const address = 'state=rejected&reason=duplicate';
    expect(writeLeadFilters(readLeadFilters(new URLSearchParams(address))).toString()).toBe(
      address,
    );
    const full = 'tab=hypotheses';
    expect(writeLeadFilters(readLeadFilters(new URLSearchParams(full))).toString()).toBe(full);
  });

  it('незнакомое значение в адресе не сужает и не роняет экран', async () => {
    const recorded = await openScreen(
      {},
      { path: '/sales?state=robots&reason=ro%20bots&hypothesis=x&page=-2&search=%20' },
    );

    expect(asked(recorded)).toEqual(['']);
    expect(screen.getByRole('textbox', { name: 'Состояние' })).toHaveValue('все');
  });

  it('причина — только у отклонённых: с другим состоянием из адреса уходит', async () => {
    const recorded = await openScreen(
      { [at('state=new')]: { body: view([IVAN]) } },
      { path: '/sales?state=new&reason=duplicate' },
    );

    expect(asked(recorded)).toEqual(['state=new']);
    expect(screen.queryByRole('textbox', { name: 'Причина отказа' })).toBeNull();
  });

  it('фильтр уходит на сервер параметром и возвращает на первую страницу', async () => {
    const recorded = await openScreen(
      {
        [at('page=3')]: { body: view([IVAN], { total: 45, page: 3 }) },
        [at('state=ready')]: { body: view([IVAN]) },
      },
      { path: '/sales?page=3' },
    );

    await choose('Состояние', 'готов');

    await waitFor(() => expect(asked(recorded).at(-1)).toBe('state=ready'), SCREEN_WAIT);
  });

  it('список причин — из ответа сервера, словами; незнакомый код виден с кодом', async () => {
    await openScreen({
      [LEADS]: { body: view([IVAN, TWIN], { reasons: { ...REASONS, robots: 1 } }) },
    });
    const user = userEvent.setup();

    const field = screen.getByRole('textbox', { name: 'Причина отказа' });
    await user.click(field);

    // Пункты — из списка этого поля: списки соседних фильтров в jsdom тоже
    // смонтированы, и запрос по всему документу собрал бы их все.
    const listId = field.getAttribute('aria-controls');
    if (listId === null) throw new Error('у поля причины нет списка');
    const list = await waitFor(() => {
      const found = document.getElementById(listId);
      if (found === null) throw new Error('список причин ещё не открыт');
      return found;
    }, SCREEN_WAIT);
    const options = within(list)
      .getAllByRole('option', { hidden: true })
      .map((option) => option.textContent);
    expect(options).toEqual([
      'все',
      'дубль',
      'стоп-лист продаж',
      'отписка',
      'домен в работе у другого направления',
      'негодный адрес',
      'домен не принимает почту',
      'адрес не существует',
      'другая причина (robots)',
    ]);
  });

  it('гипотеза фильтруется номером, а называется именем', async () => {
    const recorded = await openScreen({ [at('hypothesis=2')]: { body: view([]) } });

    await choose('Гипотеза', 'сервисы RU');

    await waitFor(() => expect(asked(recorded).at(-1)).toBe('hypothesis=2'), SCREEN_WAIT);
    expect(await screen.findByText('Под фильтр ничего не попало.', {}, SCREEN_WAIT)).toBeTruthy();
    expect(screen.getByText('Условия: гипотеза «сервисы RU». Всего лидов — 5.')).toBeTruthy();
  });

  it('поиск уходит в адрес и на сервер после паузы в наборе', async () => {
    const recorded = await openScreen({ [at('search=acme')]: { body: view([IVAN]) } });
    const user = userEvent.setup();

    await user.type(
      screen.getByRole('textbox', { name: 'Поиск по адресу, имени или компании' }),
      'acme',
    );

    await waitFor(() => expect(asked(recorded).at(-1)).toBe('search=acme'), SCREEN_WAIT);
    expect(asked(recorded).filter((query) => query.includes('search='))).toEqual(['search=acme']);
  });

  it('по двадцать: размер называет сервер, страница уходит номером', async () => {
    const recorded = await openScreen({
      [LEADS]: { body: view([IVAN], { total: 45 }) },
      [at('page=2')]: {
        body: view([{ ...IVAN, id: 9, email: 'second@beta.example.test' }], {
          total: 45,
          page: 2,
        }),
      },
    });
    const user = userEvent.setup();

    const pages = screen.getByRole('navigation', { name: 'Страницы лидов' });
    expect(within(pages).getByRole('button', { name: 'Страница 3' })).toBeInTheDocument();
    await user.click(within(pages).getByRole('button', { name: 'Страница 2' }));

    await screen.findByText('second@beta.example.test', {}, SCREEN_WAIT);
    expect(asked(recorded)).toEqual(['', 'page=2']);
  });

  it('страница из старой ссылки за концом — последняя настоящая, а не пустота', async () => {
    const recorded = await openScreen(
      {
        [at('page=9')]: { body: view([], { total: 45, page: 9 }) },
        [at('page=3')]: { body: view([IVAN], { total: 45, page: 3 }) },
      },
      { path: '/sales?page=9' },
    );

    // 45 лидов по двадцать — три страницы: девятая пуста не потому, что лидов нет.
    expect(asked(recorded)).toEqual(['page=9', 'page=3']);
    expect(screen.queryByText('Под фильтр ничего не попало.')).toBeNull();
  });

  it('отказ сервера на новом фильтре — словами под шапкой таблицы; фильтры стоят', async () => {
    await openScreen({
      [at('state=ready')]: {
        status: 500,
        body: { detail: 'База продаж не ответила — повторите через минуту' },
      },
    });

    await choose('Состояние', 'готов');

    expect(
      await screen.findByText('База продаж не ответила — повторите через минуту', {}, SCREEN_WAIT),
    ).toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: 'Состояние' })).toHaveValue('готов');
  });

  it('лидов нет вовсе — сказано, что делать', async () => {
    const none = { ...HYPOTHESES, rows: HYPOTHESES.rows.map((row) => ({ ...row, total: 0 })) };
    await openScreen(
      {
        'GET /api/sales/hypotheses': { body: none },
        [LEADS]: { body: view([], { states: { new: 0, ready: 0, rejected: 0 } }) },
      },
      { ready: 'Лидов пока нет.' },
    );

    expect(screen.getByText(/Загрузите базу/)).toBeInTheDocument();
  });
});

/** Плитка сводки: число и куда ведёт. Плитка-ссылка — сама `a`, подпись и число — её строки. */
function tile(title: string): { value: string; href: string | null } {
  const label = screen.getByText(title, { selector: '.metricTitle' });
  const card = label.closest('.metricTile');
  if (card === null) throw new Error(`плитки «${title}» нет`);
  return { value: card.children[1]?.textContent ?? '', href: card.getAttribute('href') };
}

describe('продажи: сводка — только на «Лидах» (аудит 09.10.2026)', () => {
  it('на «Лидах» три плитки — новые, готовы, отклонены, — каждая ссылкой в свой фильтр', async () => {
    await openScreen();

    expect(tile('Новые')).toEqual({ value: '2', href: '/sales?state=new' });
    expect(tile('Готовы')).toEqual({ value: '1', href: '/sales?state=ready' });
    expect(tile('Отклонены')).toEqual({ value: '2', href: '/sales?state=rejected' });
    // «Лидов» и «Гипотез» — числа вкладок, плитками они повторяли бы их.
    expect(document.querySelectorAll('.metricTile')).toHaveLength(3);
  });

  it('гипотеза в адресе — плитки считают её лидов и ведут в её фильтр', async () => {
    const both: HypothesesView = {
      ...HYPOTHESES,
      rows: HYPOTHESES.rows.map((row) =>
        row.id === 2 ? { ...row, leads: { new: 3, ready: 4, rejected: 0 }, total: 7 } : row,
      ),
    };
    await openScreen(
      {
        'GET /api/sales/hypotheses': { body: both },
        [at('hypothesis=1')]: { body: view([IVAN, TWIN]) },
      },
      { path: '/sales?hypothesis=1' },
    );

    expect(tile('Новые')).toEqual({ value: '2', href: '/sales?state=new&hypothesis=1' });
    expect(tile('Готовы')).toEqual({ value: '1', href: '/sales?state=ready&hypothesis=1' });
  });

  it('на других вкладках плиток сводки нет; пояснение раздела — в «i» у заголовка', async () => {
    await openScreen({}, { path: '/sales?tab=hypotheses', ready: 'редакции и блоги' });
    const user = userEvent.setup();

    expect(document.querySelectorAll('.metricTile')).toHaveLength(0);
    expect(screen.queryByText(/Лиды попадают сюда/)).toBeNull();

    await user.hover(screen.getByRole('button', { name: 'Откуда лиды и как их чистят' }));

    expect(await screen.findByText(/Лиды попадают сюда/, {}, SCREEN_WAIT)).toBeInTheDocument();
  });
});

/** Вкладка, у которой свой запрос отказал: ждать — заголовок её отказа. */
const REFUSED = { status: 503, body: { detail: 'выдуманный отказ теста' } };
const OTHER_TABS: [string, Record<string, Answer>, string][] = [
  ['kb', {}, 'Что увидит агент'],
  ['sender', { 'GET /api/sales/sender': REFUSED }, 'Отправитель не загрузился'],
  ['chain', { 'GET /api/sales/chain': REFUSED }, 'Цепочка писем не загрузилась'],
  ['queue', { 'GET /api/sales/queue?hypothesis=1': REFUSED }, 'Очередь писем не загрузилась'],
  ['funnel', { 'GET /api/sales/funnel': REFUSED }, 'Воронка не загрузилась'],
];

describe('продажи: «Загрузить базу» — в строке вкладок «Лидов» и «Гипотез» (аудит 09.10.2026)', () => {
  it.each([
    ['/sales', 'ivan@acme.example.test'],
    ['/sales?tab=hypotheses', 'редакции и блоги'],
  ])('%s: кнопка рядом с вкладками, одна', async (path, ready) => {
    await openScreen({}, { path, ready });

    // Соседи в одной строке, а не кнопка в шапке над вкладками.
    const tabs = screen.getByRole('radiogroup', { name: 'Вкладки продаж' });
    const upload = screen.getByRole('link', { name: 'Загрузить базу' });
    expect(upload.parentElement).toBe(tabs.parentElement);
    expect(upload).toHaveAttribute('href', '/sales/import');
  });

  it.each(OTHER_TABS)(
    'вкладка %s: кнопки нет — главная кнопка вкладки одна',
    async (tab, routes, ready) => {
      await openScreen(routes, { path: `/sales?tab=${tab}`, ready });

      expect(screen.queryByRole('link', { name: 'Загрузить базу' })).toBeNull();
    },
  );

  it('лидов нет вовсе — кнопка одна, в строке вкладок, а пустая таблица зовёт к ней словами', async () => {
    const none = { ...HYPOTHESES, rows: HYPOTHESES.rows.map((row) => ({ ...row, total: 0 })) };
    await openScreen(
      {
        'GET /api/sales/hypotheses': { body: none },
        [LEADS]: { body: view([], { states: { new: 0, ready: 0, rejected: 0 } }) },
      },
      { ready: 'Лидов пока нет.' },
    );

    expect(screen.getAllByRole('link', { name: 'Загрузить базу' })).toHaveLength(1);
    expect(screen.getByText(/Загрузите базу/)).toBeInTheDocument();
  });
});

describe('продажи: текст лида не вылезает из ячеек (аудит 09.10.2026)', () => {
  it('слова очистки — за «!»: в ячейке значок причины, слова целиком — по нажатию', async () => {
    await openScreen();
    const user = userEvent.setup();

    const twin = within(rowOf('twin@acme.example.test'));
    expect(twin.getByText('дубль')).toBeInTheDocument();
    expect(twin.queryByText('дубль: адрес уже у лида №1')).toBeNull();

    await user.click(twin.getByRole('button', { name: 'Что сказала очистка' }));

    const told = await screen.findByRole('dialog', { name: 'Что сказала очистка' }, SCREEN_WAIT);
    expect(told).toHaveTextContent('дубль: адрес уже у лида №1');
  });

  it('«!» — только у лида, о котором очистка что-то сказала; «проверка не выполнена» — тоже за ним', async () => {
    const unchecked: LeadCard = {
      ...IVAN,
      id: 4,
      email: 'unchecked@beta.example.test',
      cleaning_note: 'проверка не выполнена: сервис проверки не ответил',
    };
    await openScreen({ [LEADS]: { body: view([IVAN, unchecked]) } });

    const fresh = within(rowOf('ivan@acme.example.test'));
    expect(fresh.queryByRole('button', { name: 'Что сказала очистка' })).toBeNull();
    const waiting = within(rowOf('unchecked@beta.example.test'));
    expect(waiting.getByRole('button', { name: 'Что сказала очистка' })).toBeInTheDocument();
    expect(waiting.queryByText(/проверка не выполнена/)).toBeNull();
  });

  it('компания и гипотеза переносятся по словам; пояс — одной строкой, целиком в подсказке', async () => {
    const zone = 'America/Argentina/ComodRivadavia';
    const company = 'International Association of Independent Online Publishers';
    await openScreen({ [LEADS]: { body: view([{ ...IVAN, timezone: zone, company }]) } });

    const row = within(rowOf('ivan@acme.example.test'));
    expect(row.getByText(company).closest('td')).toHaveClass('wrapCell');
    expect(row.getByText('сайты EN').closest('td')).toHaveClass('wrapCell');
    const belt = row.getByText(zone);
    expect(belt).toHaveAttribute('title', zone);
    expect(belt).toHaveAttribute('data-line-clamp');
  });
});

describe('продажи: пустая таблица на телефоне (аудит 09.10.2026)', () => {
  it('лидов нет вовсе — ни плиток, ни шапки с фильтрами: только объяснение, не в таблице', async () => {
    const none = { ...HYPOTHESES, rows: HYPOTHESES.rows.map((row) => ({ ...row, total: 0 })) };
    await openScreen(
      {
        'GET /api/sales/hypotheses': { body: none },
        [LEADS]: { body: view([], { states: { new: 0, ready: 0, rejected: 0 } }) },
      },
      { ready: 'Лидов пока нет.' },
    );

    expect(screen.getByText('Лидов пока нет.').closest('table')).toBeNull();
    expect(screen.queryByRole('columnheader', { name: 'Лид' })).toBeNull();
    expect(
      screen.queryByRole('textbox', { name: 'Поиск по адресу, имени или компании' }),
    ).toBeNull();
    expect(document.querySelectorAll('.metricTile')).toHaveLength(0);
  });

  it('под фильтром пусто — объяснение под таблицей, а не строкой шириной в таблицу; фильтры стоят', async () => {
    await openScreen(
      { [at('hypothesis=2')]: { body: view([]) } },
      { path: '/sales?hypothesis=2', ready: 'Под фильтр ничего не попало.' },
    );

    // Строка во всю ширину таблицы в 1 224 px на телефоне уезжала в прокрутку и читалась обрубком.
    expect(screen.getByText('Под фильтр ничего не попало.').closest('table')).toBeNull();
    const head = screen.getByRole('columnheader', { name: 'Причина' }).closest('thead');
    if (head === null) throw new Error('шапки таблицы нет');
    expect(within(head).getByRole('textbox', { name: 'Гипотеза' })).toHaveValue('сервисы RU');
    expect(screen.getByRole('button', { name: 'Сбросить фильтры' })).toBeInTheDocument();
  });
});

describe('продажи: гипотезы', () => {
  it('вкладка показывает гипотезы со счётчиками, числа ведут к лидам', async () => {
    await openScreen({}, { path: '/sales?tab=hypotheses', ready: 'редакции и блоги' });

    const row = within(rowOf('сайты EN'));
    expect(row.getByRole('link', { name: '1' })).toHaveAttribute(
      'href',
      '/sales?state=ready&hypothesis=1',
    );
    expect(row.getByText('5')).toBeInTheDocument();
    // Список лидов на этой вкладке не спрашивается: таблицы лидов здесь нет.
    expect(screen.queryByText('ivan@acme.example.test')).not.toBeInTheDocument();
  });

  it('гипотез нет — сказано, как завести', async () => {
    await openScreen(
      { 'GET /api/sales/hypotheses': { body: { rows: [], total: 0 } } },
      { path: '/sales?tab=hypotheses', ready: 'Гипотез пока нет.' },
    );

    expect(screen.getByText(/sales-hypothesis-add/)).toBeInTheDocument();
  });
});
