/**
 * Доля ложных рекламодателей — строкой фактов в шапке очереди проверки.
 *
 * Сверка ТЗ 11.10.2026: требование — «не более 10% ложных рекламодателей», а экран
 * существовал ради этого числа и его не показывал (`okf/link-scoring.md`). Ложный —
 * «куплено» скоринга, которому человек сказал «не пишем».
 *
 * **Доля — по случайной выборке** (решение Anthony 12.10.2026, `crawl/sample.py`):
 * очередь идёт от сильных к слабым, и по всем решённым доля занижена. Выборка —
 * около пятой части доменов «куплено», постоянная; очередь ставит их среди остальных,
 * и человек их не отличает — экран не говорит, какие строки в выборке.
 *
 * **Вывод — с выборки судьи Этапа 1** (`min_decisions` от сервера): до неё — число
 * и «мало решений для вывода (12 из 200)», значок серый. Допуск — тоже от сервера:
 * своя копия десяти процентов разошлась бы с его проверкой при первой правке.
 *
 * **Строкой, а не плитками** (как замеры судьи на «Отборе», 10.10.2026): действуют здесь
 * по одному числу — значок с долей: мята — в допуске, роза — за ним, серый — вывода нет.
 */

import { Badge, Group, Text } from '@mantine/core';

import type { ScoringCheck } from '../api/advertisers';
import { InfoHint } from '../components/InfoHint';
import { formatNumber, formatPercent, plural } from '../format';

const ABOUT =
  'Ложный рекламодатель — «куплено» скоринга, которому человек сказал «Не пишем»: перевод ' +
  'берёт «куплено» к письму сам, и ложный среди них — оффер тому, кто ничего не покупал. ' +
  'Доля — по случайной выборке: около пятой части доменов «куплено», постоянной. Очередь ' +
  '«куплено» ставит их среди остальных, и решать их — как любую строку. Вывод «в допуске» ' +
  'или «нет» — с той же выборки решений, что у точности судьи Этапа 1. Спорные в долю не ' +
  'входят. Балл решением не переписывается.';

function colorOf(within: boolean | null): string {
  if (within === null) return 'gray';
  return within ? 'green' : 'red';
}

/** Что за долей — словами: сколько решений выборки, хватает ли их для вывода, допуск ТЗ. */
export function falseShareLine(check: ScoringCheck): string {
  const { sample } = check;
  const limit = `допуск ТЗ — не больше ${formatPercent(check.false_limit)}`;
  if (check.false_share === null) {
    const why =
      sample.total === 0 ? 'в случайной выборке «куплено» пока нет' : 'по выборке решений пока нет';
    return `мерить нечем: ${why}; ${limit}`;
  }
  const decided = `${formatNumber(sample.decided)} ${plural(sample.decided, 'решение', 'решения', 'решений')}`;
  const verdict =
    check.within_limit === null
      ? ` — мало решений для вывода (${formatNumber(sample.decided)} из ${formatNumber(check.min_decisions)})`
      : '';
  return `по случайной выборке: ${decided}, «не пишем» ${formatNumber(sample.skip)}${verdict}; ${limit}`;
}

export function FalseShare({ check }: { check: ScoringCheck }) {
  return (
    <Group gap="xs" align="center">
      <Text size="sm">Ложных в «куплено»:</Text>
      <Badge variant="light" color={colorOf(check.within_limit)}>
        {formatPercent(check.false_share)}
      </Badge>
      <Text size="sm" c="dimmed">
        {falseShareLine(check)}
      </Text>
      <InfoHint name="Как считается доля ложных" width={360}>
        {ABOUT}
      </InfoHint>
    </Group>
  );
}
