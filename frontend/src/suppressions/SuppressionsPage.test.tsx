/**
 * Стоп-лист: что экран показывает и что он не даёт сделать молча.
 *
 * Главное здесь — разница между решением адресата и нашим собственным.
 * Снять отписку можно, но объяснение обязательно: это единственное
 * место сервиса, где человек разрешает написать тому, кто просил
 * не писать.
 */

import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import type { Call } from '../test/server';
import { ADMIN, OPERATOR, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';

const STOP_LIST = {
  rows: [
    {
      id: 1,
      host: 'donor.example.test',
      email: null,
      reason: 'unsubscribed',
      stage: null,
      created_by: 'страница отписки',
      created_at: '2026-09-21T10:00:00+00:00',
      expires_at: null,
      expired: false,
      donor_decision: true,
    },
    {
      id: 2,
      host: null,
      email: 'sales@supplier.example.test',
      reason: 'supplier',
      stage: null,
      created_by: 'анна@site.com',
      created_at: '2026-09-20T10:00:00+00:00',
      expires_at: '2027-09-20T10:00:00+00:00',
      expired: false,
      donor_decision: false,
    },
    {
      id: 3,
      host: 'was.example.test',
      email: null,
      reason: 'manual',
      stage: null,
      created_by: 'анна@site.com',
      created_at: '2025-08-01T10:00:00+00:00',
      expires_at: '2026-08-01T10:00:00+00:00',
      expired: true,
      donor_decision: false,
    },
  ],
  total: 3,
  donor_decisions: 1,
  expired: 1,
};

async function openStopList(routes: Record<string, unknown> = {}, who: unknown = ADMIN) {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    'GET /api/auth/me': { body: who },
    'GET /api/suppressions': { body: STOP_LIST },
    ...(routes as Record<string, never>),
  });
  renderWith(<AppRoutes />, '/suppressions');
  await screen.findByText('donor.example.test');
  return recorded;
}

