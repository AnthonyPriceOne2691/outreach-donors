/**
 * Строка отчёта прогона «Как судья понял описание» (судья сегмента v4): слова разбора описания — как
 * их отдал сервер (`report.scope.words`); прогон до v4 или без разбора — строки нет. Слова выдуманы.
 */

import { render, screen } from '@testing-library/react';
import { MantineProvider } from '@mantine/core';
import { describe, expect, it } from 'vitest';

import { SegmentScopeLine, scopeWords } from './SegmentScopeLine';

const WORDS = 'страна сегмента по описанию — Чили; виды сайтов — сайты самих компаний';

describe('как судья понял описание', () => {
  it('слова разбора — строкой отчёта', () => {
    render(
      <MantineProvider>
        <SegmentScopeLine report={{ scope: { words: WORDS, countries: ['CL'] } }} />
      </MantineProvider>,
    );

    expect(screen.getByText(`Как судья понял описание: ${WORDS}.`)).toBeInTheDocument();
  });

  it('сбой разбора — тоже словами сервера', () => {
    const failed = 'описание сегмента не разобрано: ответ разбора описания не разобран';

    expect(scopeWords({ scope: { failed: 'x', words: failed } })).toBe(failed);
  });

  it.each([{}, { scope: null }, { scope: { words: '  ' } }, { scope: 'не объект' }])(
    'разбора нет — строки нет: %j',
    (report) => {
      expect(scopeWords(report)).toBeNull();
    },
  );
});
