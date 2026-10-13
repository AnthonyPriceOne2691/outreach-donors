/**
 * Окно «Текст первого письма» — одна прокрутка (замечание Anthony 12.10.2026: «двойная
 * прокрутка… прям косяк»). Было три вложенных: окно Mantine, поля под ним и поле длиннее
 * восьми строк. Теперь заголовок и кнопки стоят, а поля прокручиваются одной серединой.
 * Пояснение от md и шире стоит над ней (правило 10.10.2026), уже — идёт в ней первым
 * абзацем. Редактор один на всех адресатов «Писем» — донорам, рекламодателям и бизнесам
 * ниши, — проверяется у каждого.
 *
 * Раскладку jsdom не считает: проверяется, что прокрутка в окне одна, что в ней и что вне её,
 * и договор с правилами `glass.css`. Сколько прокруток на самом деле и видны ли кнопки —
 * замер в браузере (1440 × 900, 1280 × 720, 390 × 844, 375 × 667, 844 × 390 — по одной).
 */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it } from 'vitest';

import type { LetterDraftView } from '../api/types';
import { renderWith } from '../test/render';
import { HINT_FIXED, LetterDraftEditor, draftOf } from './LetterDraftEditor';
import type { LetterTarget } from './targets';

/** Зона «Вопросы» — длиннее восьми строк: на таких поле прокручивалось само. */
const FALLBACK: LetterDraftView = {
  subject: 'Guest article on {{host}}',
  zones: [
    { name: 'greeting', kind: 'rewrite', title: 'Приветствие', text: 'Hi,' },
    {
      name: 'ask',
      kind: 'rewrite',
      title: 'Вопросы',
      text: ['Could you let me know:', ...Array.from({ length: 10 }, (_, i) => `${i + 1}. ?`)].join(
        '\n',
      ),
    },
    { name: 'signature', kind: 'fixed', title: 'Подпись', text: 'Best regards,\n{{sender_name}}' },
  ],
};

const TARGETS: LetterTarget[] = ['donors', 'advertisers', 'niche'];

/** Экран, где совпадает только этот запрос, — как у рамы (`Shell.test`). */
function screenWhere(matched: string) {
  window.matchMedia = (query: string) =>
    ({
      matches: query === matched,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }) as MediaQueryList;
}

async function openEditor(target: LetterTarget): Promise<HTMLElement> {
  renderWith(
    <LetterDraftEditor
      fallback={FALLBACK}
      value={draftOf(FALLBACK)}
      onChange={() => undefined}
      target={target}
    />,
  );
  await userEvent.setup().click(screen.getByRole('button', { name: 'Текст первого письма' }));
  return screen.findByRole('dialog', { name: 'Текст первого письма' });
}

/** Середина окна — единственная прокрутка, в ней все поля, кнопки — вне её. */
function onlyScroll(dialog: HTMLElement): HTMLElement {
  const scrollers = dialog.querySelectorAll<HTMLElement>('.oneScrollArea');
  expect(scrollers).toHaveLength(1);
  // Прежняя середина — `ScrollArea` Mantine поверх прокрутки самого окна.
  expect(dialog.querySelector('.mantine-ScrollArea-root')).toBeNull();
  expect(dialog).toHaveClass('oneScroll');
  const middle = scrollers[0] as HTMLElement;
  for (const field of ['Тема', 'Приветствие', 'Вопросы', 'Подпись']) {
    expect(within(middle).getByLabelText(field)).toBeInTheDocument();
  }
  for (const button of ['Вернуть исходный текст', 'Готово']) {
    expect(within(dialog).getByRole('button', { name: button })).toBeInTheDocument();
    expect(within(middle).queryByRole('button', { name: button })).toBeNull();
  }
  return middle;
}

const HINT = /Текст закрепляется за рассылкой/;

describe('окно «Текст первого письма» — одна прокрутка', () => {
  const before = Object.getOwnPropertyDescriptor(window, 'matchMedia');
  // Не своё свойство окна — подмену удалить, иначе «экран от md» дожил бы до конца файла.
  afterEach(() => {
    if (before !== undefined) Object.defineProperty(window, 'matchMedia', before);
    else Reflect.deleteProperty(window, 'matchMedia');
  });

  it.each(TARGETS)(
    'узкий экран, адресат %s: пояснение и поля — в одной прокрутке, кнопки — вне её',
    async (target) => {
      const dialog = await openEditor(target);

      const middle = onlyScroll(dialog);
      expect(within(middle).getByText(HINT)).toBeInTheDocument();
    },
  );

  it.each(TARGETS)(
    'от md и шире, адресат %s: пояснение стоит над прокруткой, поля — в ней',
    async (target) => {
      screenWhere(HINT_FIXED);
      const dialog = await openEditor(target);

      const middle = onlyScroll(dialog);
      const hint = within(dialog).getByText(HINT);
      expect(middle).not.toContainElement(hint);
      expect(hint.compareDocumentPosition(middle) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    },
  );

  it('правила рамки: окно — столбиком, середина забирает остаток и прокручивается сама', () => {
    const glass = readFileSync(resolve(process.cwd(), 'src/styles/glass.css'), 'utf8');

    expect(glass).toMatch(
      /\.mantine-Modal-content\.oneScroll \{\s*display: flex;\s*flex-direction: column;/,
    );
    expect(glass).toMatch(
      /\.oneScroll > \.mantine-Modal-body \{[^}]*flex: 1 1 auto;[^}]*min-height: 0;/,
    );
    expect(glass).toMatch(/\.oneScrollArea \{[^}]*min-height: 0;[^}]*overflow-y: auto;/);
  });
});
