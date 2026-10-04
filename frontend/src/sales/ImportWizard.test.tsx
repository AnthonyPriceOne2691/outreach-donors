/**
 * Мастер загрузки базы: файл или ссылка → колонки → отчёт → загрузка.
 *
 * Главное — A1: после загрузки на экране «загружено 97, отклонено 3» и причины
 * словами, сгруппированные по строкам. И то, что уходит на сервер: источник —
 * формой, оба раза; сопоставление руками — JSON «поле → колонка»; гипотеза —
 * номером. Сервер между шагами ничего не помнит — мастер шлёт всё сам.
 */

import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { AppRoutes } from '../App';
import { ApiError, AuthError, DeniedError } from '../api/client';
import { previewImport } from '../api/sales';
import type { HypothesesView, IntakeView } from '../api/salesTypes';
import { ADMIN, OPERATOR, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import type { Answer } from '../test/server';
import { groupRejections, withField } from './importMapping';

const SCREEN_WAIT = { timeout: 5000 };

const HYPOTHESES: HypothesesView = {
  rows: [
    {
      id: 1,
      name: 'сайты EN',
      description: null,
      created_at: '2026-10-01T10:00:00+00:00',
      leads: { new: 0, ready: 0, rejected: 0 },
      total: 0,
    },
  ],
  total: 1,
};

/** Предпросмотр файла на сто строк: три плохие, две с замечанием. */
const PREVIEW: IntakeView = {
  source: 'leads.csv',
  header: true,
  columns: ['Почта', 'Имя', 'Компания', 'Сайт'],
  sample: [
    ['ivan@acme.example.test', 'Иван Петров', 'Acme', 'acme.example.test'],
    ['maria@beta.example.test', 'Мария', 'Beta', ''],
  ],
  mapping: { email: 0, name: 1, company: 2, website: 3 },
  needs_mapping: false,
  rows: 100,
  accepted: 97,
  rejected: 3,
  leads: [
    {
      line: 2,
      email: 'ivan@acme.example.test',
      domain: 'acme.example.test',
      name: 'Иван Петров',
      position: null,
      company: 'Acme',
      country: null,
      timezone: null,
      language: null,
    },
  ],
  problems: [
    { line: 7, reason: 'нет адреса', cell: '', loaded: false },
    { line: 12, reason: 'нет адреса', cell: '', loaded: false },
    { line: 20, reason: 'не адрес почты', cell: 'ivan at acme', loaded: false },
    {
      line: 3,
      reason: 'сайт не разобран — домен компании beta.example.test взят из почты',
      cell: 'beta',
      loaded: true,
    },
    { line: 40, reason: 'страна не распознана: ждём код или название', cell: '??', loaded: true },
  ],
  loaded: null,
};

const PREVIEW_ROUTE = 'POST /api/sales/import/preview';
const LOAD_ROUTE = 'POST /api/sales/import';

/** Адрес вызова `fetch` строкой, какой бы формы он ни пришёл. */
function urlOf(input: RequestInfo | URL): string {
  if (typeof input === 'string') return input;
  return input instanceof URL ? input.href : input.url;
}

/** Формы, ушедшие на адрес, — по порядку: заглушка сети тело формы не разбирает. */
function formsSentTo(path: string): FormData[] {
  return vi
    .mocked(globalThis.fetch)
    .mock.calls.filter(([input]) => urlOf(input).endsWith(path))
    .map(([, init]) => init?.body)
    .filter((body): body is FormData => body instanceof FormData);
}

/** Строковое поле формы. Файл на его месте — ошибка теста, а не «[object File]». */
function textOf(form: FormData | undefined, name: string): string | null {
  const value = form?.get(name) ?? null;
  if (value !== null && typeof value !== 'string') {
    throw new Error(`поле «${name}» формы — не строка`);
  }
  return value;
}

function fileOf(form: FormData | undefined, name: string): File {
  const value = form?.get(name);
  if (!(value instanceof File)) throw new Error(`в форме нет файла «${name}»`);
  return value;
}

/** Сопоставление, ушедшее на сервер, — разобранным JSON. */
function mappingSent(form: FormData | undefined): unknown {
  return JSON.parse(textOf(form, 'mapping') ?? 'null') as unknown;
}

async function openWizard(routes: Record<string, Answer> = {}) {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  serve({
    'GET /api/auth/me': { body: ADMIN },
    'GET /api/sales/hypotheses': { body: HYPOTHESES },
    [PREVIEW_ROUTE]: { body: PREVIEW },
    [LOAD_ROUTE]: { body: { ...PREVIEW, loaded: 97 } },
    ...routes,
  });
  renderWith(<AppRoutes />, '/sales/import');
  await screen.findByRole('heading', { name: 'Загрузка базы' }, SCREEN_WAIT);
  return userEvent.setup();
}

type User = ReturnType<typeof userEvent.setup>;

/** Пункт из списка именно этого поля: списки соседних полей в jsdom тоже
 *  смонтированы, и запрос по всему документу нашёл бы «страну» у каждой колонки. */
async function pick(user: User, field: HTMLElement, option: string) {
  await user.click(field);
  const listId = field.getAttribute('aria-controls');
  if (listId === null) throw new Error('у поля нет списка');
  const list = await waitFor(() => {
    const found = document.getElementById(listId);
    if (found === null) throw new Error('список ещё не открыт');
    return found;
  }, SCREEN_WAIT);
  await user.click(within(list).getByRole('option', { name: option, hidden: true }));
}

async function pickHypothesis(user: User) {
  await pick(user, screen.getByRole('textbox', { name: 'Гипотеза' }), 'сайты EN');
}

function fileInput(): HTMLInputElement {
  const input = document.querySelector('input[type="file"]');
  if (!(input instanceof HTMLInputElement)) throw new Error('поля файла нет');
  return input;
}

async function readFile(user: User) {
  await pickHypothesis(user);
  await user.upload(fileInput(), new File(['Почта;Имя\n'], 'leads.csv', { type: 'text/csv' }));
  await user.click(screen.getByRole('button', { name: 'Прочитать' }));
  await screen.findByText('Колонки файла и поля лида', {}, SCREEN_WAIT);
}

describe('мастер загрузки', () => {
  it('A1: файл с тремя плохими строками — «загружено 97, отклонено 3» и причины словами', async () => {
    const user = await openWizard();

    await readFile(user);
    // Источник ушёл формой, без сопоставления: его угадывает сервер.
    const [previewForm] = formsSentTo('/api/sales/import/preview');
    expect(fileOf(previewForm, 'file').name).toBe('leads.csv');
    expect(textOf(previewForm, 'mapping')).toBeNull();

    await user.click(screen.getByRole('button', { name: 'К отчёту' }));
    expect(await screen.findByText('Станут лидами', {}, SCREEN_WAIT)).toBeInTheDocument();
    // Отчёт целиком: отклонённые строки и замечания названы по-разному.
    const report = screen.getByRole('table', { name: 'Отчёт по строкам' });
    expect(within(report).getAllByText('строка отклонена')).toHaveLength(3);
    expect(within(report).getAllByText('загружен с замечанием')).toHaveLength(2);
    expect(within(report).getByText('ivan at acme')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Загрузить 97 лидов' }));

    expect(
      await screen.findByText('Загружено 97, отклонено 3.', {}, SCREEN_WAIT),
    ).toBeInTheDocument();
    expect(screen.getByText('нет адреса — 2')).toBeInTheDocument();
    expect(screen.getByText('не адрес почты — 1')).toBeInTheDocument();
    expect(screen.getByText(/строки 7, 12/)).toBeInTheDocument();
    // Загрузка ушла с гипотезой и с тем сопоставлением, что показывалось человеку.
    const [loadForm] = formsSentTo('/api/sales/import');
    expect(textOf(loadForm, 'hypothesis_id')).toBe('1');
    expect(mappingSent(loadForm)).toEqual(PREVIEW.mapping);
    expect(fileOf(loadForm, 'file').name).toBe('leads.csv');
    expect(screen.getByRole('link', { name: 'К лидам' })).toHaveAttribute(
      'href',
      '/sales?hypothesis=1',
    );
  });

  it('правка сопоставления руками уходит на сервер JSON «поле → колонка»', async () => {
    const user = await openWizard();
    await readFile(user);

    const rows = screen.getAllByRole('row');
    const site = rows.find((row) => within(row).queryByText('Сайт') !== null);
    if (site === undefined) throw new Error('строки колонки «Сайт» нет');
    const field = within(site).getByRole('textbox', { name: 'Поле для колонки Сайт' });
    expect(field).toHaveValue('сайт компании');
    await pick(user, field, 'страна');

    await waitFor(
      () => expect(formsSentTo('/api/sales/import/preview')).toHaveLength(2),
      SCREEN_WAIT,
    );
    const [, again] = formsSentTo('/api/sales/import/preview');
    expect(mappingSent(again)).toEqual({ email: 0, name: 1, company: 2, country: 3 });
    expect(textOf(again, 'header')).toBe('true');
  });

  it('без колонки почты дальше не пускает и говорит, что сделать', async () => {
    const user = await openWizard({
      [PREVIEW_ROUTE]: {
        body: {
          ...PREVIEW,
          header: false,
          columns: ['колонка 1', 'колонка 2'],
          mapping: {},
          needs_mapping: true,
          accepted: 0,
          rejected: 0,
          leads: [],
          problems: [],
        },
      },
    });
    await readFile(user);

    expect(screen.getByText(/Колонка почты не найдена/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'К отчёту' })).toBeDisabled();
    expect(screen.getByRole('switch', { name: 'Первая строка — заголовок' })).not.toBeChecked();
  });

  it('ссылка на таблицу уходит формой, а отказ сервера показан целиком', async () => {
    const closed = 'таблица не открыта по ссылке — откройте доступ или загрузите CSV';
    const user = await openWizard({ [PREVIEW_ROUTE]: { status: 400, body: { detail: closed } } });
    await pickHypothesis(user);

    await user.click(screen.getByRole('radio', { name: 'Ссылка на Google-таблицу' }));
    await user.type(
      screen.getByRole('textbox', { name: 'Ссылка на Google-таблицу' }),
      'https://docs.google.com/spreadsheets/d/abc/edit',
    );
    await user.click(screen.getByRole('button', { name: 'Прочитать' }));

    expect(await screen.findByText(closed, {}, SCREEN_WAIT)).toBeInTheDocument();
    const [form] = formsSentTo('/api/sales/import/preview');
    expect(textOf(form, 'link')).toBe('https://docs.google.com/spreadsheets/d/abc/edit');
    expect(form?.get('file')).toBeNull();
    // Мастер остался на шаге источника: ссылку поправляют тут же.
    expect(screen.getByRole('button', { name: 'Прочитать' })).toBeInTheDocument();
  });

  it('переключатель заголовка перечитывает файл с тем же сопоставлением', async () => {
    const user = await openWizard();
    await readFile(user);

    const toggle = screen.getByRole('switch', { name: 'Первая строка — заголовок' });
    expect(toggle).toBeChecked();
    await user.click(toggle);

    await waitFor(
      () => expect(formsSentTo('/api/sales/import/preview')).toHaveLength(2),
      SCREEN_WAIT,
    );
    const [, again] = formsSentTo('/api/sales/import/preview');
    expect(textOf(again, 'header')).toBe('false');
    expect(mappingSent(again)).toEqual(PREVIEW.mapping);
  });

  it('никто не станет лидом — загружать нечего, и кнопка это говорит', async () => {
    const user = await openWizard({
      [PREVIEW_ROUTE]: {
        body: { ...PREVIEW, accepted: 0, rejected: 100, leads: [] },
      },
    });
    await readFile(user);
    await user.click(screen.getByRole('button', { name: 'К отчёту' }));

    expect(
      await screen.findByRole('button', { name: 'Загрузить 0 лидов' }, SCREEN_WAIT),
    ).toBeDisabled();
  });

  it('отказ сервера на загрузке — словами, отчёт и кнопка остаются', async () => {
    const gone = 'гипотезы №1 нет — заведите её командой sales-hypothesis-add';
    const user = await openWizard({ [LOAD_ROUTE]: { status: 404, body: { detail: gone } } });
    await readFile(user);
    await user.click(screen.getByRole('button', { name: 'К отчёту' }));
    await user.click(
      await screen.findByRole('button', { name: 'Загрузить 97 лидов' }, SCREEN_WAIT),
    );

    expect(await screen.findByText(gone, {}, SCREEN_WAIT)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Загрузить 97 лидов' })).toBeInTheDocument();
    expect(screen.queryByText(/Загружено/)).not.toBeInTheDocument();
  });

  it('«Загрузить ещё» возвращает к источнику без файла, гипотеза остаётся', async () => {
    const user = await openWizard();
    await readFile(user);
    await user.click(screen.getByRole('button', { name: 'К отчёту' }));
    await user.click(
      await screen.findByRole('button', { name: 'Загрузить 97 лидов' }, SCREEN_WAIT),
    );
    await screen.findByText('Загружено 97, отклонено 3.', {}, SCREEN_WAIT);

    await user.click(screen.getByRole('button', { name: 'Загрузить ещё' }));

    expect(screen.getByRole('button', { name: 'Прочитать' })).toBeDisabled();
    expect(screen.getByText('Выберите файл.')).toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: 'Гипотеза' })).toHaveValue('сайты EN');
  });

  it('A4: без права «продажи» адрес мастера — отказ словами, к продажам не ходит', async () => {
    const stranger = { ...OPERATOR, permissions: ['prices', 'run', 'settings', 'view'] };
    localStorage.setItem(TOKEN_KEY, 'пропуск');
    const recorded = serve({ 'GET /api/auth/me': { body: stranger } });
    renderWith(<AppRoutes />, '/sales/import');

    const refusal = await screen.findByText('Раздел недоступен', {}, SCREEN_WAIT);
    expect(refusal.closest('[role="alert"]')).toHaveTextContent('«раздел продаж» не выдано');
    expect(recorded.calls.map((call) => call.path)).toEqual(['/api/auth/me']);
  });
});