describe('стоп-лист', () => {
  it('домен и адрес переносятся по швам, а не посреди слова', async () => {
    // Запись UI_RULES 08.10.2026: «Кому не пишем» оставалось без швов; адрес — ещё и
    // после «@» (проверка прода 10.10.2026).
    await openStopList();

    for (const [name, seams] of [
      ['donor.example.test', 2],
      ['sales@supplier.example.test', 3],
    ] as const) {
      const cell = screen.getByText(name);
      expect(cell).toHaveTextContent(name);
      expect(cell.querySelectorAll('wbr')).toHaveLength(seams);
      expect(cell).toHaveClass('cellName');
    }
  });

  it('«Кто завёл» — адрес учётки по швам, а не «qa- / agent@…co / m»', async () => {
    // Проверка прода 10.10.2026: колонка 12rem рвала адрес посреди слова на 1440 и 1280.
    await openStopList();

    const row = screen.getByText('sales@supplier.example.test').closest('tr')!;
    const author = within(row).getByText('анна@site.com');
    expect(author.querySelectorAll('wbr')).toHaveLength(2);
    expect(author).toHaveTextContent('анна@site.com');
  });

  it('решение адресата видно в строке, а не в подсказке', async () => {
    await openStopList();

    // По этому слову человек решает, можно ли снимать запись молча.
    // Ищем в таблице: «поставщик» есть ещё и в выборе причины над ней.
    const table = screen.getByRole('table');
    expect(within(table).getByText('отписался')).toBeInTheDocument();
    expect(within(table).getByText('поставщик')).toBeInTheDocument();
    expect(within(table).getByText('страница отписки')).toBeInTheDocument();
  });

  it('снятие отписки требует причины', async () => {
    await openStopList();
    const user = userEvent.setup();

    await user.click(screen.getAllByRole('button', { name: 'Снять' })[0]!);

    expect(await screen.findByText('Это решение адресата')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Снять запись' })).toBeDisabled();
  });

  it('причина уходит на сервер вместе со снятием', async () => {
    const recorded = await openStopList({
      'POST /api/suppressions/1/remove': { body: { ...STOP_LIST.rows[0] } },
      'GET /api/suppressions': { body: STOP_LIST },
    });
    const user = userEvent.setup();

    await user.click(screen.getAllByRole('button', { name: 'Снять' })[0]!);
    await user.type(await screen.findByLabelText('Почему снимаем'), 'написал «пишите»');
    await user.click(screen.getByRole('button', { name: 'Снять запись' }));

    await screen.findByText(/снова уходят/);
    const sent = recorded.calls.filter((call: Call) => call.method === 'POST');
    expect(sent).toHaveLength(1);
    expect(sent[0]?.body).toEqual({ reason: 'написал «пишите»' });
  });

  it('свою запись снимают без объяснений', async () => {
    await openStopList();
    const user = userEvent.setup();

    await user.click(screen.getAllByRole('button', { name: 'Снять' })[1]!);

    expect(await screen.findByText(/Запись завели мы сами/)).toBeInTheDocument();
    expect(screen.queryByLabelText('Почему снимаем')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Снять запись' })).toBeEnabled();
  });

  it('оператор видит список, но не правит его', async () => {
    await openStopList({}, OPERATOR);

    expect(screen.getByText('donor.example.test')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Снять' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Добавить…' })).not.toBeInTheDocument();
  });

  it('новая запись уходит доменом или адресом', async () => {
    const recorded = await openStopList({
      'POST /api/suppressions': {
        body: { ...STOP_LIST.rows[1], id: 3, new_domain: false, new_address: false, stopped: 1 },
      },
    });
    const user = userEvent.setup();

    // Заводят в окне по кнопке: форма не занимает экран, пока ею не пользуются.
    await user.click(screen.getByRole('button', { name: 'Добавить…' }));
    const dialog = await screen.findByRole('dialog', { name: 'Больше не писать' });
    await user.type(within(dialog).getByLabelText('Домен или адрес'), 'supplier.example.test');
    await user.click(within(dialog).getByRole('button', { name: 'Больше не писать' }));

    await screen.findByText(/в стоп-листе — снято 1 письмо из очереди и добивок/);
    const sent = recorded.calls.filter((call: Call) => call.method === 'POST');
    expect(sent[0]?.body).toEqual({
      target: 'supplier.example.test',
      reason: 'manual',
      expires_at: null,
    });
  });

  it('срок ставится выбором, и по умолчанию его нет', async () => {
    const recorded = await openStopList({
      'POST /api/suppressions': {
        body: { ...STOP_LIST.rows[1], id: 4, new_domain: false, new_address: false, stopped: 0 },
      },
    });
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Добавить…' }));
    const dialog = await screen.findByRole('dialog', { name: 'Больше не писать' });
    await user.type(within(dialog).getByLabelText('Домен или адрес'), 'vendor.example.test');
    // Два значения — переключателем (09.10.2026), а не выпадающим списком.
    await user.click(within(dialog).getByRole('radio', { name: '12 месяцев' }));
    await user.click(within(dialog).getByRole('button', { name: 'Больше не писать' }));

    await screen.findAllByText(/в стоп-листе — /);
    const sent = recorded.calls.filter((call: Call) => call.method === 'POST');
    const body = sent[0]?.body as { expires_at: string | null };
    expect(body.expires_at).not.toBeNull();
  });

  it('поиск и причина сужают список, не спрашивая сервер', async () => {
    const recorded = await openStopList();
    const user = userEvent.setup();
    const before = recorded.calls.length;

    await user.type(screen.getByLabelText('Поиск по домену или адресу'), 'was.');

    expect(screen.getByText('was.example.test')).toBeInTheDocument();
    expect(screen.queryByText('donor.example.test')).not.toBeInTheDocument();
    expect(recorded.calls).toHaveLength(before);
  });

  it('истёкшая запись остаётся на экране и названа истёкшей', async () => {
    await openStopList();

    const row = screen.getByText('was.example.test').closest('tr')!;
    expect(within(row).getByText(/истёк/)).toBeInTheDocument();
    expect(screen.getByText(/истекли и больше не держат/)).toBeInTheDocument();
  });
});

/** Ответ сервера на заведение: запись из базы или новый домен. */
function added(host: string, newDomain: boolean) {
  return {
    ...STOP_LIST.rows[2],
    id: 9,
    host,
    expired: false,
    new_domain: newDomain,
    new_address: false,
    stopped: newDomain ? 0 : 1,
  };
}

/** Ответ сервера на заведение адреса: знаком ли он базе и сколько писем снято. */
function addedAddress(email: string, newAddress: boolean, stopped: number) {
  return {
    ...STOP_LIST.rows[1],
    id: 10,
    email,
    new_domain: false,
    new_address: newAddress,
    stopped,
  };
}

