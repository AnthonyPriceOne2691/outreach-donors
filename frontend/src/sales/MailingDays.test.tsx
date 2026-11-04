/**
 * Подсказка плана «Рассылки» — слова этапа параметром (`PlanWords`): у продаж текст тот же, что был до
 * параметра, у другого этапа — свои ящики и что ждёт отправки, а разгон, окно, добивки и дни по UTC —
 * общие правила прогноза, одним текстом.
 */

import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { renderWith } from '../test/render';
import { MailingPlan } from './MailingDays';
import { FORECAST } from './mailingFixtures';

/** Подсказка плана словами этапа — по абзацам; своё у этапа только в двух местах. */
function hint(boxes: string, supply: string): string[] {
  return [
    `Мощность ящиков — сколько первых писем могут отправить ${boxes} в этот день: ступень разгона, лимиты домена и направления; ящик, ещё не писавший, начинает разгон в первый день отправки. В день без окна получателя — ноль. Сегодня — мощность дня целиком.`,
    `Уйдёт первых — мощность, но не больше, чем есть: ${supply}. Сегодня — сколько ещё уйдёт.`,
    'Добивок — со сроком в этот день; просроченные — сегодня, срок в день без окна — в следующем дне отправки. В лимит первых писем добивки не входят.',
    'Дни — по UTC, как дневной лимит ящиков.',
  ];
}

/** Что читает человек: наводит на «i» у заголовка плана — абзацы подсказки по порядку. */
async function shownHint(): Promise<string[]> {
  await userEvent
    .setup()
    .hover(screen.getByRole('button', { name: 'Как считается «План на 7 дней»' }));
  const first = await screen.findByText(/^Мощность ящиков — /);
  const paragraphs = first.parentElement;
  if (paragraphs === null) throw new Error('у абзаца подсказки нет обёртки');
  return Array.from(paragraphs.children, (paragraph) => paragraph.textContent ?? '');
}

describe('рассылка: подсказка плана — слова этапа', () => {
  it('без слов — продаж: подсказка та же, что была до параметра', async () => {
    renderWith(<MailingPlan view={FORECAST} />);

    expect(await shownHint()).toEqual(
      hint('ящики продаж', 'сначала очередь, затем лиды, которым сборка напишет'),
    );
  });

  it('слова этапа — его ящики и что ждёт отправки; разгон, окно, добивки и дни — общие', async () => {
    renderWith(
      <MailingPlan
        view={FORECAST}
        words={{ boxes: 'ящики рекламодателей', supply: 'письма в очереди рекламодателям' }}
      />,
    );

    expect(await shownHint()).toEqual(
      hint('ящики рекламодателей', 'письма в очереди рекламодателям'),
    );
  });
});
