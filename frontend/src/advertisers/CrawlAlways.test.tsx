/**
 * «Обход 24/7» на вкладке «Обход доноров» (17.11.2026).
 *
 * Проверяется то, чем блок может подвести: запуститься без вопроса, назвавшего, что 24/7 сделает и
 * что ночь остаётся ночной; ставиться на паузу дольше одного нажатия; отправить порог за границей
 * или не тот, что в поле; посчитать «пометку получат N из M» не по набранному порогу; дать кнопки
 * тому, у кого нет права запуска; промолчать, что рубильник выключен, что ответа нет или почему
 * включённый не идёт; не перечитываться, пока идёт.
 */

import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import type { AlwaysView } from '../api/crawlAlways';
import type { Me } from '../api/types';
import { formatDateTime } from '../format';
import { ADMIN, ALWAYS_OFF, OPERATOR, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import { CrawlAlways } from './CrawlAlways';
import { RECRAWL_LABEL, THRESHOLD_NAME } from './CrawlAlwaysSettings';
import {
  alwaysHint,
  changeTitle,
  doneWords,
  markedCount,
  markWords,
  PART_PAGES,
  partPages,
  percentText,
  startWarning,
  UNAVAILABLE,
  waitingWords,
  watched,
} from './crawlAlwaysWords';

const URL = '/api/crawls/always';

/** Доли сайтов, прошедших первые 10 000, — как в договоре. */
const YIELDS = [0.0, 0.0, 0.04, 0.3, 1.2, 6.5];

const OFF: AlwaysView = { ...ALWAYS_OFF, yields: YIELDS };

const RUNNING: AlwaysView = {
  ...OFF,
  enabled: true,
  set_at: '2026-11-17T12:40:00Z',
  set_by: 'user@example.test',
  counts: { new: 3, first: 120, deeper: 40, weak: 90, exhausted: 9, running: 12, total: 172 },
  pages: 1_250_000,
  words: 'идёт: новые — 3 · до 10 000 — 120 · дальше — 40; обходится сейчас 12',
  details: ['Новые — до 1 000 страниц, потом по 10 000 каждого.'],
};

/** Без права запуска: смотреть можно, менять — нет. */
const WATCHER: Me = { ...OPERATOR, permissions: ['advertisers', 'view'] };

/** Текст без неразрывных пробелов разрядов и единиц. */
function plain(text: string | null): string {
  return (text ?? '').replace(/[\u00a0\u202f]/g, ' ');
}

/** Блок — как его ставит вкладка: право запуска — от вошедшего (`CrawlTab`: `can('run')`). */
function open(answer: AlwaysView, who: Me = ADMIN, routes: Record<string, unknown> = {}) {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    'GET /api/auth/me': { body: who },
    [`GET ${URL}`]: { body: answer },
    ...(routes as Record<string, never>),
  });
  renderWith(<CrawlAlways mayRun={who.permissions.includes('run')} />);
  return recorded;
}

function posted(recorded: ReturnType<typeof serve>): unknown[] {
  return recorded.calls.filter((call) => call.method === 'POST').map((call) => call.body);
}

/** Пояснение поля — текстом, как его читает читалка: по `aria-describedby`. */
function described(field: HTMLElement): string {
  const ids = (field.getAttribute('aria-describedby') ?? '').split(/\s+/).filter(Boolean);
  return plain(ids.map((id) => document.getElementById(id)?.textContent ?? '').join(' '));
}

function block(): Promise<HTMLElement> {
  return screen.findByRole('group', { name: 'Обход 24/7' });
}