/** Выбор причины над таблицей. По подписи их два: поле и его список. */
function reasonFilter() {
  return screen.getByRole('textbox', { name: 'Причина записи' });
}

/** Выпадающий список Mantine в jsdom остаётся `display: none` — раскладки здесь нет,
 *  и без `hidden` его пункты не видны запросу (так же в `DonorsPage.test.tsx`). */
async function chooseReason(user: ReturnType<typeof userEvent.setup>, reason: string) {
  await user.click(reasonFilter());
  await user.click(await screen.findByRole('option', { name: reason, hidden: true }));
}

async function openAdding(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole('button', { name: 'Добавить…' }));
  return screen.findByRole('dialog', { name: 'Больше не писать' });
}

function posted(recorded: { calls: Call[] }): unknown[] {
  return recorded.calls
    .filter((call) => call.method === 'POST' && call.path === '/api/suppressions')
    .map((call) => call.body);
}

describe('стоп-лист: проверка QA 10.10.2026', () => {
  it('домен, которого в базе не было, назван новым, а не «письма сняты»', async () => {
    // `blog.` донора в зоне, корня которой список суффиксов не знает, — новый домен:
    // экран прежде говорил «в стоп-листе — письма сняты», и донор казался закрытым.
    await openStopList({
      'POST /api/suppressions': { body: added('blog.donor.example.test', true) },
    });
    const user = userEvent.setup();

    const dialog = await openAdding(user);
    await user.type(within(dialog).getByLabelText('Домен или адрес'), 'blog.donor.example.test');
    await user.click(within(dialog).getByRole('button', { name: 'Больше не писать' }));

    expect(await screen.findByText('Записан новый домен')).toBeInTheDocument();
    expect(screen.getByText(/донора с таким доменом нет/)).toBeInTheDocument();
    expect(screen.queryByText(/в стоп-листе — /)).not.toBeInTheDocument();
  });

  it('окно открывается чистым: «поставщик» и «12 месяцев» не достаются следующей записи', async () => {
    const recorded = await openStopList({
      'POST /api/suppressions': { body: added('supplier.example.test', false) },
    });
    const user = userEvent.setup();

    let dialog = await openAdding(user);
    await user.type(within(dialog).getByLabelText('Домен или адрес'), 'supplier.example.test');
    await user.click(within(dialog).getByRole('radio', { name: 'поставщик' }));
    await user.click(within(dialog).getByRole('radio', { name: '12 месяцев' }));
    await user.click(within(dialog).getByRole('button', { name: 'Больше не писать' }));
    await screen.findByText(/в стоп-листе — /);

    dialog = await openAdding(user);
    expect(within(dialog).getByLabelText('Домен или адрес')).toHaveValue('');
    expect(within(dialog).getByRole('radio', { name: 'вручную' })).toBeChecked();
    expect(within(dialog).getByRole('radio', { name: 'навсегда' })).toBeChecked();
    await user.type(within(dialog).getByLabelText('Домен или адрес'), 'other.example.test');
    await user.click(within(dialog).getByRole('button', { name: 'Больше не писать' }));

    await waitFor(() => expect(posted(recorded)).toHaveLength(2));
    expect(posted(recorded)[1]).toEqual({
      target: 'other.example.test',
      reason: 'manual',
      expires_at: null,
    });
  });

  it('отменённое окно тоже открывается чистым', async () => {
    await openStopList();
    const user = userEvent.setup();

    let dialog = await openAdding(user);
    await user.type(within(dialog).getByLabelText('Домен или адрес'), 'draft.example.test');
    await user.click(within(dialog).getByRole('radio', { name: 'поставщик' }));
    await user.click(within(dialog).getByRole('button', { name: 'Отмена' }));

    dialog = await openAdding(user);
    expect(within(dialog).getByLabelText('Домен или адрес')).toHaveValue('');
    expect(within(dialog).getByRole('radio', { name: 'вручную' })).toBeChecked();
  });

  it('Enter в поле — то же, что кнопка', async () => {
    const recorded = await openStopList({
      'POST /api/suppressions': { body: added('supplier.example.test', false) },
    });
    const user = userEvent.setup();

    const dialog = await openAdding(user);
    await user.type(
      within(dialog).getByLabelText('Домен или адрес'),
      'supplier.example.test{Enter}',
    );

    await screen.findByText(/в стоп-листе — /);
    expect(posted(recorded)).toEqual([
      { target: 'supplier.example.test', reason: 'manual', expires_at: null },
    ]);
  });

  it('Enter с коротким вводом не отправляет, как и кнопка', async () => {
    const recorded = await openStopList();
    const user = userEvent.setup();

    const dialog = await openAdding(user);
    await user.type(within(dialog).getByLabelText('Домен или адрес'), 'ab{Enter}');

    expect(within(dialog).getByRole('button', { name: 'Больше не писать' })).toBeDisabled();
    expect(posted(recorded)).toHaveLength(0);
    expect(screen.getByRole('dialog', { name: 'Больше не писать' })).toBeInTheDocument();
  });

  it('фильтр причины не переживает свою причину', async () => {
    let removed = false;
    await openStopList({
      'GET /api/suppressions': () => ({
        body: removed ? { ...STOP_LIST, rows: STOP_LIST.rows.slice(1), total: 2 } : STOP_LIST,
      }),
      'POST /api/suppressions/1/remove': () => {
        removed = true;
        return { body: STOP_LIST.rows[0] };
      },
    });
    const user = userEvent.setup();

    await chooseReason(user, 'отписался');
    await user.click(screen.getByRole('button', { name: 'Снять' }));
    await user.type(await screen.findByLabelText('Почему снимаем'), 'написал «пишите»');
    await user.click(screen.getByRole('button', { name: 'Снять запись' }));

    // Отписок больше нет — и фильтра по ним тоже: виден весь список, а не пустота
    // с «под поиск ничего не попало» при пустом поиске.
    expect(await screen.findByText('was.example.test')).toBeInTheDocument();
    expect(reasonFilter()).toHaveValue('все причины');
    expect(screen.queryByText(/Под поиск/)).not.toBeInTheDocument();
  });

  it('пустая таблица называет причину: поиск или поиск внутри причины', async () => {
    await openStopList();
    const user = userEvent.setup();

    await user.type(screen.getByLabelText('Поиск по домену или адресу'), 'нет-такого');
    expect(
      screen.getByText('Под поиск «нет-такого» ничего не попало — в стоп-листе такого адреса нет.'),
    ).toBeInTheDocument();

    await chooseReason(user, 'поставщик');
    expect(
      screen.getByText(/Под поиск «нет-такого» с причиной «поставщик» ничего не попало/),
    ).toBeInTheDocument();
  });
});

