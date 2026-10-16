/**
 * Ключи сбора без отрисовки (`segmentKeys`): строки — как их делит сервер (Python
 * `str.splitlines`), снятые — по тексту строки после правки поля, чипы сметы — по порядку строк.
 */

import { describe, expect, it } from 'vitest';

import type { SegmentEstimate, SegmentRunKeywords } from './segmentApi';
import {
  EMPTY_MARK,
  estimateChips,
  excludedLines,
  keyCounts,
  LOST_MARK,
  remarkGroups,
  runHeadLine,
  runRows,
  splitLines,
  toggled,
  twinCaption,
} from './segmentKeys';

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

// --- ключи в отчёте прогона ----------------------------------------------------------------------

const FIRM_NOTE = 'похоже на название фирмы («GmbH»): выдача найдёт её реестры и карточки';

const RUN_KEYS: SegmentRunKeywords = {
  taken: [
    'zahnarzt berlin',
    'kieferorthopäde berlin',
    'zahnarzt berlin mitte',
    'praxis müller gmbh',
    'zahnarzt berlin kreuzberg',
  ],
  notes: {
    'zahnarzt berlin mitte': 'похож на «zahnarzt berlin» — общих слов 2 из 3',
    'praxis müller gmbh': FIRM_NOTE,
  },
  rejected: { 'site:praxis.example.test': 'поисковый оператор' },
  lost: ['zahnarzt berlin kreuzberg'],
  empty: ['kieferorthopäde berlin', 'zahnarzt notdienst'],
  excluded: ['zahnarzt kosten'],
  twins: {
    'zahnarzt berlin mitte': 'zahnarzt berlin',
    'zahnarzt berlin kreuzberg': 'zahnarzt berlin',
  },
  remarks: { 'praxis müller gmbh': FIRM_NOTE },
  sites: { 'zahnarzt berlin': 12, 'zahnarzt berlin mitte': 1, 'praxis müller gmbh': 2 },
  accepted: { 'zahnarzt berlin': 9, 'praxis müller gmbh': 0 },
};

describe('ключи отчёта прогона', () => {
  it('почти повторы — сразу под ключом, на который похожи; пустой не из взятых — в конце', () => {
    expect(runRows(RUN_KEYS)).toEqual([
      { phrase: 'zahnarzt berlin', twin: undefined, marks: [], sites: 12, accepted: 9 },
      {
        phrase: 'zahnarzt berlin mitte',
        twin: 'zahnarzt berlin',
        marks: [],
        sites: 1,
        accepted: 0,
      },
      {
        phrase: 'zahnarzt berlin kreuzberg',
        twin: 'zahnarzt berlin',
        marks: [LOST_MARK],
        sites: 0,
        accepted: 0,
      },
      {
        phrase: 'kieferorthopäde berlin',
        twin: undefined,
        marks: [EMPTY_MARK],
        sites: 0,
        accepted: 0,
      },
      { phrase: 'praxis müller gmbh', twin: undefined, marks: [], sites: 2, accepted: 0 },
      { phrase: 'zahnarzt notdienst', twin: undefined, marks: [EMPTY_MARK], sites: 0, accepted: 0 },
    ]);
  });

  it('ключ, на который похож повтор, не куплен (снят) — повтор стоит своей строкой', () => {
    const found = { ...RUN_KEYS, twins: { 'zahnarzt berlin mitte': 'zahnarzt kosten' } };
    expect(runRows(found).map((row) => [row.phrase, row.twin])).toEqual([
      ['zahnarzt berlin', undefined],
      ['kieferorthopäde berlin', undefined],
      ['zahnarzt berlin mitte', undefined],
      ['praxis müller gmbh', undefined],
      ['zahnarzt berlin kreuzberg', undefined],
      ['zahnarzt notdienst', undefined],
    ]);
  });

  it('прочие пометки — группами по словам; у сервера без групп — и почти повторы', () => {
    const twice = {
      ...RUN_KEYS,
      remarks: { 'praxis müller gmbh': FIRM_NOTE, 'zahnarzt berlin mitte': FIRM_NOTE },
    };
    expect(remarkGroups(twice)).toEqual([
      { words: FIRM_NOTE, phrases: ['zahnarzt berlin mitte', 'praxis müller gmbh'] },
    ]);
    const old: SegmentRunKeywords = { ...RUN_KEYS };
    delete old.remarks;
    expect(remarkGroups(old).map((group) => group.phrases)).toEqual([
      ['zahnarzt berlin mitte'],
      ['praxis müller gmbh'],
    ]);
  });

  it('шапка: взятые с пропавшими и пустыми, снятые на запуске и отсеянные', () => {
    expect(runHeadLine(RUN_KEYS)).toBe('взято 6 из 8 · снято на запуске 1 · отсеяно 1');
  });

  it('строка группы и числа ключа — словами', () => {
    expect(twinCaption('zahnarzt berlin', 1)).toBe(
      'Похож на «zahnarzt berlin»: выдача может частично совпасть.',
    );
    expect(twinCaption('zahnarzt berlin', 2)).toBe(
      'Похожи на «zahnarzt berlin»: выдача может частично совпасть.',
    );
    expect(keyCounts({ phrase: 'x', marks: [], sites: 1234, accepted: 0 })).toBe(
      'новых сайтов 1\u00a0234 · принято 0',
    );
  });
});
