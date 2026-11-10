/**
 * «Как судья понял описание» (судья сегмента v4): слова разбора описания — как их отдал сервер
 * (`report.scope.words`); прогон до v4 или без разбора — слов нет. С замечания владельца 10.11.2026
 * («пояснения — не простынёй, а свёрнуто или в «i»») удачный разбор — в «i» у заголовка прогона
 * (`SegmentRunHint`), а строкой отчёта — только сбой разбора: по нему сайты ждут человека. Слова
 * выдуманы.
 */

import { render, screen } from '@testing-library/react';
import { MantineProvider } from '@mantine/core';
import { describe, expect, it } from 'vitest';

import { scopeFailed, SegmentScopeLine, scopeWords } from './SegmentScopeLine';

const WORDS = 'страна сегмента по описанию — Чили; виды сайтов — сайты самих компаний';
const FAILED = 'описание сегмента не разобрано: ответ разбора описания не разобран';

function line(report: Record<string, unknown>) {
  return render(
    <MantineProvider>
      <SegmentScopeLine report={report} />
    </MantineProvider>,
  );
}

describe('как судья понял описание', () => {
  it('удачный разбор — не строкой отчёта: он в «i» у заголовка прогона', () => {
    const { container } = line({ scope: { words: WORDS, countries: ['CL'] } });

    expect(scopeWords({ scope: { words: WORDS } })).toBe(WORDS);
    expect(scopeFailed({ scope: { words: WORDS } })).toBe(false);
    expect(container.querySelector('p')).toBeNull();
  });

  it('сбой разбора — строкой отчёта словами сервера, с заглавной и точкой', () => {
    line({ scope: { failed: 'ответ разбора описания не разобран', words: FAILED } });

    expect(screen.getByText(`О${FAILED.slice(1)}.`)).toBeInTheDocument();
  });

  it.each([{}, { scope: null }, { scope: { words: '  ' } }, { scope: 'не объект' }])(
    'разбора нет — слов нет и строки нет: %j',
    (report) => {
      const { container } = line(report);

      expect(scopeWords(report)).toBeNull();
      expect(scopeFailed(report)).toBe(false);
      expect(container.querySelector('p')).toBeNull();
    },
  );
});