describe('обход 24/7: состояния блока', () => {
  it('выключен — строка сервера тихо, «Запустить 24/7…», паузы нет', async () => {
    open(OFF);

    const shown = await block();
    expect(plain(shown.textContent)).toContain('Обход 24/7 на паузе');
    expect(within(shown).getByRole('button', { name: 'Запустить 24/7…' })).toBeEnabled();
    expect(within(shown).queryByRole('button', { name: 'Пауза' })).toBeNull();
    // Строки «Сейчас не идёт» у выключенного нет: он не ждёт, его поставили на паузу.
    expect(within(shown).queryByText(/Сейчас не идёт/)).toBeNull();
  });

  it('включён и идёт — слова сервера, «Пауза», подробности — в «i»', async () => {
    open(RUNNING);
    const user = userEvent.setup();

    const shown = await block();
    expect(plain(shown.textContent)).toContain(
      'Обход 24/7 идёт: новые — 3 · до 10 000 — 120 · дальше — 40; обходится сейчас 12',
    );
    expect(within(shown).getByRole('button', { name: 'Пауза' })).toBeEnabled();
    expect(within(shown).queryByRole('button', { name: 'Запустить 24/7…' })).toBeNull();

    await user.click(within(shown).getByRole('button', { name: 'Подробнее об обходе 24/7' }));
    expect(
      await screen.findByText('Новые — до 1 000 страниц, потом по 10 000 каждого.'),
    ).toBeInTheDocument();
    expect(screen.getByText('Пройдено обходом 24/7: 1 250 000 страниц.')).toBeInTheDocument();
    expect(
      screen.getByText(
        `Последняя смена настроек: ${formatDateTime('2026-11-17T12:40:00Z')}, user@example.test.`,
      ),
    ).toBeInTheDocument();
  });

  it('включён, но не идёт — почему, строкой под итогом', async () => {
    open({ ...RUNNING, waiting_for: 'ночной обход — продолжится после него' });

    const shown = await block();
    expect(
      within(shown).getByText('Сейчас не идёт: ночной обход — продолжится после него.'),
    ).toBeInTheDocument();
  });

  it('пройдено всё — когда, строкой', async () => {
    const done = '2026-11-20T03:15:00Z';
    open({
      ...RUNNING,
      waiting_for: 'новых страниц нет — все сайты пройдены',
      done_at: done,
    });

    const shown = await block();
    expect(
      within(shown).getByText(`Все сайты пройдены до конца: ${formatDateTime(done)}.`),
    ).toBeInTheDocument();
    expect(
      within(shown).getByText('Сейчас не идёт: новых страниц нет — все сайты пройдены.'),
    ).toBeInTheDocument();
  });

  it('рубильник окружения выключен — одна строка, без кнопок и поля', async () => {
    open({ ...OFF, available: false });

    expect(await screen.findByText(UNAVAILABLE)).toBeInTheDocument();
    expect(screen.queryByRole('group', { name: 'Обход 24/7' })).toBeNull();
    expect(screen.queryByRole('button')).toBeNull();
    expect(screen.queryByRole('textbox')).toBeNull();
  });

  it('ответа нет — строка отказа, а не пустое место', async () => {
    open(OFF, ADMIN, { [`GET ${URL}`]: { status: 503, body: { detail: 'База не отвечает.' } } });

    expect(await screen.findByText(/^Обход 24\/7 не загрузился: /)).toHaveTextContent(
      'База не отвечает.',
    );
    expect(screen.queryByRole('button')).toBeNull();
  });

  it('без права запуска — строки без кнопок, поля и переключателя', async () => {
    open(RUNNING, WATCHER);

    const shown = await block();
    expect(plain(shown.textContent)).toContain('Обход 24/7 идёт:');
    expect(within(shown).queryByRole('button', { name: /Пауза|Запустить/ })).toBeNull();
    expect(within(shown).queryByRole('textbox')).toBeNull();
    expect(within(shown).queryByRole('switch')).toBeNull();
    expect(
      within(shown).getByText((_, node) =>
        plain(node?.textContent ?? '').startsWith(
          'Мало рекламодателей: меньше 0,1% страниц с платной ссылкой на 10 000 — при этом ' +
            'пороге пометку получат 3 из 6 сайтов.',
        ),
      ),
    ).toBeInTheDocument();
    expect(
      within(shown).getByText(`${RECRAWL_LABEL}: выключен.`, { exact: false }),
    ).toBeInTheDocument();
  });
});

