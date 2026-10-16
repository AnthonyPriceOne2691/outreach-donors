/**
 * Ключи сбора без отрисовки (`segmentKeys`): строки — как их делит сервер (Python
 * `str.splitlines`), снятые — по тексту строки после правки поля, чипы сметы — по порядку строк.
 */

import { describe, expect, it } from 'vitest';

import type { SegmentEstimate } from './segmentApi';
import { estimateChips, excludedLines, splitLines, toggled } from './segmentKeys';

describe('строки поля — как у сервера', () => {
  it('разделители Python: «\\r\\n» — один разрыв, редкие переводы строки — тоже разрывы', () => {
    // То же, что `'a\r\nb\rc\vd\fe\x1cf\x1dg\x1eh\x85i\u2028j\u2029k'.splitlines()` в Python.
    expect(splitLines('a\r\nb\rc\vd\fe\u001cf\u001dg\u001eh\u0085i\u2028j\u2029k')).toEqual([
      'a',
      'b',
      'c',
      'd',
      'e',
      'f',
      'g',
      'h',
      'i',
      'j',
      'k',
    ]);
  });

  it('пустые строки держат номера, за последним разрывом пустой строки нет', () => {
    expect(splitLines('доставка цветов\n\nцветы оптом\n')).toEqual([
      'доставка цветов',
      '',
      'цветы оптом',
    ]);
    expect(splitLines('')).toEqual([]);
    expect(splitLines('\n')).toEqual(['']);
  });
});

describe('снятые ключи после правки поля', () => {
  const text = 'zahnarzt berlin\nzahnarzt kosten\nzahnarzt notdienst';

  it('строка вставлена выше — номер снятого ищется по тексту', () => {
    const dropped = toggled(text, [], 2, false);

    expect(excludedLines(text, dropped)).toEqual([2]);
    expect(excludedLines(`zahnarzt münchen\n${text}`, dropped)).toEqual([3]);
  });

  it('строку снятого поправили или удалили — снятие пропало', () => {
    const dropped = toggled(text, [], 2, false);

    expect(excludedLines(text.replace('zahnarzt kosten', 'zahnarzt preise'), dropped)).toEqual([]);
  });

  it('тот же текст в двух строках — снятие остаётся на своей', () => {
    const twice = 'cargo ports\nfreight rates\ncargo ports';
    const dropped = toggled(twice, [], 3, false);

    expect(excludedLines(twice, dropped)).toEqual([3]);
  });

  it('галочку вернули — снятия нет', () => {
    const dropped = toggled(text, toggled(text, [], 2, false), 2, true);

    expect(excludedLines(text, dropped)).toEqual([]);
  });
});

describe('чипы сметы', () => {
  const found = {
    keywords: ['taller de bicicletas', 'reparación de bicicletas'],
    excluded: ['bicicletas eléctricas'],
    keyword_lines: {
      'reparación de bicicletas': 1,
      'bicicletas eléctricas': 2,
      'taller de bicicletas': 4,
    },
    rejected: { 'site:bicis.example.test': 'поисковый оператор' },
    notes: { 'bicicletas eléctricas': 'похож на «…»' },
  } as unknown as SegmentEstimate;

  it('по порядку строк, снятый — на своём месте и словами «снят вами», отсеянные — последними', () => {
    expect(estimateChips(found, null)).toEqual([
      {
        phrase: 'reparación de bicicletas',
        line: 1,
        state: 'taken',
        words: undefined,
      },
      {
        phrase: 'bicicletas eléctricas',
        line: 2,
        state: 'excluded',
        words: 'снят вами; похож на «…»',
      },
      { phrase: 'taller de bicicletas', line: 4, state: 'taken', words: undefined },
      { phrase: 'site:bicis.example.test', state: 'rejected', words: 'поисковый оператор' },
    ]);
  });

  it('слова пометки — как их сказал сервер, хоть две пометки через «; »', () => {
    const two = 'похож на «zahnarzt berlin» — общих слов 2 из 3; похоже на название фирмы («GmbH»)';
    const chips = estimateChips(
      {
        ...found,
        keywords: ['zahnarzt gmbh berlin'],
        excluded: ['dental gmbh'],
        keyword_lines: { 'zahnarzt gmbh berlin': 1, 'dental gmbh': 2 },
        notes: { 'zahnarzt gmbh berlin': two, 'dental gmbh': two },
        rejected: {},
      },
      null,
    );

    expect(chips.map((chip) => chip.words)).toEqual([two, `снят вами; ${two}`]);
  });

  it('пока смета пересчитывается — снятое на экране, а не прошлый ответ сервера', () => {
    const chips = estimateChips(found, new Set([4]));

    expect(chips.map((chip) => chip.state)).toEqual(['taken', 'taken', 'excluded', 'rejected']);
  });
});
