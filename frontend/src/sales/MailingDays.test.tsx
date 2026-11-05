/**
 * План и факт «Рассылки» — вкладками одного блока (`MailingDaysTabs`) и слова этапа параметром
 * (`PlanWords`): у продаж текст подсказки тот же, что был до параметра, у другого этапа — свои ящики
 * и что ждёт отправки, а разгон, окно, добивки и дни по UTC — общие правила прогноза, одним текстом.
 */

import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { renderWith } from '../test/render';
import { MailingDaysTabs } from './MailingDays';
import { FORECAST } from './mailingFixtures';

const PLAN = 'План на 7 дней';
const PAST = 'Факт за 7 дней';
const PLAN_HINT = `Как считается «${PLAN}»`;

/** Подсказка плана словами этапа — по абзацам; своё у этапа только в двух местах. */
function hint(boxes: string, supply: string): string[] {
  return [
    `Мощность ящиков — сколько первых писем могут отправить ${boxes} в этот день: ступень разгона, лимиты домена и направления; ящик, ещё не писавший, начинает разгон в первый день отправки. В день без окна получателя — ноль. Сегодня — мощность дня целиком.`,
    `Уйдёт первых — мощность, но не больше, чем есть: ${supply}. Сегодня — сколько ещё уйдёт.`,
    'Добивок — со сроком в этот день; просроченные — сегодня, срок в день без окна — в следующем дне отправки. В лимит первых писем добивки не входят.',
    'Дни — по UTC, как дневной лимит ящиков.',
  ];
}

/** Что читает человек: наводит на «i» у вкладок плана — абзацы подсказки по порядку. */
async function shownHint(): Promise<string[]> {
  await userEvent.setup().hover(screen.getByRole('button', { name: PLAN_HINT }));
  const first = await screen.findByText(/^Мощность ящиков — /);
  const paragraphs = first.parentElement;
  if (paragraphs === null) throw new Error('у абзаца подсказки нет обёртки');
  return Array.from(paragraphs.children, (paragraph) => paragraph.textContent ?? '');
}

describe('рассылка: план и факт — вкладками', () => {
  it('сначала план: сегодня, окно и семь дней; факта на экране нет', () => {
    renderWith(<MailingDaysTabs view={FORECAST} />);

    expect(screen.getByRole('radio', { name: PLAN })).toBeChecked();
    const plan = within(screen.getByRole('region', { name: PLAN }));
    expect(plan.getByText(/^Сегодня ушло первых/)).toBeInTheDocument();
    expect(plan.getByText(/^Окно получателя/)).toBeInTheDocument();
    expect(plan.getAllByRole('row')).toHaveLength(8);
    expect(screen.queryByRole('region', { name: PAST })).toBeNull();
    // Заголовка рядом с переключателем нет: то же слово дважды — лишняя строка, а блок ради краткости.
    expect(screen.queryByRole('heading')).toBeNull();
  });

  it('факт — по нажатию: его таблица вместо плана, «как считается» — своё; план возвращается', async () => {
    const user = userEvent.setup();
    renderWith(<MailingDaysTabs view={FORECAST} />);

    await user.click(screen.getByRole('radio', { name: PAST }));

    expect(screen.getByRole('radio', { name: PAST })).toBeChecked();
    const past = within(screen.getByRole('region', { name: PAST }));
    expect(past.getAllByRole('row')).toHaveLength(8);
    expect(past.queryByText(/^Сегодня ушло первых/)).toBeNull();
    expect(screen.queryByRole('region', { name: PLAN })).toBeNull();
    expect(screen.queryByRole('button', { name: PLAN_HINT })).toBeNull();
    await user.hover(screen.getByRole('button', { name: `Как считается «${PAST}»` }));
    expect(await screen.findByText(/^Сколько писем ушло на самом деле/)).toHaveTextContent(
      'Сегодня здесь нет: оно на вкладке плана.',
    );

    await user.click(screen.getByRole('radio', { name: PLAN }));

    expect(screen.getByRole('region', { name: PLAN })).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: PAST })).toBeNull();
    expect(screen.getByRole('button', { name: PLAN_HINT })).toBeInTheDocument();
  });

  it('на телефоне — столбиком во всю ширину: в ряд с «i» на 360 px подпись срезалась', () => {
    // Заглушку снимает `restoreAllMocks` после теста (`test/setup.ts`).
    vi.spyOn(window, 'matchMedia').mockImplementation(
      (query) =>
        ({
          matches: query.includes('36em'),
          media: query,
          addEventListener: () => {},
          removeEventListener: () => {},
        }) as unknown as MediaQueryList,
    );
    renderWith(<MailingDaysTabs view={FORECAST} />);

    const tabs = screen.getByRole('radiogroup', { name: 'План или факт по дням' });
    expect(tabs).toHaveAttribute('data-orientation', 'vertical');
    expect(tabs).toHaveAttribute('data-full-width');
  });

  it('клавиатура: стрелка переключает вкладку, а фокус остаётся на переключателе', async () => {
    const user = userEvent.setup();
    renderWith(<MailingDaysTabs view={FORECAST} />);

    await user.tab();
    expect(screen.getByRole('radio', { name: PLAN })).toHaveFocus();
    await user.keyboard('{ArrowRight}');

    expect(screen.getByRole('region', { name: PAST })).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: PAST })).toHaveFocus();
  });
});

describe('рассылка: подсказка плана — слова этапа', () => {
  it('без слов — продаж: подсказка та же, что была до параметра', async () => {
    renderWith(<MailingDaysTabs view={FORECAST} />);

    expect(await shownHint()).toEqual(
      hint('ящики продаж', 'сначала очередь, затем лиды, которым сборка напишет'),
    );
  });

  it('слова этапа — его ящики и что ждёт отправки; разгон, окно, добивки и дни — общие', async () => {
    renderWith(
      <MailingDaysTabs
        view={FORECAST}
        words={{ boxes: 'ящики рекламодателей', supply: 'письма в очереди рекламодателям' }}
      />,
    );

    expect(await shownHint()).toEqual(
      hint('ящики рекламодателей', 'письма в очереди рекламодателям'),
    );
  });
});