describe('обход 24/7: запуск и пауза', () => {
  it('запуск — только после вопроса у кнопки, который говорит, что 24/7 сделает', async () => {
    const recorded = open(OFF, ADMIN, { [`POST ${URL}`]: { body: RUNNING } });
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: 'Запустить 24/7…' }));

    const question = await screen.findByRole('dialog');
    expect(plain(question.textContent)).toContain('до 1 000 страниц');
    expect(plain(question.textContent)).toContain('по 10 000 страниц каждого сайта');
    // Часы ночи — настройка сервера (`CRAWL_NIGHT_WINDOW_SEC`, с 20.11 — четыре часа): не зашиты.
    expect(question).toHaveTextContent('Ночной обход с полуночи по Москве остаётся как есть.');
    expect(question.textContent).not.toMatch(/0[47]:00/);
    expect(question).toHaveTextContent('Пауза — одним нажатием.');
    expect(posted(recorded)).toEqual([]);

    await user.click(within(question).getByRole('button', { name: 'Запустить' }));

    await waitFor(() => expect(posted(recorded)).toEqual([{ enabled: true }]));
    expect(await screen.findByRole('button', { name: 'Пауза' })).toBeInTheDocument();
  });

  it('передумали — «Отмена», и на сервер ничего не ушло', async () => {
    const recorded = open(OFF);
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: 'Запустить 24/7…' }));
    await user.click(
      within(await screen.findByRole('dialog')).getByRole('button', { name: 'Отмена' }),
    );

    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(posted(recorded)).toEqual([]);
  });

  it('пауза — одним нажатием, без вопроса', async () => {
    const recorded = open(RUNNING, ADMIN, { [`POST ${URL}`]: { body: OFF } });
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: 'Пауза' }));

    await waitFor(() => expect(posted(recorded)).toEqual([{ enabled: false }]));
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(await screen.findByRole('button', { name: 'Запустить 24/7…' })).toBeInTheDocument();
  });

  it('отказ сервера — словами, с тем, что не вышло', async () => {
    const refusal = 'Обход 24/7 выключен настройкой сервиса: рубильник окружения снят.';
    open(OFF, ADMIN, { [`POST ${URL}`]: { status: 409, body: { detail: refusal } } });
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: 'Запустить 24/7…' }));
    await user.click(
      within(await screen.findByRole('dialog')).getByRole('button', { name: 'Запустить' }),
    );

    expect(await screen.findByText(refusal)).toBeInTheDocument();
    expect(screen.getByText('Обход 24/7 не запущен')).toBeInTheDocument();
  });
});

describe('обход 24/7: порог «мало рекламодателей»', () => {
  it('сколько получат пометку — по набранному порогу, сразу, без запроса', async () => {
    const recorded = open(OFF);
    const user = userEvent.setup();
    const field = await screen.findByRole('textbox', { name: THRESHOLD_NAME });

    expect(field).toHaveValue('0,1');
    expect(described(field)).toBe('при этом пороге пометку получат 3 из 6 сайтов');
    await user.clear(field);
    await user.type(field, '0,5');

    expect(described(field)).toBe('при этом пороге пометку получат 4 из 6 сайтов');
    await user.clear(field);
    await user.type(field, '0');
    expect(described(field)).toBe('при этом пороге пометку получат 0 из 6 сайтов');
    expect(recorded.calls.filter((call) => call.path === URL)).toHaveLength(1);
  });

  it('«Сохранить» — только когда набрано другое; уходит число из поля, запятая — как точка', async () => {
    const recorded = open(OFF, ADMIN, {
      [`POST ${URL}`]: { body: { ...OFF, threshold_pct: 0.35 } },
    });
    const user = userEvent.setup();
    const field = await screen.findByRole('textbox', { name: THRESHOLD_NAME });

    expect(screen.queryByRole('button', { name: 'Сохранить' })).toBeNull();
    await user.clear(field);
    await user.type(field, '0,35');
    await user.click(screen.getByRole('button', { name: 'Сохранить' }));

    await waitFor(() => expect(posted(recorded)).toEqual([{ threshold_pct: 0.35 }]));
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Сохранить' })).toBeNull());
    expect(field).toHaveValue('0,35');
  });

  it.each([
    ['150', 'Допустимо от 0 до 100'],
    ['-1', 'Допустимо от 0 до 100'],
    ['0,125', 'Не больше двух знаков после запятой'],
    ['мало', 'Только число, например 99,50'],
    ['', 'Впишите число'],
  ])('«%s» — отказ на месте строки, и сохранить нельзя', async (typed, refusal) => {
    const recorded = open(OFF);
    const user = userEvent.setup();
    const field = await screen.findByRole('textbox', { name: THRESHOLD_NAME });

    await user.clear(field);
    if (typed !== '') await user.type(field, typed);

    expect(described(field)).toBe(refusal);
    expect(field).toHaveAttribute('aria-invalid', 'true');
    expect(screen.getByRole('button', { name: 'Сохранить' })).toBeDisabled();
    expect(posted(recorded)).toEqual([]);
  });

  it('порог сменили на сервере, пока поле не трогали, — поле идёт за ним', async () => {
    const recorded = open(RUNNING, ADMIN, {
      [`POST ${URL}`]: { body: { ...RUNNING, threshold_pct: 2, enabled: false } },
    });
    const user = userEvent.setup();
    const field = await screen.findByRole('textbox', { name: THRESHOLD_NAME });

    // Ответ на паузу несёт и порог, сменённый коллегой: несохранённого набора нет — поле за
    // сервером.
    await user.click(screen.getByRole('button', { name: 'Пауза' }));

    await waitFor(() => expect(field).toHaveValue('2'));
    expect(posted(recorded)).toEqual([{ enabled: false }]);
  });
});