describe('сопоставление и отчёт — чистые правила', () => {
  it('поле у колонки одно: прежняя колонка этого поля освобождается', () => {
    const mapping = { email: 0, name: 1 } as const;
    expect(withField(mapping, 2, 'name')).toEqual({ email: 0, name: 2 });
    expect(withField(mapping, 1, null)).toEqual({ email: 0 });
    expect(withField(mapping, 1, 'company')).toEqual({ email: 0, company: 1 });
  });

  it('отказы группируются по причине, частые первыми, со строками файла', () => {
    expect(groupRejections(PREVIEW.problems)).toEqual([
      { reason: 'нет адреса', lines: [7, 12] },
      { reason: 'не адрес почты', lines: [20] },
    ]);
  });
});

describe('отказ сервера на форму — как у остальных запросов', () => {
  const SOURCE = { file: new File(['Почта\n'], 'leads.csv', { type: 'text/csv' }), link: '' };

  /** Чем кончилась отправка формы: отказ — исключением. */
  async function refusalOn(answer: Answer): Promise<unknown> {
    localStorage.setItem(TOKEN_KEY, 'пропуск');
    serve({ [PREVIEW_ROUTE]: answer });
    return previewImport(SOURCE, {}).then(
      () => null,
      (failure: unknown) => failure,
    );
  }

  it('отказ разбора формы списком — первой причиной, а не схемой', async () => {
    const failure = await refusalOn({
      status: 422,
      body: { detail: [{ msg: 'нет ни файла, ни ссылки', loc: ['body', 'file'] }] },
    });

    expect(failure).toBeInstanceOf(ApiError);
    expect(failure).toHaveProperty('message', 'нет ни файла, ни ссылки');
    expect(failure).toHaveProperty('status', 422);
  });

  it('без текста отказа — код ответа, а не пустота', async () => {
    expect(await refusalOn({ status: 422, body: { detail: [] } })).toHaveProperty(
      'message',
      'Сервер ответил 422',
    );
    expect(await refusalOn({ status: 502, raw: '<html>шлюз</html>' })).toHaveProperty(
      'message',
      'Сервер ответил 502',
    );
  });

  it('401 сбрасывает пропуск, 403 называет действие', async () => {
    const expired = await refusalOn({ status: 401, body: { detail: 'Пропуск просрочен' } });
    expect(expired).toBeInstanceOf(AuthError);
    expect(localStorage.getItem(TOKEN_KEY)).toBeNull();

    const denied = await refusalOn({
      status: 403,
      body: { detail: 'Действие «sales» недоступно этой учётке' },
    });
    expect(denied).toBeInstanceOf(DeniedError);
    expect(denied).toHaveProperty('message', 'Действие «sales» недоступно этой учётке');
  });
});