describe('стоп-лист: проверка прода 10.10.2026', () => {
  async function add(response: unknown, target: string) {
    await openStopList({ 'POST /api/suppressions': { body: response } });
    const user = userEvent.setup();
    const dialog = await openAdding(user);
    await user.type(within(dialog).getByLabelText('Домен или адрес'), target);
    await user.click(within(dialog).getByRole('button', { name: 'Больше не писать' }));
  }

  it('незнакомый адрес назван незнакомым, а не «письма сняты с очереди»', async () => {
    // Как у домена: адреса нет ни у одного донора — опечатка выглядела закрытым донором.
    await add(
      addedAddress('nobody@elsewhere.example.test', true, 0),
      'nobody@elsewhere.example.test',
    );

    expect(await screen.findByText('Записан незнакомый адрес')).toBeInTheDocument();
    expect(
      screen.getByText(/nobody@elsewhere\.example\.test нет ни у одного донора или рекламодателя/),
    ).toBeInTheDocument();
    expect(screen.queryByText(/в стоп-листе — /)).not.toBeInTheDocument();
  });

  it('знакомый адрес — сколько писем снято, числом с сервера', async () => {
    await add(addedAddress('editor@donor.example.test', false, 2), 'editor@donor.example.test');

    expect(
      await screen.findByText(
        'editor@donor.example.test в стоп-листе — снято 2 письма из очереди и добивок',
      ),
    ).toBeInTheDocument();
  });

  it('снимать было нечего — так и сказано, а не «письма сняты»', async () => {
    await add(addedAddress('editor@donor.example.test', false, 0), 'editor@donor.example.test');

    expect(
      await screen.findByText(
        'editor@donor.example.test в стоп-листе — в очереди и добивках ему ничего не было',
      ),
    ).toBeInTheDocument();
  });
});