describe('обход 24/7: часть — числом сервера', () => {
  it('во фразе у поля и в вопросе у кнопки — часть из ответа', async () => {
    open({ ...OFF, part_pages: 20_000 });
    const user = userEvent.setup();

    const shown = await block();
    expect(plain(shown.textContent)).toContain('% страниц с платной ссылкой на 20 000');
    await user.click(within(shown).getByRole('button', { name: 'Запустить 24/7…' }));
    expect(plain((await screen.findByRole('dialog')).textContent)).toContain(
      'потом по 20 000 страниц каждого сайта, дальше — следующие 20 000',
    );
  });

  it('сервер старше и части не прислал — своим числом, 10 000; без права запуска — так же', async () => {
    const older: AlwaysView = { ...RUNNING };
    delete older.part_pages;
    open(older, WATCHER);

    expect(plain((await block()).textContent)).toContain(
      'меньше 0,1% страниц с платной ссылкой на 10 000',
    );
  });
});

describe('обход 24/7: переобход слабых', () => {
  it('переключатель — одним нажатием; уходит только он', async () => {
    const recorded = open(OFF, ADMIN, {
      [`POST ${URL}`]: { body: { ...OFF, recrawl_weak: true } },
    });
    const user = userEvent.setup();
    const toggle = await screen.findByRole('switch', { name: RECRAWL_LABEL });

    expect(toggle).not.toBeChecked();
    await user.click(toggle);

    await waitFor(() => expect(posted(recorded)).toEqual([{ recrawl_weak: true }]));
    await waitFor(() => expect(toggle).toBeChecked());
  });

  it('отказ — заголовком, что не переключилось, и словами сервера', async () => {
    open(OFF, ADMIN, { [`POST ${URL}`]: { status: 403, body: { detail: 'Нет права «запуск».' } } });
    const user = userEvent.setup();

    await user.click(await screen.findByRole('switch', { name: RECRAWL_LABEL }));

    expect(await screen.findByText('Нет права «запуск».')).toBeInTheDocument();
    expect(screen.getByText('Переобход слабых не переключён')).toBeInTheDocument();
  });
});

describe('обход 24/7: на телефоне', () => {
  it('ряды блока переносятся, ширина задана только полю — прокрутки вбок нет', async () => {
    // Раскладку jsdom не считает: проверяется договор разметки — ряды с кнопкой и полем
    // переносятся (`--group-wrap: wrap`), у строки итога «i» держится за слова, а слова переносятся
    // сами; фиксированная ширина — только у поля порога (5rem), уже 390 px нет ничего.
    open(OFF);

    const shown = await block();
    const field = within(shown).getByRole('textbox', { name: THRESHOLD_NAME });
    const sentence = field.closest<HTMLElement>('.mantine-Group-root');
    expect(sentence?.style.getPropertyValue('--group-wrap')).toBe('wrap');
    const head = within(shown)
      .getByRole('button', { name: 'Запустить 24/7…' })
      .closest<HTMLElement>('.mantine-Group-root');
    expect(head?.style.getPropertyValue('--group-wrap')).toBe('wrap');
    const widths = [...shown.querySelectorAll<HTMLElement>('[style*="width"]')].map(
      (node) => node.style.width,
    );
    expect(widths.filter((width) => width !== '')).toEqual(['5rem']);
  });
});

describe('обход 24/7: слова', () => {
  it('сколько получат пометку — строго меньше порога, по долям сервера', () => {
    expect(markedCount(YIELDS, 0.1)).toBe(3);
    expect(markedCount(YIELDS, 0.04)).toBe(2);
    expect(markedCount(YIELDS, 0)).toBe(0);
    expect(markedCount(YIELDS, 100)).toBe(6);
  });

  it('«получит» и «получат», «из 1 сайта» и «из 6 сайтов»; сайтов ещё нет — так и сказано', () => {
    expect(plain(markWords(YIELDS, 0.1, 10_000))).toBe(
      'при этом пороге пометку получат 3 из 6 сайтов',
    );
    expect(plain(markWords([0.01], 0.1, 10_000))).toBe(
      'при этом пороге пометку получит 1 из 1 сайта',
    );
    expect(plain(markWords([0.01, 5], 0.1, 10_000))).toBe(
      'при этом пороге пометку получит 1 из 2 сайтов',
    );
    expect(plain(markWords([], 0.1, 20_000))).toBe(
      'пометку получат сайты, прошедшие первые 20 000 страниц, — таких пока нет',
    );
  });

  it('проценты — без лишних нулей, но и не на нуле и не через порог', () => {
    expect(percentText(0.04)).toBe('0,04%');
    expect(percentText(6.5)).toBe('6,5%');
    expect(percentText(0)).toBe('0%');
    expect(percentText(0.004)).toBe('0,004%');
    // 0,0999 при пороге 0,1 сотыми было бы «0,1%» — ровно порог, а сайт под ним.
    expect(percentText(0.0999, 0.1)).toBe('0,0999%');
    expect(percentText(0.0999)).toBe('0,1%');
  });

  it('почему не идёт и когда пройдено — только когда сервер сказал', () => {
    expect(waitingWords(OFF)).toBeNull();
    expect(
      waitingWords({ ...RUNNING, waiting_for: 'юниты Ahrefs кончились — продолжится сам.' }),
    ).toBe('Сейчас не идёт: юниты Ahrefs кончились — продолжится сам.');
    expect(doneWords(OFF)).toBeNull();
  });

  it('«i» — строки сервера, пройдено и кто менял; пусто — «i» нет', () => {
    expect(alwaysHint(OFF)).toEqual([]);
    expect(alwaysHint({ ...RUNNING, set_by: null }).at(-1)).toBe(
      `Последняя смена настроек: ${formatDateTime('2026-11-17T12:40:00Z')}.`,
    );
  });

  it('заголовок отказа называет, что не вышло', () => {
    expect(changeTitle({ enabled: true })).toBe('Обход 24/7 не запущен');
    expect(changeTitle({ enabled: false })).toBe('Обход 24/7 не поставлен на паузу');
    expect(changeTitle({ threshold_pct: 1 })).toBe('Порог не сохранён');
    expect(changeTitle({ recrawl_weak: true })).toBe('Переобход слабых не переключён');
  });

  it('часть 24/7 — числом сервера; сервер старше — своим числом', () => {
    expect(partPages({ ...OFF, part_pages: 20_000 })).toBe(20_000);
    const older: AlwaysView = { ...OFF };
    delete older.part_pages;
    expect(partPages(older)).toBe(PART_PAGES);
    expect(PART_PAGES).toBe(10_000);
  });

  it('вопрос у кнопки называет части, ночь, юниты, потолок и паузу', () => {
    const said = plain(startWarning(20_000));
    expect(said).toContain('потом по 20 000 страниц каждого сайта, дальше — следующие 20 000');
    expect(said).toContain('сначала сайты, которых ещё не обходили, — до 1 000 страниц');
    expect(said).toContain('первыми сайты с большей долей платных ссылок');
    expect(said).toContain('Юниты Ahrefs и месячный потолок платного обхода действуют как сейчас');
  });

  it('блок перечитывается сам, пока 24/7 включён и не всё пройдено', () => {
    expect(watched(undefined)).toBe(false);
    expect(watched(OFF)).toBe(false);
    expect(watched(RUNNING)).toBe(true);
    expect(watched({ ...RUNNING, waiting_for: 'ночной обход — продолжится после него' })).toBe(
      true,
    );
    expect(watched({ ...RUNNING, done_at: '2026-11-20T03:15:00Z' })).toBe(false);
    expect(watched({ ...RUNNING, available: false })).toBe(false);
  });
});
